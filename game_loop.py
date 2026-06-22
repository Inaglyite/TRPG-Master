#!/usr/bin/env python3
"""TRPG Agent 主循环 —— 原生 Function Calling + 双模型自动路由

- deepseek-v4-flash: 默认模型，处理叙事、对话、场景描述
- deepseek-v4-pro:   复杂判定（技能检定、战斗、理智）时自动切换
"""

import os
import sys
import json
import subprocess
from pathlib import Path

from openai import OpenAI

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = PROJECT_ROOT / "skills"
SAVEFILE = PROJECT_ROOT / "mod" / "mansion_of_madness" / "savegame.json"

# ---------------------------------------------------------------------------
# API 配置
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL_FLASH = os.environ.get("TRPG_FLASH_MODEL", "deepseek-v4-flash")
MODEL_PRO = os.environ.get("TRPG_PRO_MODEL", "deepseek-v4-pro")

# ---------------------------------------------------------------------------
# Skill 加载
# ---------------------------------------------------------------------------
SKILL_LOAD_ORDER = [
    "no_spoiler.skill",
    "narrative.skill",
    "skill_check.skill",
    "combat.skill",
    "trpg_master.skill",
]


def load_system_prompt() -> str:
    parts = []
    for name in SKILL_LOAD_ORDER:
        path = SKILLS_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 定义（OpenAI Function Calling Schema）
# ---------------------------------------------------------------------------

TOOLS = [
    # ---- 骰子 ----
    {
        "type": "function",
        "function": {
            "name": "dice_roll",
            "description": "掷骰子。用于技能/属性检定、先攻、随机事件。每次检定必须掷骰，不得编造结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": "骰子规格，如 d20, d100, 2d6, 3d8+2"
                    }
                },
                "required": ["spec"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dice_roll_advantage",
            "description": "d20 优势掷骰（两骰取高）。用于有利情境。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dice_roll_disadvantage",
            "description": "d20 劣势掷骰（两骰取低）。用于不利情境。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # ---- 状态管理 ----
    {
        "type": "function",
        "function": {
            "name": "state_get",
            "description": "读取世界状态中的指定字段。用于获取 PC/NPC 属性、当前场景、标志等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "JSON 路径，如 pc.hp, npcs.0.name, current_scene.id"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_set",
            "description": "修改世界状态中的指定字段并保存。用于更新 HP、属性、场景等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "JSON 路径"
                    },
                    "value": {
                        "type": "string",
                        "description": "新值（JSON 格式字符串，如 10 或 \"大厅\"）"
                    }
                },
                "required": ["path", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_npcs",
            "description": "列出所有 NPC 及其 visible_tags。用于确认当前可被玩家看到的 NPC 信息。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_clues",
            "description": "列出已发现的所有线索。每次生成叙事前应调用此函数确认已知线索边界。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_add_clue",
            "description": "记录新发现的线索。只有在检定成功或玩家确实发现了新信息时才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "线索文本描述"
                    }
                },
                "required": ["text"]
            }
        }
    },
    # ---- 伤害与治疗 ----
    {
        "type": "function",
        "function": {
            "name": "apply_damage",
            "description": "对目标造成伤害。用于战斗、陷阱、环境危害等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "目标路径，如 pc, npcs.0"
                    },
                    "amount": {
                        "type": "integer",
                        "description": "伤害数值"
                    },
                    "damage_type": {
                        "type": "string",
                        "enum": ["物理", "火焰", "冰冻", "精神"],
                        "description": "伤害类型，默认物理"
                    }
                },
                "required": ["target", "amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_heal",
            "description": "治疗目标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "目标路径"
                    },
                    "amount": {
                        "type": "integer",
                        "description": "治疗量"
                    }
                },
                "required": ["target", "amount"]
            }
        }
    },
    # ---- 理智值 ----
    {
        "type": "function",
        "function": {
            "name": "sanity_loss",
            "description": "对 PC 施加理智损失。用于目睹恐怖事件、阅读禁忌文本等情境。",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["minor", "moderate", "major", "catastrophic"],
                        "description": "损失严重度"
                    }
                },
                "required": ["severity"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sanity_restore",
            "description": "恢复 PC 理智值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "integer",
                        "description": "恢复量"
                    }
                },
                "required": ["amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sanity_check",
            "description": "查看 PC 当前理智状态。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # ---- 文件读取 ----
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目中的文件内容。用于加载规则文件、世界状态等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径，如 rules/rule_schema.json"
                    }
                },
                "required": ["path"]
            }
        }
    }
]

# ---------------------------------------------------------------------------
# Function → CLI 映射
# ---------------------------------------------------------------------------

