"""主游戏循环"""

import json
import sys
import subprocess

from openai import OpenAI

from .config import PROJECT_ROOT, SAVEFILE, API_KEY, BASE_URL, MODEL_FLASH, MODEL_PRO, MAX_TOOL_ROUNDS
from .persistence import load_system_prompt, save_game, load_game
from .tools import TOOLS, COMPLEX_FUNCTIONS, tool_category, needs_pro_model, execute_function, dice_summary
from .llm import stream_llm, tension, glm_quick_summary


def game_loop():
    system_prompt = load_system_prompt()

    if not API_KEY:
        print("错误: 请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    messages = [{"role": "system", "content": system_prompt}]
    messages.append({
        "role": "user",
        "content": (
            "（游戏开始。请调用 read_file 读取以下文件来初始化："
            "rules/rule_schema.json、rules/rule_config.json、"
            "mod/mansion_of_madness/world_state.json。"
            "然后调用 state_clues 确认已知线索。"
            "最后描述开场场景并提供选项。）"
        )
    })

    print("=" * 55)
    print("  🎲  TRPG Agent 内核 — 疯狂宅邸")
    print("  /quit 退出  /save 存档  /load 读档  /state 状态")
    print("=" * 55)

    need_gm_turn = True
    current_model = MODEL_FLASH

    # 检测已有存档
    if SAVEFILE.exists():
        save_data = json.loads(SAVEFILE.read_text(encoding="utf-8"))
        msg_count = save_data.get("message_count", 0)
        if msg_count > 0:
            print(f"\n📜 发现存档 ({msg_count} 条消息)，输入 /load 恢复进度。\n")

    while True:
        if need_gm_turn:
            tool_round = 0
            narrative = ""

            while tool_round <= MAX_TOOL_ROUNDS:
                if tool_round == 0:
                    print()
                text, tool_calls = stream_llm(client, messages, current_model, TOOLS)

                if not text and not tool_calls:
                    break

                if not tool_calls:
                    narrative = text
                    break

                # Pro 切换
                if current_model == MODEL_FLASH and needs_pro_model(tool_calls):
                    current_model = MODEL_PRO
                    cat = "dice"
                    for tc in tool_calls:
                        if tc["function"]["name"].startswith("sanity"):
                            cat = "sanity"; break
                        elif tc["function"]["name"] in ("apply_damage", "apply_heal"):
                            cat = "combat"; break
                    print(f"  {tension(cat)}")
                    if messages and messages[-1]["role"] == "assistant":
                        messages.pop()
                    continue

                # 沉浸式提示
                complex_hit = any(tc["function"]["name"] in COMPLEX_FUNCTIONS for tc in tool_calls)
                if complex_hit and tool_round == 0:
                    cat = "dice"
                    for tc in tool_calls:
                        n = tc["function"]["name"]
                        if n.startswith("sanity"):
                            cat = "sanity"; break
                        elif n in ("apply_damage", "apply_heal"):
                            cat = "combat"; break
                    print(f"  {tension(cat)}")

                if text:
                    narrative += text + "\n\n"

                # 记录 assistant 消息
                assistant_msg: dict = {"role": "assistant", "content": text}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)

                # 执行工具
                tool_outputs = []
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    output = execute_function(name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": output
                    })

                    if name in ("dice_roll", "dice_roll_advantage", "dice_roll_disadvantage"):
                        summary = dice_summary(output)
                        if summary:
                            print(f"  {summary}")

                    if name in COMPLEX_FUNCTIONS:
                        tool_outputs.append((name, output))

                # GLM 快速摘要
                if tool_outputs:
                    quick = glm_quick_summary(tool_outputs, text or narrative)
                    if quick:
                        print(f"  {quick}")
                        print()

                tool_round += 1

            if narrative.strip():
                messages.append({"role": "assistant", "content": narrative.strip()})
                print()
                print()
            else:
                print("\n（守秘人陷入了沉思……请稍后再试。）\n")

        # --- 玩家输入 ---
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n游戏结束。")
            break

        if not user_input:
            need_gm_turn = False
            continue

        if user_input.lower() in ["/quit", "/exit", "/q"]:
            print("正在保存……")
            save_game(messages)
            print("游戏结束。")
            break

        if user_input.lower() == "/state":
            print()
            subprocess.run(["python3", "tools/state_manager.py", "get", "pc"], cwd=PROJECT_ROOT)
            subprocess.run(["python3", "tools/state_manager.py", "clues"], cwd=PROJECT_ROOT)
            print()
            need_gm_turn = False
            continue

        if user_input.lower() == "/help":
            print("""
命令:
  /quit, /exit, /q  退出游戏（自动存档）
  /save             手动存档
  /load             读取存档
  /state            查看 PC 状态和已发现线索
  /help             显示此帮助

直接输入你角色的行动即可。""")
            need_gm_turn = False
            continue

        if user_input.lower() == "/save":
            if save_game(messages):
                print("存档成功。")
            need_gm_turn = False
            continue

        if user_input.lower() == "/load":
            loaded = load_game()
            if loaded is None:
                print("未找到存档。")
            else:
                system_msg = messages[0]
                messages = [system_msg] + loaded[1:]
                print(f"读档成功，恢复了 {len(loaded) - 1} 条消息。")
                need_gm_turn = True
            continue

        messages.append({"role": "user", "content": user_input})
        current_model = MODEL_FLASH
        need_gm_turn = True
