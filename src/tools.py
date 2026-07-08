"""Tool 定义（Function Calling Schema）+ 执行器 + 骰子摘要"""

import json
import sys
import os
import base64
import mimetypes
import subprocess
from .config import PROJECT_ROOT, STATE_FILE
from . import config as _cfg


# ---------------------------------------------------------------------------
# Function Calling Schema
# ---------------------------------------------------------------------------

TOOLS = [
    # ---- 技能检定（COC 第七版 d100 roll-under） ----
    {
        "type": "function",
        "function": {
            "name": "skill_check",
            "description": "COC 7e d100 技能检定。自动读取 PC 技能值，掷 d100，判断成功等级。d100 ≤ 技能值 = 常规成功，≤ 半值 = 困难成功，≤ 五分之一 = 极难成功。01 = 大成功，100 = 大失败。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "技能 ID: spot_hidden, persuade, dodge, fighting_brawl, firearms_handgun, stealth, library_use, listen, psychology, first_aid, occult, charm, fast_talk, intimidate, climb, locksmith, navigate, drive_auto, credit_rating, language_own, cthulhu_mythos, psychoanalysis 等(见 rule_schema.json)"
                    },
                    "bonus_dice": {
                        "type": "integer",
                        "description": "奖励骰数量（额外十位骰取优），默认 0。多个可叠加，与惩罚骰抵消"
                    },
                    "penalty_dice": {
                        "type": "integer",
                        "description": "惩罚骰数量（额外十位骰取劣），默认 0。多个可叠加，与奖励骰抵消"
                    },
                    "push": {
                        "type": "boolean",
                        "description": "是否为孤注一掷。战斗技能/理智检定不可孤注一掷。push=true 则 is_push=true"
                    }
                },
                "required": ["skill"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dice_roll",
            "description": "掷普通骰子。用于伤害、SAN损失、先攻、随机事件。技能检定请用 skill_check。",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string", "description": "骰子规格，如 d100, 1d6, 2d6, 1d10, 3d10"}
                },
                "required": ["spec"]
            }
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
            "description": "列出所有 NPC 及其 visible_tags、揭示程度（revealed_level 和 revealed_entries）。每次叙事前应调用以确认信息边界。",
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
            "description": "记录新发现的线索。只在检定成功或确凿发现了信息时调用。根据线索性质选择分类：investigation(探案/现场证据)、event(事件/剧情)、task(任务/目标)、npc(人物相关发现)。如果该线索对应 asset_map.clues 中的展示材料，请提供 asset_id；引擎会在记录线索后自动分发图片。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "线索文本描述"},
                    "category": {
                        "type": "string",
                        "enum": ["investigation", "event", "task", "npc"],
                        "description": "线索分类"
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "可选。对应 world_state.asset_map.clues 的键，如 wright_body、monster_manifest、wick_dinner。仅在线索确实对应该图片时填写。"
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
    # ---- 物品管理 ----
    {
        "type": "function",
        "function": {
            "name": "state_add_item",
            "description": "添加物品到 PC 背包。当玩家捡起、获得或购买物品时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "物品名称，如'黄铜钥匙''银质徽章'"}
                },
                "required": ["item"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "state_remove_item",
            "description": "从 PC 背包移除物品。当玩家使用、丢弃或失去物品时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "物品名称"}
                },
                "required": ["item"]
            }
        }
    },
    # ---- 模组导入 ----
    {
        "type": "function",
        "function": {
            "name": "import_module",
            "description": "从 Markdown 文件导入模组。解析 MD 中 PC/NPC/场景/线索/标志，覆盖当前 world_state.json。路径如 mod/mansion_of_madness/module.md。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "模组 MD 文件路径"}
                },
                "required": ["path"]
            }
        }
    },
    # ---- 角色管理 ----
    {
        "type": "function",
        "function": {
            "name": "create_character",
            "description": "按 COC 7e 规则创建新角色。随机掷八项属性，分配职业技能点和兴趣技能点。可用职业：私家侦探/记者/医生/教授/古董商/警察/牧师/作家/神秘学家。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "角色姓名"},
                    "occupation": {"type": "string", "description": "职业名称"}
                },
                "required": ["name", "occupation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "load_character",
            "description": "从 JSON 文件加载已有角色卡并应用到当前游戏。路径如 characters/黄千陆.json。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "角色卡 JSON 文件路径"}
                },
                "required": ["path"]
            }
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
    },
    # ---- 属性检定与核心机制 ----
    {
        "type": "function",
        "function": {
            "name": "attribute_check",
            "description": "裸属性检定（非技能）。d100 ≤ 属性值 = 成功。用于纯体力/智力/意志行动。STR=撞门搬物, DEX=接物平衡, CON=抗毒抗病, INT=理解信息/灵感浮现, POW=意志对抗, SIZ=挤入窄缝, APP=第一印象(暗骰), EDU=常识知识。APP检定应为暗骰，不告知玩家数值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "attribute": {
                        "type": "string",
                        "enum": ["STR", "DEX", "CON", "INT", "POW", "SIZ", "APP", "EDU"],
                        "description": "属性缩写"
                    },
                    "bonus_dice": {"type": "integer", "description": "奖励骰数量"},
                    "penalty_dice": {"type": "integer", "description": "惩罚骰数量"}
                },
                "required": ["attribute"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "luck_check",
            "description": "幸运检定。d100 ≤ 当前 POW → 成功。用于外部环境因素（停车位、设备是否正常、恰巧遇到某人）。不可孤注一掷。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    # ---- 心理与精神分析 ----
    {
        "type": "function",
        "function": {
            "name": "psychoanalysis",
            "description": "精神分析治疗。进行 psychoanalysis 技能检定，成功恢复目标 1D3 SAN 并暂时压制恐惧症1小时。同一目标同一天只能受益一次。用于安抚恐慌队友或缓解疯狂症状。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标路径，pc 或 NPC ID"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reality_check",
            "description": "现实认知检定。仅用于处于潜在疯狂期的调查员鉴别所见是否为幻觉。SAN检定（d100≤当前SAN）→成功看穿幻觉获得抗性；失败失去1SAN并可能触发疯狂发作。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sanity_trigger",
            "description": "判断当前场景是否应触发SAN损失及严重度。不掷骰，只输出建议。在玩家遭遇恐怖/震惊/超自然事件时，先调用此工具获得severity建议，再调用sanity_loss。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "场景简述，如'玩家第一次看到血肉模糊的尸体''NPC突然表现出超自然特征'"}
                },
                "required": ["description"]
            }
        }
    },
    # ---- 展示材料与线索关联 ----
    {
        "type": "function",
        "function": {
            "name": "show_handout",
            "description": "向玩家展示视觉材料（NPC肖像/场景图/线索图片）。当玩家首次遇到NPC、进入新场景、或发现关键线索时调用。引擎自动查找资产映射并推送到前端。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {"type": "string", "enum": ["npc", "scene", "clue"], "description": "实体类型"},
                    "entity_id": {"type": "string", "description": "实体ID（NPC ID/场景ID/线索ID）"}
                },
                "required": ["entity_type", "entity_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "link_clues",
            "description": "创建两条线索的关联推理，自动生成TIER_2推理线索条目。当玩家/守秘人发现两条线索之间存在逻辑关联时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string", "description": "源线索ID"},
                    "to_id": {"type": "string", "description": "目标线索ID"},
                    "reasoning": {"type": "string", "description": "关联推理描述，如'血迹方向指向楼梯口，管家恰好对楼梯口存在记忆空白'"}
                },
                "required": ["from_id", "to_id", "reasoning"]
            }
        }
    },
    # ---- NPC 信息边界管理 ----
    {
        "type": "function",
        "function": {
            "name": "set_psychological_trait",
            "description": "记录 PC 的心理特质（恐惧症/躁狂症/性格特质/重要关系）。疯狂发作触发恐惧症或躁狂症时调用。自定义内容必须锚定于实际游戏事件（创伤场景），不可凭空编造无关内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["phobia", "mania", "trait", "relationship"],
                        "description": "phobia=恐惧症, mania=躁狂症, trait=性格特质, relationship=重要关系"
                    },
                    "name": {"type": "string", "description": "特质名称。恐惧症格式如'墨水恐惧——对任何黑色液体的非理性恐惧'。必须与游戏中的触发事件相关。"},
                    "context": {"type": "string", "description": "触发/来源背景，如'在地下室目睹怪物从墨水中显形后产生'"}
                },
                "required": ["category", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "npc_reveal",
            "description": "记录 PC 对 NPC 秘密的揭示进度。当检定成功获得 NPC 相关信息时调用。tier: 1=表层观察(紧张/回避等行为线索), 2=部分推断(拼凑多条线索后的结论), 3=完全揭露(NPC主动坦白或无可辩驳的证据)。调用后该 NPC 的 revealed_level 会自动更新。",
            "parameters": {
                "type": "object",
                "properties": {
                    "npc_id": {"type": "string", "description": "NPC ID，如 butler_gregory, lady_elizabeth"},
                    "tier": {"type": "integer", "description": "揭示层级：1/2/3"},
                    "entry_text": {"type": "string", "description": "揭示的具体信息，如'管家在提及楼梯口时表现出异常紧张'"}
                },
                "required": ["npc_id", "tier", "entry_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_npc_secret",
            "description": "获取 NPC 的完整秘密信息（守秘人专用）。仅在需要确认 NPC 幕后设定时调用——绝不在叙事中直接引用返回值。调用后你仍然只能基于已揭示的 tier 层级来描述信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "npc_id": {"type": "string", "description": "NPC ID"}
                },
                "required": ["npc_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_private_memory",
            "description": "读取守秘人私有工作记忆。包含幕后计划、隐藏事实、推理笔记。用于确认当前信息边界——哪些秘密尚未揭示、当前剧情推进方向。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_private_memory",
            "description": "更新私有工作记忆的指定字段。在剧情推进或秘密被揭示后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "description": "要更新的字段：goals_and_plans / hidden_facts / inference_notes"},
                    "value": {"type": "string", "description": "新值（JSON 字符串或纯文本）"}
                },
                "required": ["section", "value"]
            }
        }
    }
]

