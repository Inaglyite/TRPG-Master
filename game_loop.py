#!/usr/bin/env python3
"""TRPG Agent 主循环 —— 原生 Function Calling + 双模型自动路由

- deepseek-v4-flash: 默认模型，处理叙事、对话、场景描述
- deepseek-v4-pro:   复杂判定（技能检定、战斗、理智）时自动切换
"""

import os
import sys
import json
import random
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

# GLM-4 Flash 快速模型（免费，用于检定后即时摘要）
GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash-250414")

# ---------------------------------------------------------------------------
# 沉浸式等待文本
# ---------------------------------------------------------------------------

TENSION_LINES = {
    "dice": [
        "命运之骰在黑暗中翻滚……",
        "这是个艰难的行动，不知道能不能成功……",
        "来，让我们看看命运站在哪一边……",
        "你在心中默默祈祷……",
        "成败在此一举——",
        "心跳加速，手心渗出细密的汗珠……",
        "空气仿佛凝固了……",
        "你深吸一口气，放手一搏……",
    ],
    "pro": [
        "这个判定比较复杂，需要仔细斟酌一下……",
        "让我想想，这个事情没有那么简单……",
        "局势微妙，容我仔细推敲……",
    ],
    "combat": [
        "肾上腺素在血管中奔涌……",
        "生死存亡，就在电光石火之间——",
        "战斗的本能接管了你的身体……",
    ],
    "sanity": [
        "一股莫名的寒意爬上你的脊背……",
        "你的理智正在经受考验……",
        "空气中似乎有什么东西在低语……",
    ],
}


