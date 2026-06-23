#!/usr/bin/env python3
"""TRPG 技能检定工具 —— COC 第七版 d100 roll-under

d100 ≤ 技能值 = 常规成功
d100 ≤ 技能值/2 = 困难成功
d100 ≤ 技能值/5 = 极难成功
01 = 大成功, 100 = 大失败
"""

import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "rules" / "rule_schema.json"
STATE_PATH = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"
CONFIG_PATH = PROJECT_ROOT / "rules" / "rule_config.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def success_level(d100_roll: int, skill_value: int, bonus_dice: int = 0,
                  penalty_dice: int = 0, is_push: bool = False) -> dict:
    """执行 d100 roll-under 检定，返回完整结果。

    Args:
        d100_roll: 已掷出的 d100 值（用于测试重现），None 则随机
        skill_value: 技能值 (0-99)
        bonus_dice: 奖励骰数量
        penalty_dice: 惩罚骰数量
        is_push: 是否为孤注一掷
    """
    # 掷 d100（个位骰 + 十位骰）
    tens = random.randint(0, 9)
    ones = random.randint(0, 9)

    # 奖惩骰：额外十位骰
    extra_tens = []
    net_penalty = penalty_dice - bonus_dice  # 正数=惩罚，负数=奖励
    if net_penalty != 0:
        for _ in range(abs(net_penalty)):
            extra_tens.append(random.randint(0, 9))

    # 取最优/最劣十位
    if net_penalty < 0:  # 奖励骰：取数值最小的十位（最接近 00 即成功）
        tens = min([tens] + extra_tens)
    elif net_penalty > 0:  # 惩罚骰：取数值最大的十位（最接近 100 即失败）
        tens = max([tens] + extra_tens)

    # 计算 d100
    if tens == 0 and ones == 0:
        d100 = 100  # 00 + 0 = 100
    else:
        d100 = tens * 10 + ones
        if d100 == 0:
            d100 = 100

    # 判定成功等级
    if d100 <= 1:
        level = "critical_success"
    elif d100 <= max(1, skill_value // 5):
        level = "extreme_success"
    elif d100 <= max(1, skill_value // 2):
        level = "hard_success"
    elif d100 <= skill_value:
        level = "regular_success"
    elif skill_value < 50 and d100 >= 96:
        level = "fumble"
    elif d100 >= 100:
        level = "fumble"
    else:
        level = "failure"

    # 读技能定义
    try:
        schema = load_json(SCHEMA_PATH)
        skills_map = {s["id"]: s for s in schema.get("skills", [])}
        skill_name = skills_map.get(skill_name_global, {}).get("name", skill_name_global)
    except Exception:
        skill_name = skill_name_global

    success = level in ("critical_success", "extreme_success", "hard_success", "regular_success")

    result = {
        "skill": skill_name_global,
        "skill_name": skill_name,
        "skill_value": skill_value,
        "d100_roll": d100,
        "tens_dice": [tens] + extra_tens,
        "ones_dice": ones,
        "bonus_dice": bonus_dice,
        "penalty_dice": penalty_dice,
        "difficulty_regular": skill_value,
        "difficulty_hard": max(1, skill_value // 2),
        "difficulty_extreme": max(1, skill_value // 5),
        "level": level,
        "success": success,
        "is_push": is_push,
    }

    print_json(result)

    if level == "fumble":
        print("!!! 大失败！后果极其严重。", file=sys.stderr)
    elif level == "critical_success":
        print("!!! 大成功！额外收益。", file=sys.stderr)

    return result


skill_name_global = ""


def console_check():
    """命令行模式"""
    if len(sys.argv) < 2:
        print("用法: python skill_check.py <skill_id> [bonus_dice] [penalty_dice] [--push]")
        print("  skill_id: spot_hidden, persuade, fighting_brawl, dodge, firearms_handgun, ...")
        print("  bonus_dice: 奖励骰数量（默认 0）")
        print("  penalty_dice: 惩罚骰数量（默认 0）")
        print("  --push: 标记为孤注一掷")
        print("示例:")
        print("  python skill_check.py spot_hidden")
        print("  python skill_check.py persuade 1 0         # 1个奖励骰")
        print("  python skill_check.py dodge 0 1             # 1个惩罚骰")
        print("  python skill_check.py library_use 0 0 --push # 孤注一掷")
        sys.exit(1)

    global skill_name_global
    skill_name_global = sys.argv[1]
    bonus = 0
    penalty = 0
    is_push = False

    for i, arg in enumerate(sys.argv[2:], start=2):
        if arg == "--push":
            is_push = True
        elif i == 2:
            bonus = int(arg)
        elif i == 3:
            penalty = int(arg)

    state = load_json(STATE_PATH)
    skill_value = state["pc"]["skills"].get(skill_name_global, 50)

    success_level(0, skill_value, bonus, penalty, is_push)


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False))


if __name__ == "__main__":
    console_check()