# ---------------------------------------------------------------------------
# 工具分类
# ---------------------------------------------------------------------------

COMPLEX_FUNCTIONS = {
    "skill_check",
    "dice_roll",
    "apply_damage", "apply_heal",
    "sanity_loss", "sanity_restore", "sanity_check",
    "create_character",
    "attribute_check", "luck_check",
    "psychoanalysis", "reality_check",
}


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

def _run_cli(argv: list) -> str:
    try:
        # 传入 TRPG_MODULE 环境变量,确保子进程读写的 world_state.json
        # 与运行时切换后的活跃模组一致(set_active_module 不写 os.environ)
        env = {
            **os.environ,
            "TRPG_MODULE": _cfg.MODULE_NAME,
            "PYTHONIOENCODING": "utf-8",
        }
        # shell=False + argv 列表：经 CreateProcess 直接启动，exe 路径含
        # 空格(Program Files)或中文(疯狂宅邸)时不会被 cmd /c 截断。
        result = subprocess.run(
            [str(a) for a in argv], shell=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=30, cwd=PROJECT_ROOT, env=env
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
    exe = sys.executable  # frozen exe 路径；argv 列表经 CreateProcess 启动，不怕空格/中文

    if name == "skill_check":
        bonus = args.get("bonus_dice", 0) or 0
        penalty = args.get("penalty_dice", 0) or 0
        argv = [exe, "tools/skill_check.py", args.get("skill", "spot_hidden"), bonus, penalty]
        if args.get("push", False):
            argv.append("--push")
        return _run_cli(argv)
    elif name == "dice_roll":
        return _run_cli([exe, "tools/dice.py", args.get('spec', 'd20')])
    elif name == "state_get":
        return _run_cli([exe, "tools/state_manager.py", "get", args.get('path', 'pc.hp')])
    elif name == "state_set":
        return _run_cli([exe, "tools/state_manager.py", "set", args.get('path', ''), args.get('value', '')])
    elif name == "state_npcs":
        return _run_cli([exe, "tools/state_manager.py", "npcs"])
    elif name == "state_clues":
        return _run_cli([exe, "tools/state_manager.py", "clues"])
    elif name == "state_add_clue":
        argv = [exe, "tools/state_manager.py", "add-clue", args.get("text", ""), args.get("category", "investigation")]
        asset_id = args.get("asset_id", "") or ""
        if asset_id:
            argv.append(asset_id)
        return _run_cli(argv)
    elif name == "state_add_item":
        return _run_cli([exe, "tools/state_manager.py", "add-item", args.get('item', '')])
    elif name == "state_remove_item":
        return _run_cli([exe, "tools/state_manager.py", "remove-item", args.get('item', '')])
    elif name == "apply_damage":
        return _run_cli([exe, "tools/damage.py", "damage", args.get('target', 'pc'), args.get('amount', 0), args.get('damage_type', '物理')])
    elif name == "apply_heal":
        return _run_cli([exe, "tools/damage.py", "heal", args.get('target', 'pc'), args.get('amount', 0)])
    elif name == "sanity_loss":
        return _run_cli([exe, "tools/sanity.py", "loss", args.get('severity', 'moderate')])
    elif name == "sanity_restore":
        return _run_cli([exe, "tools/sanity.py", "restore", args.get('amount', 0)])
    elif name == "sanity_check":
        return _run_cli([exe, "tools/sanity.py", "check"])
    elif name == "import_module":
        return _run_cli([exe, "tools/module_loader.py", args.get('path', '')])
    elif name == "create_character":
        return _run_cli([exe, "tools/character.py", "create", args.get('name', '调查员'), args.get('occupation', '私家侦探')])
    elif name == "load_character":
        return _run_cli([exe, "tools/character.py", "load", args.get('path', '')])
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
        with open(_cfg.STATE_FILE, "r+", encoding="utf-8") as f:
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
    elif name == "attribute_check":
        bonus = args.get("bonus_dice", 0) or 0
        penalty = args.get("penalty_dice", 0) or 0
        return _run_cli([exe, "tools/skill_check.py", args.get("attribute", "STR"), bonus, penalty])
    elif name == "luck_check":
        return _run_cli([exe, "tools/skill_check.py", "POW"])
    elif name == "psychoanalysis":
        return _run_cli([exe, "tools/sanity.py", "psychoanalysis", args.get('target', 'pc')])
    elif name == "reality_check":
        return _run_cli([exe, "tools/sanity.py", "reality-check"])
    elif name == "sanity_trigger":
        desc = args.get("description", "")
        # 基于关键词的 severity 建议
        if any(w in desc for w in ["直视", "伟大", "克苏鲁", "神话生物完全显形"]):
            suggestion = "catastrophic"
        elif any(w in desc for w in ["朋友被杀", "目击死亡", "尸雨", "严刑拷打", "割喉"]):
            suggestion = "major"
        elif any(w in desc for w in ["恐怖尸体", "血肉模糊", "超自然", "非人", "不是人类", "食尸鬼", "深潜者", "怪物显形", "第一次杀人"]):
            suggestion = "moderate"
        elif any(w in desc for w in ["尸体", "血迹", "诡异", "禁忌文本", "噩梦", "幻觉", "异常倒影", "第一次目睹"]):
            suggestion = "minor"
        elif any(w in desc for w in ["不安", "违和感", "奇怪", "不对劲"]):
            suggestion = "trivial"
        else:
            suggestion = "moderate"  # 默认
        return json.dumps({
            "suggestion": suggestion,
            "note": "这是建议的严重度，最终由守秘人根据具体情境决定。确认后调用 sanity_loss(severity=...)",
            "severity_options": {
                "trivial": "0/1 (几乎无损失)",
                "minor": "0/1D4 (轻微不适)",
                "moderate": "1/1D6+1 (明显冲击)",
                "major": "1D4/2D6+2 (严重创伤)",
                "catastrophic": "1D10/1D100 (终极恐怖)"
            }
        }, ensure_ascii=False)
    elif name == "set_psychological_trait":
        return _run_cli([exe, "tools/state_manager.py", "psych-trait", args.get('category', 'phobia'), args.get('name', ''), args.get('context', '')])
    elif name == "show_handout":
        result = _run_cli([exe, "tools/state_manager.py", "show-handout", args.get('entity_type', 'npc'), args.get('entity_id', '')])
        # 读取资产文件并转 base64 data URI（electron file:// 下 HTTP URL 不可用）
        try:
            info = json.loads(result)
            if info.get("found") and info.get("file"):
                asset_path = _cfg.MODULE_DIR / "assets" / info["file"]
                if asset_path.exists():
                    mime = mimetypes.guess_type(str(asset_path))[0] or "image/png"
                    data = base64.b64encode(asset_path.read_bytes()).decode("ascii")
                    info["asset_data_uri"] = f"data:{mime};base64,{data}"
                    # 同时保留 URL 供 web 模式使用
                    info["asset_url"] = f"/api/assets/{_cfg.MODULE_NAME}/{info['file']}"
                    result = json.dumps(info, ensure_ascii=False)
        except Exception:
            pass
        return result
    elif name == "link_clues":
        return _run_cli([exe, "tools/state_manager.py", "link-clues", args.get('from_id', ''), args.get('to_id', ''), args.get('reasoning', '')])
    elif name == "npc_reveal":
        return _run_cli([exe, "tools/state_manager.py", "npc-reveal", args.get('npc_id', ''), args.get('tier', 1), args.get('entry_text', '')])
    elif name == "get_npc_secret":
        return _run_cli([exe, "tools/state_manager.py", "npc-secret", args.get('npc_id', '')])
    elif name == "get_private_memory":
        return _run_cli([exe, "tools/state_manager.py", "private-memory"])
    elif name == "update_private_memory":
        return _run_cli([exe, "tools/state_manager.py", "private-memory-update", args.get('section', ''), args.get('value', '')])
    else:
        return f"[错误] 未知函数: {name}"


# ---------------------------------------------------------------------------
# 骰子结果摘要
# ---------------------------------------------------------------------------

def dice_summary(output: str) -> str | None:
    """从 skill_check.py 或 dice.py 的 JSON 输出中提取摘要"""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None

    # skill_check d100 输出
    if "d100_roll" in data:
        skill = data.get("skill_name", data.get("skill", "?"))
        roll = data["d100_roll"]
        value = data["skill_value"]
        level = data["level"]
        bonus = data.get("bonus_dice", 0)
        penalty = data.get("penalty_dice", 0)
        is_push = data.get("is_push", False)

        level_emoji = {
            "critical_success": "★ 大成功！",
            "extreme_success": "✦ 极难成功",
            "hard_success": "◆ 困难成功",
            "regular_success": "✓ 成功",
            "failure": "✗ 失败",
            "fumble": "💀 大失败！"
        }

        dice_note = ""
        if bonus > 0:
            dice_note = f" ({bonus}奖励骰)"
        elif penalty > 0:
            dice_note = f" ({penalty}惩罚骰)"
        if is_push:
            dice_note += " [孤注一掷]"

        return (
            f"🎲 【{skill}】d100={roll}{dice_note} vs {value} → {level_emoji.get(level, level)}"
        )

    # dice.py 输出
    spec = data.get("spec", "?").upper()
    total = data["total"]
    rolls = data.get("rolls", [total])
    mod = data.get("modifier", 0)

    rolls_str = ", ".join(str(r) for r in rolls)
    if mod:
        return f"🎲 {rolls_str} +{mod} = {total}"
    else:
        return f"🎲 {spec} = {total}"
