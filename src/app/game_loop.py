"""Terminal adapter for the transactional engine used by the desktop UI."""

import os
import subprocess
import sys

from src.ai.model.model_streamer import sanitize_visible_narrative
from src.ai.model.route_service import LocalRouteResolver, apply_pending_routes
from src.app.config import API_KEY, AUTO_SAVE_SLOT, PROJECT_ROOT
from src.app.engine import EngineCallbacks, GameEngine
from src.app.logger import error as log_error
from src.app.runtime import RuntimeContext
from src.gameplay.scene_speaker_presence import speaker_display_name
from src.gameplay.speaker_parser import parse_segments
from src.storage.persistence import _next_slot

HELP = """命令：
  /new                 开始新游戏（保留已有存档）
  /retry               重试刚才失败的开场或行动
  /saves               列出存档
  /load [slot_001]     读取指定或最新存档，不调用模型
  /save                保存到自动槽位
  /save-new            保存到新槽位
  /state               查看角色与线索
  /quit                保存已开始的游戏并退出
"""


def game_loop() -> int:
    context = RuntimeContext.from_env()
    engine = GameEngine(context)
    engine.route_resolver = LocalRouteResolver(context.runtime_root / "model_settings.local.json")
    try:
        settings = engine.route_resolver()
        if not API_KEY and any(binding.mode == "default" for binding in (settings.narrative, settings.judgement)):
            print("错误：请先用 python3 start.py --config 配置默认 Key，或在桌面模型设置中配置叙事和裁决服务。")
            return 1
        apply_pending_routes(engine)
    except Exception as exc:
        log_error(f"CLI模型配置不可用 | {type(exc).__name__}")
        print("模型配置无法读取，请检查本地模型设置后重试。")
        return 1

    speaker = None

    def on_narrative(text: str, npc_id: str | None = None):
        nonlocal speaker
        if speaker != npc_id:
            name = speaker_display_name(context.world_store.load(), npc_id) if npc_id else "守秘人"
            print(f"\n\n{name or '人物'}：", end="", flush=True)
            speaker = npc_id
        print(text, end="", flush=True)

    def on_suggest(info: dict) -> bool:
        print(f"\n  ⚡ 检定提议：{info.get('description', '')}")
        print(f"     【{info.get('skill', '')}】难度：{info.get('dc_label', '')}")
        try:
            return input("  → 确定尝试吗？(y/n) ").strip().lower() in ("y", "yes", "是")
        except (EOFError, KeyboardInterrupt):
            return False

    def on_decision(info: dict) -> str | None:
        print(f"\n  {info.get('title', '需要你做出决定')}")
        if info.get("description"):
            print(f"  {info['description']}")
        options = info.get("options", [])
        for index, option in enumerate(options, 1):
            print(f"    {index}. {option.get('label', option.get('id', ''))}")
        try:
            selected = int(input("  → 请选择: ").strip()) - 1
        except (ValueError, EOFError, KeyboardInterrupt):
            return info.get("default_option")
        return options[selected].get("id") if 0 <= selected < len(options) else info.get("default_option")

    def on_game_over(ending_type: str, title: str, summary: str):
        print(f"\n{'=' * 50}\n  {title}\n{'=' * 50}\n{summary}\n")

    engine.cb = EngineCallbacks(
        on_narrative=on_narrative,
        on_tension=lambda text, cat: print(f"\n  {text}"),
        on_dice=lambda summary, roll_data=None: print(f"\n  {summary}"),
        on_glm_summary=lambda text: print(f"\n  {text}"),
        on_suggest=on_suggest,
        on_decision=on_decision,
        on_game_over=on_game_over,
        on_error=lambda message: print(f"\n（{message}）"),
        on_phase=lambda phase, label: print(f"\n[{label}]", flush=True),
    )
    engine.prepare_session()
    print("=" * 55)
    print("  🎲  TRPG Game 内核")
    print(f"  叙事：{engine.narrative_model} / 裁决：{engine.judgement_model}")
    print("  /new 开始  /load 读档  /retry 重试  /help 帮助  /quit 退出")
    print("=" * 55)
    saves = engine.list_saves()
    started = opening_ready = failed = False
    failed_action = None

    def run_turn(action=None) -> bool:
        nonlocal failed, failed_action, speaker
        speaker = None
        print("\n[守秘人正在生成……]", flush=True)
        try:
            engine.handle_action(action)
        except KeyboardInterrupt:
            engine.cancel_active_turn()
            engine.finish_turn_record(status="cancelled", error="终端玩家取消本轮")
            engine.clear_turn_cancellation()
            print("\n本轮已取消，未提交的结果不会保存。")
        except Exception as exc:
            log_error(f"CLI回合失败 | world={context.world_id} | {type(exc).__name__}")
            print("\n本轮未完成；已提交的进度保留。输入 /retry 重试，或 /load 读取存档。")
        else:
            failed = False
            print("\n")
            return True
        failed, failed_action = True, action
        return False

    def show_loaded_reply():
        for message in reversed(engine.messages):
            if message.get("role") != "assistant" or message.get("tool_calls"):
                continue
            text = sanitize_visible_narrative(str(message.get("content") or ""))
            segments, _ = parse_segments(text, is_valid_npc=engine.is_valid_npc_id)
            for segment in segments:
                name = speaker_display_name(context.world_store.load(), segment.npc_id) if segment.npc_id else "守秘人"
                print(f"\n{name or '人物'}：\n{segment.text}")
            break

    if saves:
        print(f"\n📜 发现存档（{len(saves)} 个）。输入 /load 继续，或 /new 开始新游戏。")
    else:
        engine.reset()
        started = True
        opening_ready = run_turn()

    while True:
        exit_on_failure = False
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "/quit"
            exit_on_failure = True
        if not user_input:
            continue
        command, _, argument = user_input.partition(" ")
        command = command.lower()
        try:
            if command in {"/quit", "/exit", "/q"}:
                if started and opening_ready:
                    print("正在保存……")
                    engine.save(AUTO_SAVE_SLOT)
                print("游戏结束。")
                return 0
            if command == "/help":
                print(HELP)
            elif command == "/saves":
                for save in engine.list_saves():
                    print(f"  {save['id']}  {save.get('scene_name', '?')}  {save.get('message_count', 0)} 条消息")
            elif command == "/new":
                engine.reset()
                started = True
                opening_ready = run_turn()
            elif command == "/load":
                count = engine.load(argument.strip() or None)
                if count is None:
                    print("未找到存档。")
                else:
                    started = opening_ready = True
                    failed = False
                    print(f"读档成功，恢复了 {count} 条消息（世界状态已恢复）。")
                    show_loaded_reply()
            elif command == "/retry":
                if not failed:
                    print("没有需要重试的失败回合。")
                elif run_turn(failed_action):
                    opening_ready = True
            elif command == "/state":
                env = {**os.environ, **context.child_process_env()}
                for args in (("get", "pc"), ("clues",)):
                    subprocess.run([sys.executable, "tools/state_manager.py", *args], cwd=PROJECT_ROOT, env=env)
            elif command in {"/save", "/save-new"}:
                if not opening_ready:
                    print("尚无完整开场或已读取存档；请先 /new、/retry 或 /load。")
                    continue
                slot = _next_slot(context) if command == "/save-new" else AUTO_SAVE_SLOT
                print(f"已保存到 {engine.save(slot)}")
            elif command.startswith("/"):
                print("未知命令。输入 /help 查看可用命令。")
            elif not started:
                print("请先输入 /load 读取存档，或 /new 开始新游戏。")
            elif not opening_ready:
                print("开场尚未完成，请输入 /retry 重试开场，或 /load 读取存档。")
            else:
                run_turn(user_input)
        except KeyboardInterrupt:
            print("\n操作已取消。")
        except Exception as exc:
            log_error(f"CLI命令失败 | {type(exc).__name__}")
            print("操作未完成，请重试；可用 /help 查看命令。")
            if exit_on_failure:
                return 1