def tension(category: str = "dice") -> str:
    """返回一条随机沉浸式等待文本"""
    lines = TENSION_LINES.get(category, TENSION_LINES["dice"])
    return random.choice(lines)


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
    {
        "type": "function",
        "function": {
            "name": "dice_roll",
            "description": "掷骰子。用于技能/属性检定、先攻、随机事件。每次检定必须掷骰，不得编造结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string", "description": "骰子规格，如 d20, d100, 2d6, 3d8+2"}
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
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dice_roll_disadvantage",
            "description": "d20 劣势掷骰（两骰取低）。用于不利情境。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_get",
            "description": "读取世界状态中的指定字段。用于获取 PC/NPC 属性、当前场景、标志等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "JSON 路径，如 pc.hp, npcs.0.name"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_set",
            "description": "修改世界状态中的指定字段并保存。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "JSON 路径"},
                    "value": {"type": "string", "description": "新值（JSON 格式）"}
                },
                "required": ["path", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_npcs",
            "description": "列出所有 NPC 及其 visible_tags。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_clues",
            "description": "列出已发现的所有线索。每次生成叙事前应调用。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_add_clue",
            "description": "记录新发现的线索。只在检定成功时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "线索文本描述"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_damage",
            "description": "对目标造成伤害。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标路径，如 pc, npcs.0"},
                    "amount": {"type": "integer", "description": "伤害数值"},
                    "damage_type": {
                        "type": "string",
                        "enum": ["物理", "火焰", "冰冻", "精神"],
                        "description": "伤害类型"
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
                    "target": {"type": "string", "description": "目标路径"},
                    "amount": {"type": "integer", "description": "治疗量"}
                },
                "required": ["target", "amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sanity_loss",
            "description": "对 PC 施加理智损失。",
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
                    "amount": {"type": "integer", "description": "恢复量"}
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
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_check",
            "description": "掷骰前向玩家确认。告知即将进行的检定（技能、难度DC），让玩家决定是否继续。仅在玩家自由行动且行动结果不确定时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "description": "技能名，如 侦查、说服、潜行"},
                    "attribute": {"type": "string", "description": "对应属性，如 WIS、CHA、DEX"},
                    "dc": {"type": "integer", "description": "难度等级 DC（5=琐碎 10=简单 15=中等 20=困难 25=极难）"},
                    "dc_label": {"type": "string", "description": "难度标签，如 中等、困难"},
                    "description": {"type": "string", "description": "向玩家展示的行动简述"}
                },
                "required": ["skill", "attribute", "dc", "dc_label", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cache_scene",
            "description": "缓存已生成的场景描写。后续进入同一场景时直接复用，不再重新生成。仅在首次描写一个场景后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string", "description": "场景ID，如 entrance_hall, east_wing_parlor"},
                    "description": {"type": "string", "description": "完整的场景描写文本（直接写自然语言，不需要 JSON 转义）"}
                },
                "required": ["scene_id", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目中的文件内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径，如 rules/rule_schema.json"}
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
    safe = lambda s: json.dumps(str(s))

    if name == "dice_roll":
        return _run_cli(f"python3 tools/dice.py {args.get('spec', 'd20')}")
    elif name == "dice_roll_advantage":
        return _run_cli("python3 tools/dice.py d20 adv")
    elif name == "dice_roll_disadvantage":
        return _run_cli("python3 tools/dice.py d20 dis")
    elif name == "state_get":
        return _run_cli(f"python3 tools/state_manager.py get {args.get('path', 'pc.hp')}")
    elif name == "state_set":
        return _run_cli(f"python3 tools/state_manager.py set {args.get('path', '')} {safe(args.get('value', ''))}")
    elif name == "state_npcs":
        return _run_cli("python3 tools/state_manager.py npcs")
    elif name == "state_clues":
        return _run_cli("python3 tools/state_manager.py clues")
    elif name == "state_add_clue":
        return _run_cli(f"python3 tools/state_manager.py add-clue {safe(args.get('text', ''))}")
    elif name == "apply_damage":
        target = args.get("target", "pc")
        amount = args.get("amount", 0)
        dtype = args.get("damage_type", "物理")
        return _run_cli(f"python3 tools/damage.py damage {target} {amount} {dtype}")
    elif name == "apply_heal":
        return _run_cli(f"python3 tools/damage.py heal {args.get('target', 'pc')} {args.get('amount', 0)}")
    elif name == "sanity_loss":
        return _run_cli(f"python3 tools/sanity.py loss {args.get('severity', 'moderate')}")
    elif name == "sanity_restore":
        return _run_cli(f"python3 tools/sanity.py restore {args.get('amount', 0)}")
    elif name == "sanity_check":
        return _run_cli("python3 tools/sanity.py check")
    elif name == "suggest_check":
        skill = args.get("skill", "?")
        attr = args.get("attribute", "?")
        dc = args.get("dc", 15)
        dc_label = args.get("dc_label", "中等")
        desc = args.get("description", "")
        print()
        print(f"  ⚡ 检定提议：{desc}")
        print(f"     【{skill}】（{attr}）— 难度：{dc_label}（DC {dc}）")
        try:
            answer = input("  → 确定尝试吗？(y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("y", "yes", "是"):
            return json.dumps({"confirmed": True, "skill": skill, "attribute": attr, "dc": dc})
        else:
            return json.dumps({"confirmed": False, "reason": "玩家选择不冒险"})

    elif name == "cache_scene":
        scene_id = args.get("scene_id", "")
        desc = args.get("description", "")
        state_path = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"
        try:
            with open(state_path, "r+", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("scene_cache", {})[scene_id] = desc
                f.seek(0)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.truncate()
            return json.dumps({"cached": True, "scene_id": scene_id})
        except Exception as e:
            return json.dumps({"cached": False, "error": str(e)})

    elif name == "read_file":
        path = args.get("path", "")
        full_path = (PROJECT_ROOT / path).resolve()
        if not str(full_path).startswith(str(PROJECT_ROOT)):
            return "[错误] 不允许读取项目外的文件"
        if not full_path.exists():
            return f"[错误] 文件不存在: {path}"
        return full_path.read_text(encoding="utf-8")
    else:
        return f"[错误] 未知函数: {name}"


# ---------------------------------------------------------------------------
# 工具分类（用于沉浸式文本选择）
# ---------------------------------------------------------------------------

COMPLEX_FUNCTIONS = {
    "dice_roll", "dice_roll_advantage", "dice_roll_disadvantage",
    "apply_damage", "apply_heal",
    "sanity_loss", "sanity_restore", "sanity_check",
}


def tool_category(name: str) -> str:
    if name.startswith("dice"):
        return "dice"
    if name.startswith("sanity"):
        return "sanity"
    if name in ("apply_damage", "apply_heal"):
        return "combat"
    return "dice"


def _tc_name(tc) -> str:
    """兼容 SDK 对象和 plain dict 两种 tool_call 格式"""
    if isinstance(tc, dict):
        return tc.get("function", {}).get("name", "")
    return tc.function.name


def needs_pro_model(tool_calls: list) -> bool:
    for tc in tool_calls:
        if _tc_name(tc) in COMPLEX_FUNCTIONS:
            return True
    return False


# ---------------------------------------------------------------------------
# LLM 调用（流式 + 非流式）
# ---------------------------------------------------------------------------

def stream_llm(client: OpenAI, messages: list, model: str,
               tools: list | None = None) -> tuple[str, list]:
    """流式调用 LLM——文本实时输出，tool_calls 静默积累。
    返回 (full_text, tool_calls_list)。"""
    kwargs = dict(model=model, messages=messages, temperature=0.8,
                  max_tokens=2048, stream=True)
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    try:
        stream = client.chat.completions.create(**kwargs)
    except Exception as e:
        print(f"\n[API 错误] {e}")
        return "", []

    full_text = ""
    tool_calls_acc: dict[int, dict] = {}  # index → {id, function_name, arguments}

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        if delta.content:
            full_text += delta.content
            print(delta.content, end="", flush=True)

        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""}
                    }
                acc = tool_calls_acc[idx]
                if tc_delta.id:
                    acc["id"] += tc_delta.id  # DeepSeek 可能分段发 id
                if tc_delta.function:
                    if tc_delta.function.name:
                        acc["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        acc["function"]["arguments"] += tc_delta.function.arguments

    tool_calls_list = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
    return full_text, tool_calls_list


def call_llm(client: OpenAI, messages: list, model: str,
             tools: list | None = None) -> dict:
    """非流式调用 LLM（用于工具执行后的中间步骤）。"""
    kwargs = dict(model=model, messages=messages, temperature=0.8, max_tokens=2048)
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
    try:
        serializable = []
        for m in messages:
            entry = {"role": m["role"], "content": m.get("content", "")}
            if "tool_calls" in m:
                entry["tool_calls"] = m["tool_calls"]
            if "tool_call_id" in m:
                entry["tool_call_id"] = m["tool_call_id"]
            serializable.append(entry)
        data = {"version": 2, "messages": serializable, "message_count": len(serializable)}
        SAVEFILE.parent.mkdir(parents=True, exist_ok=True)
        SAVEFILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[存档失败] {e}")
        return False


def load_game() -> list | None:
    if not SAVEFILE.exists():
        return None
    try:
        data = json.loads(SAVEFILE.read_text(encoding="utf-8"))
        return data.get("messages", [])
    except Exception as e:
        print(f"[读档失败] {e}")
        return None


# ---------------------------------------------------------------------------
# 骰子结果摘要（即时显示给玩家）
# ---------------------------------------------------------------------------

def dice_summary(output: str) -> str | None:
    """从 dice.py 的 JSON 输出中提取玩家友好的摘要，如 '🎲 d20 = 14'"""
    try:
        data = json.loads(output)
        spec = data.get("spec", "?").upper()
        total = data["total"]
        rolls = data.get("rolls", [total])
        mod = data.get("modifier", 0)
        adv = data.get("advantage", False)
        dis = data.get("disadvantage", False)

        rolls_str = ", ".join(str(r) for r in rolls)
        if adv:
            return f"🎲 {spec} (优势) → 取 {total}"
        elif dis:
            return f"🎲 {spec} (劣势) → 取 {total}"
        elif mod:
            return f"🎲 {rolls_str} +{mod} = {total}"
        else:
            return f"🎲 {spec} = {total}"
    except (json.JSONDecodeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# GLM-4 Flash 快速摘要（检定后即时反馈，消除愣神）
# ---------------------------------------------------------------------------

_glm_client: OpenAI | None = None


def _get_glm() -> OpenAI | None:
    global _glm_client
    if _glm_client is not None:
        return _glm_client
    if GLM_API_KEY:
        _glm_client = OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL)
        return _glm_client
    return None


def glm_quick_summary(tool_outputs: list[tuple[str, str]], model_context: str) -> str | None:
    """用 GLM-4 Flash 生成 1-2 句即时检定摘要。极快（<1s），免费。"""
    glm = _get_glm()
    if glm is None:
        return None

    # 从工具结果拼出检定信息
    dice_info = ""
    sanity_info = ""
    damage_info = ""
    for name, out in tool_outputs:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            continue
        if "spec" in data:
            dice_info = f"d{data['sides']} = {data['total']}"
            if data.get("rolls"):
                dice_info += f"（掷出 {data['rolls']}）"
        if "loss_amount" in data:
            sanity_info = f"理智 -{data['loss_amount']}"
        if "damage" in data:
            damage_info = f"造成 {data['damage']} 点{data.get('damage_type', '')}伤害"
        if "heal_amount" in data:
            damage_info = f"恢复 {data['heal_amount']} 点生命"

    parts = [p for p in [dice_info, sanity_info, damage_info] if p]
    if not parts:
        return None

    result_summary = "，".join(parts)
    context_snippet = model_context[:300] if model_context else ""

    prompt = (
        f"检定：{result_summary}。\n"
        f"上下文：{context_snippet}\n\n"
        "用1-2句有画面感的中文概述这个检定结果。不要提问，不要给选项，不要剧透NPC秘密。"
    )

    try:
        resp = glm.chat.completions.create(
            model=GLM_MODEL,
            messages=[
                {"role": "system", "content": "你是TRPG游戏检定播报员。用简洁有画面感的中文叙述检定结果。1-2句。不提问，不给选项。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=80,
        )
        return resp.choices[0].message.content
    except Exception:
        return None


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
                # 全部流式——叙事实时输出，工具调用静默积累
                if tool_round == 0:
                    print()  # 首次调用前换行，分隔玩家输入
                text, tool_calls = stream_llm(client, messages, current_model, TOOLS)

                if not text and not tool_calls:
                    break

                if not tool_calls:
                    # 叙事已流式输出到终端，无需再打印
                    narrative = text
                    break

                # --- 有工具调用 ---
                # Pro 切换
                if current_model == MODEL_FLASH and needs_pro_model(tool_calls):
                    current_model = MODEL_PRO
                    cat = "dice"
                    for tc in tool_calls:
                        if tc["function"]["name"].startswith("sanity"):
                            cat = "sanity"
                            break
                        elif tc["function"]["name"] in ("apply_damage", "apply_heal"):
                            cat = "combat"
                            break
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

                    # 骰子结果即时显示
                    if name in ("dice_roll", "dice_roll_advantage", "dice_roll_disadvantage"):
                        summary = dice_summary(output)
                        if summary:
                            print(f"  {summary}")

                    # 收集复杂工具结果用于 GLM 快速摘要
                    if name in COMPLEX_FUNCTIONS:
                        tool_outputs.append((name, output))

                # --- GLM 快速摘要：检定后秒出 1-2 句反馈 ---
                if tool_outputs:
                    quick = glm_quick_summary(tool_outputs, text or narrative)
                    if quick:
                        print(f"  {quick}")
                        print()

                tool_round += 1

            if narrative.strip():
                # 流式已在终端输出，这里只加入消息历史 + 补换行
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


if __name__ == "__main__":
    game_loop()
