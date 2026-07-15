"""Tool 定义（Function Calling Schema）+ 执行器 + 骰子摘要"""

import base64
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path

from .consequences import SanitySeverity, classify_sanity_consequence
from .endings import validate_ending
from .runtime import RuntimeContext
from .tool_runtime import ToolRuntime, UnknownToolError


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
            "description": "记录新发现的线索。只在检定成功或确凿发现了信息时调用。根据线索性质选择分类：investigation(探案/现场证据)、event(事件/剧情)、task(任务/目标)、npc(人物相关发现)。若发现对应 clue_catalog 中的预设线索，优先提供 clue_id；引擎会采用模组定义并可靠分发关联素材。asset_id 只用于没有预设线索的兼容场景。",
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
                    },
                    "clue_id": {
                        "type": "string",
                        "description": "可选。对应 world_state.clue_catalog 的稳定线索 ID；匹配预设线索时优先填写，以使用作者配置的分类、关联与素材触发。"
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
            "name": "sanity_event",
            "description": "一次性结算恐怖事件：根据场景描述确认严重度、执行SAN检定与损失，并触发关联展示素材。关联素材对应的预设线索及其flag_effects会自动提交；结果会列出auto_committed，禁止再重复调用state_add_clue或state_set。首次目击尸体、超自然现象或其他恐怖事件时只调用本工具一次。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "玩家实际目击的恐怖场景简述"
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["trivial", "minor", "moderate", "major", "catastrophic"],
                        "description": "按模组设定选择的损失严重度"
                    },
                    "clue_id": {
                        "type": "string",
                        "description": "同一恐怖发现对应的available_scene_clues稳定ID；没有时传空字符串"
                    },
                    "npc_reveals": {
                        "type": "array",
                        "description": "同一事件已明确揭示的NPC信息；没有时传空数组",
                        "items": {
                            "type": "object",
                            "properties": {
                                "npc_id": {"type": "string"},
                                "tier": {"type": "integer"},
                                "entry_text": {"type": "string"}
                            },
                            "required": ["npc_id", "tier", "entry_text"]
                        }
                    }
                },
                "required": ["description", "severity", "clue_id", "npc_reveals"]
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
            "name": "use_item",
            "description": "确定性使用调查员物品。战斗外任何真实开枪（鸣枪、试射、打锁/灯等）必须用 firearm_discharge 扣弹；普通可重复道具用 use 验证持有但不消耗；明确的一次性道具用 consume。对角色/NPC 发起的枪械攻击改用 combat_action，不能与本工具重复扣弹。必须先调用工具成功，再叙述物品已生效。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "物品名称或足以唯一匹配物品栏的关键字"},
                    "operation": {
                        "type": "string",
                        "enum": ["use", "consume", "firearm_discharge"],
                        "description": "use=验证持有且不消耗；consume=消耗一次性物品；firearm_discharge=战斗外开枪并扣减发数"
                    },
                    "amount": {"type": "integer", "minimum": 1, "maximum": 20, "description": "消耗数量或开枪发数，默认 1"},
                    "reason": {"type": "string", "description": "具体用途，如鸣枪示警、用钥匙开门、喝下药剂"}
                },
                "required": ["item", "operation", "reason"]
            }
        }
    },
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
            "description": "从 PC 背包移除物品。只在玩家明确丢弃、交出或永久失去物品时调用；正常使用与消耗必须调用 use_item。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "物品名称"}
                },
                "required": ["item"]
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
                    "occupation": {"type": "string", "description": "职业名称"},
                    "violence_stance": {
                        "type": "string",
                        "enum": ["avoidant", "conditional", "unrestrained"],
                        "description": "角色对主动暴力的倾向；只影响角色扮演与确认文案",
                    },
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
    # ---- 服务端战斗状态机 ----
    {
        "type": "function",
        "function": {
            "name": "combat_start",
            "description": "进入战斗并建立唯一的服务端回合状态。玩家最新输入已明确包含首个攻击或武力威胁时，必须同时填写 initial_action，状态机会立即确认/结算，绝不能只开战后再次询问玩家。自动加入 PC 并按 DEX 排序；NPC 缺少战斗数值时可提供保守覆盖值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "发生敌对行动的简述"},
                    "initial_action": {
                        "type": "object",
                        "description": "玩家在最新输入中已经明确声明的开场攻击或武力威胁；没有明确行动时省略",
                        "properties": {
                            "actor_id": {"type": "string", "description": "通常为 pc"},
                            "target_id": {"type": "string", "description": "目标 NPC id"},
                            "action_type": {
                                "type": "string",
                                "enum": ["melee", "firearm", "threat"],
                                "description": "threat 表示持枪、持刀等武力胁迫但尚未攻击",
                            },
                            "description": {"type": "string", "description": "玩家已经声明的具体行动意图"},
                            "skill": {"type": "string", "description": "攻击技能 id"},
                            "weapon": {"type": "string", "description": "武器名称或物品栏关键字"},
                            "damage_spec": {"type": "string", "description": "攻击伤害骰，如 1d8"},
                            "damage_mode": {"type": "string", "enum": ["normal", "impaling", "blunt"]},
                            "bonus_dice": {"type": "integer", "minimum": 0, "maximum": 2},
                            "penalty_dice": {"type": "integer", "minimum": 0, "maximum": 2},
                        },
                        "required": ["actor_id", "target_id", "action_type", "description"],
                    },
                    "participants": {
                        "type": "array",
                        "description": "参战者配置。PC 可省略并会自动加入；NPC 必须使用 world_state 中的 id。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "pc 或 NPC id"},
                                "dex": {"type": "integer", "description": "可选 DEX 覆盖值"},
                                "con": {"type": "integer", "description": "可选 CON 覆盖值，用于重伤检定"},
                                "fighting_brawl": {"type": "integer", "description": "可选斗殴覆盖值"},
                                "dodge": {"type": "integer", "description": "可选闪避覆盖值"},
                                "firearms_handgun": {"type": "integer", "description": "可选手枪覆盖值"},
                                "damage_spec": {"type": "string", "description": "默认伤害骰，如 1d6"},
                                "ready_firearm": {"type": "boolean", "description": "是否已持枪待发；先攻按 DEX+50"}
                            },
                            "required": ["id"]
                        }
                    }
                },
                "required": ["participants", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "combat_status",
            "description": "读取服务端战斗状态、当前轮次、行动者、参战者生命值和待处理决定。战斗专员不确定当前行动者时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "combat_action",
            "description": "提交当前行动者的一个战斗动作。工具验证行动顺序、掷骰、比较成功等级、结算伤害并推进回合。NPC 攻击 PC 时会暂停并弹出玩家防御选择，绝不能替玩家选择。",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_id": {"type": "string", "description": "必须等于 combat_status.current_actor"},
                    "target_id": {"type": "string", "description": "攻击目标 id；移动或其他动作可省略"},
                    "action_type": {"type": "string", "enum": ["melee", "firearm", "threat", "move", "other"]},
                    "description": {"type": "string", "description": "具体动作意图，供确认弹窗和后续叙事使用"},
                    "skill": {"type": "string", "description": "可选技能 id，近战默认 fighting_brawl，射击默认 firearms_handgun"},
                    "weapon": {"type": "string", "description": "射击所用武器名称或物品栏中的关键字；用于从“武器（N发）”中扣减弹药"},
                    "damage_spec": {"type": "string", "description": "命中后的伤害骰，如 1d3、1d6、1d8+1"},
                    "damage_mode": {"type": "string", "enum": ["normal", "impaling", "blunt"], "description": "极难成功伤害模式；枪弹/刺击用 impaling，钝击用 blunt"},
                    "defender_choice": {"type": "string", "enum": ["dodge", "fight_back", "take_cover", "no_defense"], "description": "仅用于 NPC 防御选择；PC 防御必须留空，由工具向玩家确认"},
                    "bonus_dice": {"type": "integer", "minimum": 0, "maximum": 2},
                    "penalty_dice": {"type": "integer", "minimum": 0, "maximum": 2}
                },
                "required": ["actor_id", "action_type", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "combat_end",
            "description": "敌意解除、撤退成功、投降或剧情明确中止战斗时结束战斗状态。击倒全部敌人或 PC 后状态机会自动结束。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "战斗结束原因"}},
                "required": ["reason"]
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
            "description": "请求结束游戏。若模组定义了结局，必须提供 ending_id；引擎会校验 required_flags，前置条件未满足时拒绝进入结局画面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ending_id": {
                        "type": "string",
                        "description": "world_state.endings 中的稳定结局 ID"
                    },
                    "ending_type": {
                        "type": "string",
                        "enum": ["good", "bad", "neutral", "secret"],
                        "description": "结局类型"
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


# These remain available to the engine and old save histories, but exposing
# them to the story model creates redundant read/display/model round trips.
_ENGINE_ONLY_TOOL_NAMES = {
    "cache_scene",
    "read_file",
    "state_npcs",
    "state_clues",
    "get_private_memory",
    "show_handout",
    "sanity_trigger",
    "sanity_loss",
    "update_private_memory",
}

MODEL_TOOLS = [
    tool
    for tool in TOOLS
    if tool.get("function", {}).get("name") not in _ENGINE_ONLY_TOOL_NAMES
]

_STORY_EXCLUDED_MODEL_TOOLS = {
    "create_character",
    "load_character",
    "combat_status",
    "combat_action",
    "combat_end",
}

_COMBAT_EXCLUDED_MODEL_TOOLS = {
    "create_character",
    "load_character",
    "combat_start",
    "suggest_check",
    "link_clues",
    "set_psychological_trait",
    "get_npc_secret",
}


def model_tools_for(role: str) -> list[dict]:
    """Return a stable, role-specific subset without changing tool execution."""
    excluded = (
        _COMBAT_EXCLUDED_MODEL_TOOLS
        if role == "combat"
        else _STORY_EXCLUDED_MODEL_TOOLS
    )
    return [
        tool
        for tool in MODEL_TOOLS
        if tool.get("function", {}).get("name") not in excluded
    ]

# ---------------------------------------------------------------------------
# 工具分类
# ---------------------------------------------------------------------------

COMPLEX_FUNCTIONS = {
    "skill_check",
    "dice_roll",
    "apply_damage", "apply_heal",
    "combat_start", "combat_action", "combat_end",
    "sanity_event", "sanity_loss", "sanity_restore", "sanity_check",
    "create_character",
    "attribute_check", "luck_check",
    "psychoanalysis", "reality_check",
}


# ---------------------------------------------------------------------------
# CLI 执行器
# ---------------------------------------------------------------------------

def _run_cli(argv: list, context: RuntimeContext) -> str:
    try:
        env = {
            **os.environ,
            **context.child_process_env(),
            "PYTHONIOENCODING": "utf-8",
        }
        # shell=False + argv 列表：经 CreateProcess 直接启动，exe 路径含
        # 空格(Program Files)或中文(疯狂宅邸)时不会被 cmd /c 截断。
        result = subprocess.run(
            [str(a) for a in argv], shell=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=30, cwd=context.project_root, env=env
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


TOOL_RUNTIME = ToolRuntime()


def _register_cli_tool(name: str, argv_builder) -> None:
    def handler(args: dict, context: RuntimeContext) -> str:
        return _run_cli(argv_builder(sys.executable, args), context)

    TOOL_RUNTIME.add(name, handler)


_CLI_TOOL_BUILDERS = {
    "dice_roll": lambda exe, args: [exe, "tools/dice.py", args.get("spec", "d20")],
    "state_get": lambda exe, args: [
        exe, "tools/state_manager.py", "get", args.get("path", "pc.hp")
    ],
    "state_set": lambda exe, args: [
        exe, "tools/state_manager.py", "set",
        args.get("path", ""), args.get("value", ""),
    ],
    "state_npcs": lambda exe, _args: [exe, "tools/state_manager.py", "npcs"],
    "state_clues": lambda exe, _args: [exe, "tools/state_manager.py", "clues"],
    "state_add_item": lambda exe, args: [
        exe, "tools/state_manager.py", "add-item", args.get("item", "")
    ],
    "state_remove_item": lambda exe, args: [
        exe, "tools/state_manager.py", "remove-item", args.get("item", "")
    ],
    "apply_damage": lambda exe, args: [
        exe, "tools/damage.py", "damage", args.get("target", "pc"),
        args.get("amount", 0), args.get("damage_type", "物理"),
    ],
    "apply_heal": lambda exe, args: [
        exe, "tools/damage.py", "heal",
        args.get("target", "pc"), args.get("amount", 0),
    ],
    "sanity_loss": lambda exe, args: [
        exe, "tools/sanity.py", "loss", args.get("severity", "moderate")
    ],
    "sanity_restore": lambda exe, args: [
        exe, "tools/sanity.py", "restore", args.get("amount", 0)
    ],
    "sanity_check": lambda exe, _args: [exe, "tools/sanity.py", "check"],
    "load_character": lambda exe, args: [
        exe, "tools/character.py", "load", args.get("path", "")
    ],
    "use_item": lambda exe, args: [
        exe, "tools/item.py", json.dumps(args, ensure_ascii=False)
    ],
    "combat_start": lambda exe, args: [
        exe, "tools/combat.py", "start", json.dumps(args, ensure_ascii=False)
    ],
    "combat_status": lambda exe, _args: [exe, "tools/combat.py", "status", "{}"],
    "combat_action": lambda exe, args: [
        exe, "tools/combat.py", "action", json.dumps(args, ensure_ascii=False)
    ],
    "combat_decide": lambda exe, args: [
        exe, "tools/combat.py", "decide", json.dumps(args, ensure_ascii=False)
    ],
    "combat_end": lambda exe, args: [
        exe, "tools/combat.py", "end", json.dumps(args, ensure_ascii=False)
    ],
    "attribute_check": lambda exe, args: [
        exe, "tools/skill_check.py", args.get("attribute", "STR"),
        args.get("bonus_dice", 0) or 0, args.get("penalty_dice", 0) or 0,
    ],
    "luck_check": lambda exe, _args: [exe, "tools/skill_check.py", "POW"],
    "psychoanalysis": lambda exe, args: [
        exe, "tools/sanity.py", "psychoanalysis", args.get("target", "pc")
    ],
    "reality_check": lambda exe, _args: [exe, "tools/sanity.py", "reality-check"],
    "set_psychological_trait": lambda exe, args: [
        exe, "tools/state_manager.py", "psych-trait",
        args.get("category", "phobia"), args.get("name", ""),
        args.get("context", ""),
    ],
    "link_clues": lambda exe, args: [
        exe, "tools/state_manager.py", "link-clues", args.get("from_id", ""),
        args.get("to_id", ""), args.get("reasoning", ""),
    ],
    "npc_reveal": lambda exe, args: [
        exe, "tools/state_manager.py", "npc-reveal", args.get("npc_id", ""),
        args.get("tier", 1), args.get("entry_text", ""),
    ],
    "get_npc_secret": lambda exe, args: [
        exe, "tools/state_manager.py", "npc-secret", args.get("npc_id", "")
    ],
    "get_private_memory": lambda exe, _args: [
        exe, "tools/state_manager.py", "private-memory"
    ],
    "update_private_memory": lambda exe, args: [
        exe, "tools/state_manager.py", "private-memory-update",
        args.get("section", ""), args.get("value", ""),
    ],
}

for _tool_name, _argv_builder in _CLI_TOOL_BUILDERS.items():
    _register_cli_tool(_tool_name, _argv_builder)


@TOOL_RUNTIME.handler("skill_check")
def _skill_check(args: dict, context: RuntimeContext) -> str:
    argv = [
        sys.executable,
        "tools/skill_check.py",
        args.get("skill", "spot_hidden"),
        args.get("bonus_dice", 0) or 0,
        args.get("penalty_dice", 0) or 0,
    ]
    if args.get("push", False):
        argv.append("--push")
    return _run_cli(argv, context)


@TOOL_RUNTIME.handler("state_add_clue")
def _state_add_clue(args: dict, context: RuntimeContext) -> str:
    argv = [
        sys.executable,
        "tools/state_manager.py",
        "add-clue",
        args.get("text", ""),
        args.get("category", "investigation"),
    ]
    asset_id = args.get("asset_id", "") or ""
    clue_id = args.get("clue_id", "") or ""
    if asset_id or clue_id:
        argv.extend([asset_id, clue_id])
    return _run_cli(argv, context)


@TOOL_RUNTIME.handler("create_character")
def _create_character(args: dict, context: RuntimeContext) -> str:
    return _run_cli([
        sys.executable,
        "tools/character.py",
        "create",
        args.get("name", "调查员"),
        args.get("occupation", "私家侦探"),
        args.get("violence_stance", "conditional"),
    ], context)


@TOOL_RUNTIME.handler("sanity_trigger")
def _sanity_trigger(args: dict, _context: RuntimeContext) -> str:
    description = args.get("description", "")
    consequence = classify_sanity_consequence(description)
    return json.dumps({
        "suggestion": consequence.severity.value,
        "note": "这是建议的严重度，最终由守秘人根据具体情境决定。确认后调用 sanity_loss(severity=...)",
        "severity_options": {
            SanitySeverity.TRIVIAL.value: "0/1 (几乎无损失)",
            SanitySeverity.MINOR.value: "0/1D4 (轻微不适)",
            SanitySeverity.MODERATE.value: "1/1D6+1 (明显冲击)",
            SanitySeverity.MAJOR.value: "1D4/2D6+2 (严重创伤)",
            SanitySeverity.CATASTROPHIC.value: "1D10/1D100 (终极恐怖)",
        },
    }, ensure_ascii=False)


@TOOL_RUNTIME.handler("sanity_event")
def _sanity_event(args: dict, context: RuntimeContext) -> str:
    trigger = json.loads(TOOL_RUNTIME.execute(
        "sanity_trigger",
        {"description": args.get("description", "")},
        context,
    ))
    loss = json.loads(TOOL_RUNTIME.execute(
        "sanity_loss",
        {"severity": args.get("severity", trigger["suggestion"])},
        context,
    ))
    return json.dumps({
        **loss,
        "description": args.get("description", ""),
        "suggested_severity": trigger.get("suggestion"),
    }, ensure_ascii=False)


@TOOL_RUNTIME.handler("suggest_check")
def _suggest_check(args: dict, _context: RuntimeContext) -> str:
    skill = args.get("skill", "?")
    attribute = args.get("attribute", "?")
    dc = args.get("dc", 15)
    print()
    print(f"  ⚡ 检定提议：{args.get('description', '')}")
    print(
        f"     【{skill}】（{attribute}）— "
        f"难度：{args.get('dc_label', '中等')}（DC {dc}）"
    )
    try:
        answer = input("  → 确定尝试吗？(y/n) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer in ("y", "yes", "是"):
        return json.dumps({
            "confirmed": True,
            "skill": skill,
            "attribute": attribute,
            "dc": dc,
        })
    return json.dumps({"confirmed": False, "reason": "玩家选择不冒险"})


@TOOL_RUNTIME.handler("cache_scene")
def _cache_scene(args: dict, context: RuntimeContext) -> str:
    scene_id = args.get("scene_id", "")
    description = args.get("description", "")
    try:
        def cache(data: dict) -> None:
            data.setdefault("scene_cache", {})[scene_id] = description

        context.world_store.update(cache)
        return json.dumps({"cached": True, "scene_id": scene_id})
    except Exception as exc:
        return json.dumps({"cached": False, "error": str(exc)})


@TOOL_RUNTIME.handler("end_game")
def _end_game(args: dict, context: RuntimeContext) -> str:
    resolution = validate_ending(context.world_store.load(), args)
    if not resolution.get("ok"):
        return json.dumps({"game_over": False, **resolution}, ensure_ascii=False)

    def finish(data: dict) -> None:
        data["game_over"] = {
            "id": resolution.get("ending_id"),
            "type": resolution["ending_type"],
            "title": resolution["title"],
            "summary": resolution["summary"],
        }

    context.world_store.update(finish)
    return json.dumps({
        "ok": True,
        "game_over": True,
        "ending_id": resolution.get("ending_id"),
        "ending_type": resolution["ending_type"],
        "title": resolution["title"],
        "summary": resolution["summary"],
    }, ensure_ascii=False)


@TOOL_RUNTIME.handler("read_file")
def _read_file(args: dict, context: RuntimeContext) -> str:
    path = str(args.get("path") or "").strip()
    if not path:
        return "[错误] 文件路径不能为空"
    normalized_path = path.replace("\\", "/").lstrip("./")
    module_name = context.module_name
    world_aliases = {
        "world://state",
        "world_state.json",
        f"mod/{module_name}/world_state.json",
        f"modules/{module_name}/world_state.json",
    }
    if normalized_path in world_aliases:
        return json.dumps(context.world_store.load(), ensure_ascii=False, indent=2)
    module_aliases = {
        "module.md",
        f"mod/{module_name}/module.md",
        f"modules/{module_name}/module.md",
    }
    full_path = (
        (context.module_dir / "module.md").resolve()
        if normalized_path in module_aliases
        else (context.project_root / path).resolve()
    )
    allowed_roots = (context.project_root.resolve(), context.runtime_root.resolve())
    if not any(_is_relative_to(full_path, root) for root in allowed_roots):
        return "[错误] 不允许读取项目外的文件"
    if not full_path.exists():
        return f"[错误] 文件不存在: {path}"
    if not full_path.is_file():
        return f"[错误] 不能读取目录: {path}"
    try:
        return full_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"[错误] 文件读取失败: {exc}"


@TOOL_RUNTIME.handler("show_handout")
def _show_handout(args: dict, context: RuntimeContext) -> str:
    argv = [
        sys.executable,
        "tools/state_manager.py",
        "show-handout",
        args.get("entity_type", "npc"),
        args.get("entity_id", ""),
    ]
    if args.get("asset_id"):
        argv.append(args["asset_id"])
    result = _run_cli(argv, context)
    try:
        info = json.loads(result)
        if info.get("found") and info.get("file"):
            asset_path = context.assets_dir / info["file"]
            if asset_path.exists():
                mime = mimetypes.guess_type(str(asset_path))[0] or "image/png"
                data = base64.b64encode(asset_path.read_bytes()).decode("ascii")
                info["asset_data_uri"] = f"data:{mime};base64,{data}"
                info["asset_url"] = (
                    f"/api/assets/{context.module_name}/{info['file']}"
                )
                result = json.dumps(info, ensure_ascii=False)
    except (json.JSONDecodeError, OSError, TypeError):
        pass
    return result


def execute_function(
    name: str,
    args: dict,
    *,
    context: RuntimeContext | None = None,
) -> str:
    context = context or RuntimeContext.local()
    try:
        return TOOL_RUNTIME.execute(name, args, context)
    except UnknownToolError:
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