def _run_cli(cmd: str) -> str:
    """执行 CLI 命令并返回 stdout（出错返回错误信息）"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=PROJECT_ROOT
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip()
            return f"[错误] {err}" if err else f"[返回码 {result.returncode}] {output}"
        return output if output else "(空)"
    except subprocess.TimeoutExpired:
        return "[超时] 命令执行超过 30 秒"
    except Exception as e:
        return f"[异常] {e}"


def execute_function(name: str, args: dict) -> str:
    """将 function call 映射到 CLI 命令并执行"""
    safe = lambda s: json.dumps(str(s))  # JSON 安全字符串

    if name == "dice_roll":
        spec = args.get("spec", "d20")
        return _run_cli(f"python3 tools/dice.py {spec}")

    elif name == "dice_roll_advantage":
        return _run_cli("python3 tools/dice.py d20 adv")

    elif name == "dice_roll_disadvantage":
        return _run_cli("python3 tools/dice.py d20 dis")

    elif name == "state_get":
        path = args.get("path", "pc.hp")
        return _run_cli(f"python3 tools/state_manager.py get {path}")

    elif name == "state_set":
        path = args.get("path", "")
        value = safe(args.get("value", ""))
        return _run_cli(f"python3 tools/state_manager.py set {path} {value}")

    elif name == "state_npcs":
        return _run_cli("python3 tools/state_manager.py npcs")

    elif name == "state_clues":
        return _run_cli("python3 tools/state_manager.py clues")

    elif name == "state_add_clue":
        text = safe(args.get("text", ""))
        return _run_cli(f"python3 tools/state_manager.py add-clue {text}")

    elif name == "apply_damage":
        target = args.get("target", "pc")
        amount = args.get("amount", 0)
        dtype = args.get("damage_type", "物理")
        return _run_cli(f"python3 tools/damage.py damage {target} {amount} {dtype}")

    elif name == "apply_heal":
        target = args.get("target", "pc")
        amount = args.get("amount", 0)
        return _run_cli(f"python3 tools/damage.py heal {target} {amount}")

    elif name == "sanity_loss":
        severity = args.get("severity", "moderate")
        return _run_cli(f"python3 tools/sanity.py loss {severity}")

    elif name == "sanity_restore":
        amount = args.get("amount", 0)
        return _run_cli(f"python3 tools/sanity.py restore {amount}")

    elif name == "sanity_check":
        return _run_cli("python3 tools/sanity.py check")

    elif name == "read_file":
        path = args.get("path", "")
        # 安全检查：只允许读取项目内的文件
        full_path = (PROJECT_ROOT / path).resolve()
        if not str(full_path).startswith(str(PROJECT_ROOT)):
            return "[错误] 不允许读取项目外的文件"
        if not full_path.exists():
            return f"[错误] 文件不存在: {path}"
        return full_path.read_text(encoding="utf-8")

    else:
        return f"[错误] 未知函数: {name}"


# ---------------------------------------------------------------------------
# 复杂性检测：判断是否需要切换到 Pro 模型
# ---------------------------------------------------------------------------

COMPLEX_FUNCTIONS = {
    "dice_roll", "dice_roll_advantage", "dice_roll_disadvantage",
    "apply_damage", "apply_heal",
    "sanity_loss", "sanity_restore", "sanity_check",
}


def needs_pro_model(tool_calls: list) -> bool:
    """如果涉及技能检定、战斗、理智判定，切换到 Pro 模型"""
    for tc in tool_calls:
        if tc.function.name in COMPLEX_FUNCTIONS:
            return True
    return False


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def call_llm(client: OpenAI, messages: list, model: str,
             tools: list | None = None) -> dict:
    """调用 LLM，返回完整 completion 对象或 None"""
    kwargs = dict(
        model=model,
        messages=messages,
        temperature=0.8,
        max_tokens=2048,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    try:
        return client.chat.completions.create(**kwargs)
    except Exception as e:
        print(f"\n[API 错误] {e}")
        return None


# ---------------------------------------------------------------------------
# 存档 / 读档
# ---------------------------------------------------------------------------

def save_game(messages: list) -> bool:
    """保存对话历史到磁盘。返回 True 表示成功。"""
    try:
        serializable = []
        for m in messages:
            entry = {"role": m["role"], "content": m.get("content", "")}
            if "tool_calls" in m:
                entry["tool_calls"] = m["tool_calls"]
            if "tool_call_id" in m:
                entry["tool_call_id"] = m["tool_call_id"]
            serializable.append(entry)

        data = {
            "version": 2,
            "messages": serializable,
            "message_count": len(serializable)
        }
        SAVEFILE.parent.mkdir(parents=True, exist_ok=True)
        SAVEFILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[存档失败] {e}")
        return False


def load_game() -> list | None:
    """从磁盘加载对话历史。无存档返回 None。"""
    if not SAVEFILE.exists():
        return None
    try:
        data = json.loads(SAVEFILE.read_text(encoding="utf-8"))
        return data.get("messages", [])
    except Exception as e:
        print(f"[读档失败] {e}")
        return None


def delete_save():
    """删除存档文件"""
    if SAVEFILE.exists():
        SAVEFILE.unlink()


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS = 5


def game_loop():
    system_prompt = load_system_prompt()

    if not API_KEY:
        print("错误: 请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    print(f"API: {BASE_URL}")
    print(f"Flash 模型: {MODEL_FLASH}")
    print(f"Pro 模型:   {MODEL_PRO}")
    print()

    messages = [{"role": "system", "content": system_prompt}]

    # 初始消息：引导模型加载数据
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

    print("=" * 60)
    print("  TRPG Agent 内核 — 疯狂宅邸")
    print("  /quit 退出  /save 存档  /load 读档  /state 状态  /help 帮助")
    print("=" * 60)

    need_gm_turn = True
    current_model = MODEL_FLASH

    # 检测已有存档
    if SAVEFILE.exists():
        save_data = json.loads(SAVEFILE.read_text(encoding="utf-8"))
        msg_count = save_data.get("message_count", 0)
        if msg_count > 0:
            print(f"发现存档 ({msg_count} 条消息)，输入 /load 恢复进度，或直接开始新游戏。")
            print()

    while True:
        if need_gm_turn:
            tool_round = 0
            narrative = ""

            while tool_round <= MAX_TOOL_ROUNDS:
                response = call_llm(client, messages, current_model, TOOLS)
                if response is None:
                    break

                msg = response.choices[0].message
                text = msg.content or ""
                tool_calls = msg.tool_calls or []

                if not tool_calls:
                    narrative = text
                    break

                # 检测是否需要 Pro 处理
                if current_model == MODEL_FLASH and needs_pro_model(tool_calls):
                    # 切换到 Pro 重新处理
                    print(f"  [切换 Pro] 检测到复杂判定")
                    current_model = MODEL_PRO
                    # 不保存 Flash 的 tool_calls，用 Pro 重新生成
                    # 移除上一条 assistant 消息（如果有）
                    if messages and messages[-1]["role"] == "assistant":
                        messages.pop()
                    continue

                # 收集叙事部分
                if text:
                    narrative += text + "\n\n"

                # 记录 assistant 消息（含 tool_calls）
                assistant_msg = {"role": "assistant", "content": text}
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in tool_calls
                    ]
                messages.append(assistant_msg)

                # 执行每个 tool
                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    print(f"  [执行] {name}({json.dumps(args, ensure_ascii=False)})")
                    output = execute_function(name, args)
                    print(f"  [结果] {output[:120]}{'...' if len(output) > 120 else ''}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": output
                    })

                tool_round += 1

            if narrative.strip():
                print()
                print(narrative.strip())
                print()
                messages.append({"role": "assistant", "content": narrative.strip()})
            else:
                print("\n（模型未能生成有效回复，请重试。）\n")

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
            # 退出前自动存档
            print("正在保存...")
            save_game(messages)
            print(f"存档已保存到 {SAVEFILE}")
            print("游戏结束。")
            break

        if user_input.lower() == "/state":
            print()
            subprocess.run(["python3", "tools/state_manager.py", "get", "pc"],
                           cwd=PROJECT_ROOT)
            subprocess.run(["python3", "tools/state_manager.py", "clues"],
                           cwd=PROJECT_ROOT)
            print()
            need_gm_turn = False
            continue

        if user_input.lower() == "/help":
            print("""
命令:
  /quit, /exit, /q  退出游戏（自动存档）
  /save             手动存档（保存对话历史到 savegame.json）
  /load             读取存档（恢复上次的对话历史）
  /state            查看 PC 状态和已发现线索
  /help             显示此帮助

直接输入你角色的行动即可。""")
            need_gm_turn = False
            continue

        if user_input.lower() == "/save":
            if save_game(messages):
                print(f"存档成功 → {SAVEFILE}")
            need_gm_turn = False
            continue

        if user_input.lower() == "/load":
            loaded = load_game()
            if loaded is None:
                print("未找到存档文件。")
            else:
                # 保留 system prompt，替换其余消息
                system_msg = messages[0]
                messages = [system_msg] + loaded[1:]  # 跳过旧的 system prompt
                print(f"读档成功，恢复了 {len(loaded) - 1} 条消息。")
                need_gm_turn = True
            continue

        messages.append({"role": "user", "content": user_input})
        current_model = MODEL_FLASH  # 新回合重置为 Flash
        need_gm_turn = True


if __name__ == "__main__":
    game_loop()
