"""终端版游戏循环 —— 使用 GameEngine + print/input 回调"""

import sys
import subprocess

from .config import PROJECT_ROOT
from .engine import GameEngine, EngineCallbacks


def game_loop():
    engine = GameEngine()

    # ---- 终端回调 ----
    def on_narrative(text: str):
        print(text, end="", flush=True)

    def on_tension(text: str, cat: str):
        print(f"  {text}")

    def on_dice(summary: str):
        print(f"  {summary}")

    def on_glm_summary(text: str):
        print(f"  {text}")
        print()

    def on_suggest(info: dict) -> bool:
        print()
        print(f"  ⚡ 检定提议：{info['description']}")
        print(f"     【{info['skill']}】（{info['attribute']}）— 难度：{info['dc_label']}（DC {info['dc']}）")
        try:
            answer = input("  → 确定尝试吗？(y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        return answer in ("y", "yes", "是")

    def on_done():
        pass

    def on_game_over(ending_type: str, title: str, summary: str):
        print(f"\n{'='*50}")
        print(f"  {title}")
        print(f"{'='*50}")
        print(f"\n{summary}\n")
        if ending_type == "good":
            print("🏆 真相大白 —— 你成功揭开了宅邸的秘密。")
        elif ending_type == "bad":
            print("💀 黑暗吞噬了你 —— 你的故事到此为止。")
        else:
            print("🌫 你离开了宅邸 —— 有些秘密或许永远不该被揭开。")
        print()

    def on_error(msg: str):
        print(f"\n（{msg}）\n")

    engine.cb = EngineCallbacks(
        on_narrative=on_narrative,
        on_tension=on_tension,
        on_dice=on_dice,
        on_glm_summary=on_glm_summary,
        on_suggest=on_suggest,
        on_done=on_done,
        on_error=on_error,
    )

    # ---- 初始化 ----
    if not engine.client.api_key:
        print("错误: 请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    engine.reset()

    print("=" * 55)
    print("  🎲  TRPG Agent 内核 — 疯狂宅邸")
    print("  /quit 退出  /save 存档  /load 读档  /state 状态")
    print("=" * 55)

    saves = engine.list_saves()
    if saves:
        latest = saves[0]
        print(f"\n📜 发现存档 ({len(saves)} 个)，最新: {latest.get('scene_name', '?')} ({latest.get('message_count', 0)} 条消息)")
        print("   输入 /load 恢复进度，或直接开始新游戏。\n")

    need_gm_turn = True

    while True:
        if need_gm_turn:
            print()  # 分隔行
            engine.handle_action()
            print()
            print()
            need_gm_turn = False

        # ---- 玩家输入 ----
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n游戏结束。")
            break

        if not user_input:
            continue

        if user_input.lower() in ["/quit", "/exit", "/q"]:
            print("正在保存……")
            engine.save()
            print("游戏结束。")
            break

        if user_input.lower() == "/state":
            print()
            subprocess.run([sys.executable, "tools/state_manager.py", "get", "pc"], cwd=PROJECT_ROOT)
            subprocess.run([sys.executable, "tools/state_manager.py", "clues"], cwd=PROJECT_ROOT)
            print()
            continue

        if user_input.lower() == "/help":
            print("\n命令:\n  /quit 退出  /save 存档  /load 读档  /state 状态\n")
            continue

        if user_input.lower() == "/save":
            sid = engine.save()  # 自动存档到 slot_000
            print(f"已保存到 {sid}")
            continue

        if user_input.lower() == "/save-new":
            sid = engine.save(slot_id=None)  # 新槽位
            print(f"已保存到 {sid}")
            continue

        if user_input.lower() == "/load":
            count = engine.load()  # 加载最新存档
            if count is None:
                print("未找到存档。")
            else:
                print(f"读档成功，恢复了 {count} 条消息（世界状态已恢复）。")
                need_gm_turn = True
            continue

        engine.handle_action(user_input)
