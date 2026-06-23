"""Tool 定义（Function Calling Schema）+ 执行器 + 骰子摘要"""

import json
import subprocess
from .config import PROJECT_ROOT, STATE_FILE


# ---------------------------------------------------------------------------
# Function Calling Schema
# ---------------------------------------------------------------------------

TOOLS = [
    # ---- 技能检定（属性绑定，确定性计算） ----
    {
        "type": "function",
        "function": {
            "name": "skill_check",
            "description": "执行一次完整的技能检定。自动读取 PC 属性值、查属性修正表、计算技能加值、掷 d20、比较 DC。用于所有技能/属性判定。绝不手动掷骰心算修正——始终调用此函数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "技能 ID: investigation, spot_hidden, persuasion, stealth, dodge, occult, first_aid, firearms, athletics"
                    },
                    "dc": {"type": "integer", "description": "难度等级 (5=琐碎, 10=简单, 15=中等, 20=困难, 25=极难)"},
                    "advantage": {
                        "type": "string",
                        "enum": ["advantage", "disadvantage"],
                        "description": "优势/劣势，不填则正常掷骰"
                    }
                },
                "required": ["skill", "dc"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dice_roll",
            "description": "掷骰子。仅用于先攻、随机事件等非技能场景。技能检定请用 skill_check。",
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
            "description": "记录新发现的线索。只在检定成功或确凿发现了信息时调用。根据线索性质选择分类：investigation(探案/现场证据)、event(事件/剧情)、task(任务/目标)、npc(人物相关发现)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "线索文本描述"},
                    "category": {
                        "type": "string",
                        "enum": ["investigation", "event", "task", "npc"],
                        "description": "线索分类"
                    }
                },
                "required": ["text", "category"]
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
            "name": "end_game",
            "description": "结束游戏。当故事到达结局时调用：侦探成功破案、角色死亡/疯狂、真相大白等。调用后游戏将进入结局画面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ending_type": {
                        "type": "string",
                        "enum": ["good", "bad", "neutral"],
                        "description": "结局类型：good=真相大白/成功破案，bad=死亡/疯狂/失败，neutral=撤退/不了了之"
                    },
                    "title": {"type": "string", "description": "结局标题，如「真相大白」「疯狂之末」"},
                    "summary": {"type": "string", "description": "结局概要，200字以内，总结整个冒险的关键发现和最终命运"}
                },
                "required": ["ending_type", "title", "summary"]
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
# 工具分类
# ---------------------------------------------------------------------------

COMPLEX_FUNCTIONS = {
    "skill_check",
    "dice_roll", "dice_roll_advantage", "dice_roll_disadvantage",
    "apply_damage", "apply_heal",
    "sanity_loss", "sanity_restore", "sanity_check",
}


def tool_category(name: str) -> str:
    if name == "skill_check":
        return "dice"
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
# CLI 执行器
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
    safe = lambda s: json.dumps(str(s), ensure_ascii=False)

    if name == "skill_check":
        skill = args.get("skill", "investigation")
        dc = args.get("dc", 15)
        adv = args.get("advantage", "")
        if adv in ("advantage", "disadvantage"):
            return _run_cli(f"python3 tools/skill_check.py {skill} {dc} {adv}")
        return _run_cli(f"python3 tools/skill_check.py {skill} {dc}")
    elif name == "dice_roll":
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
        text = safe(args.get("text", ""))
        cat = args.get("category", "investigation")
        return _run_cli(f"python3 tools/state_manager.py add-clue {text} {cat}")
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
        try:
            with open(STATE_FILE, "r+", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("scene_cache", {})[scene_id] = desc
                f.seek(0)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.truncate()
            return json.dumps({"cached": True, "scene_id": scene_id})
        except Exception as e:
            return json.dumps({"cached": False, "error": str(e)})

    elif name == "end_game":
        ending = args.get("ending_type", "neutral")
        title = args.get("title", "故事结束")
        summary = args.get("summary", "")
        state_path = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"
        with open(state_path, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["game_over"] = {
                "type": ending,
                "title": title,
                "summary": summary
            }
            f.seek(0)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.truncate()
        return json.dumps({"game_over": True, "ending_type": ending, "title": title, "summary": summary})

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
# 骰子结果摘要
# ---------------------------------------------------------------------------

def dice_summary(output: str) -> str | None:
    """从 dice.py 或 skill_check.py 的 JSON 输出中提取摘要"""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None

    # skill_check 输出（含属性绑定）
    if "skill_name" in data:
        skill = data["skill_name"]
        attr = data["attribute_name"]
        d20 = data["d20_roll"]
        total = data["total"]
        dc = data["dc"]
        attr_mod = data["attribute_modifier"]
        skill_bonus = data["skill_bonus"]
        adv = data.get("advantage", "normal")

        mod_str = ""
        if attr_mod >= 0:
            mod_str += f"+{attr_mod}"
        else:
            mod_str += f"{attr_mod}"
        if skill_bonus >= 0:
            mod_str += f"+{skill_bonus}"
        else:
            mod_str += f"{skill_bonus}"

        adv_suffix = ""
        if adv == "advantage":
            adv_suffix = " (优势)"
        elif adv == "disadvantage":
            adv_suffix = " (劣势)"

        status = "✓" if data["success"] else "✗"
        if data["critical"]:
            status = "★ 大成功！"
        if data["fumble"]:
            status = "💀 大失败！"

        return (
            f"🎲 【{skill}】(d20={d20}{mod_str}={total}) vs DC {dc} → {status}"
        )

    # dice.py 输出
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
