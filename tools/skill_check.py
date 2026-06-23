#!/usr/bin/env python3
"""TRPG 确定性技能检定工具 —— 属性绑定 + 公式计算 + 掷骰 + DC 比较"""

import json
import random
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "rules" / "rule_schema.json"
STATE_PATH = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_attribute_modifier(attribute_value: int, modifier_table: dict) -> int:
    """根据属性值查表返回修正值"""
    for key, mod in modifier_table.items():
        low, high = map(int, key.split("_"))
        if low <= attribute_value <= high:
            return mod
    return 0


def skill_check(skill_name: str, dc: int, advantage: str | None = None):
    schema = load_json(SCHEMA_PATH)
    state = load_json(STATE_PATH)

    # 查技能 → 属性映射
    skills_map = {s["id"]: s for s in schema["skills"]}
    skill_def = skills_map.get(skill_name)
    if not skill_def:
        result = {"success": False, "error": f"未知技能: {skill_name}"}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    attr_id = skill_def["attribute"]
    attr_defs = {a["id"]: a for a in schema["attributes"]}
    attr_name = attr_defs[attr_id]["name"] if attr_id in attr_defs else attr_id

    # 读 PC 属性值
    pc = state["pc"]
    attr_value = pc["attributes"].get(attr_id, 50)
    skill_value = pc["skills"].get(skill_name, 0)

    # 计算修正
    modifier_table = schema["attribute_modifier_table"]
    attr_mod = get_attribute_modifier(attr_value, modifier_table)
    skill_bonus = skill_value // 10  # 技能值 / 10 向下取整

    # 掷骰
    d20_roll = random.randint(1, 20)
    if advantage == "advantage":
        d20_roll = max(d20_roll, random.randint(1, 20))
    elif advantage == "disadvantage":
        d20_roll = min(d20_roll, random.randint(1, 20))

    total = d20_roll + attr_mod + skill_bonus
    success = total >= dc

    # 暴击 / 大失败
    rule_config_path = PROJECT_ROOT / "rules" / "rule_config.json"
    config = load_json(rule_config_path) if rule_config_path.exists() else {}
    critical_enabled = config.get("critical_success", {}).get("enabled", True)
    fumble_enabled = config.get("fumble", {}).get("enabled", True)

    is_critical = d20_roll == 20 and critical_enabled
    is_fumble = d20_roll == 1 and fumble_enabled

    if is_critical:
        success = True
    if is_fumble:
        success = False

    result = {
        "skill": skill_name,
        "skill_name": skill_def["name"],
        "attribute": attr_id,
        "attribute_name": attr_name,
        "attribute_value": attr_value,
        "attribute_modifier": attr_mod,
        "skill_value": skill_value,
        "skill_bonus": skill_bonus,
        "dc": dc,
        "d20_roll": d20_roll,
        "total": total,
        "success": success,
        "critical": is_critical,
        "fumble": is_fumble,
        "advantage": advantage if advantage else "normal",
    }

    print(json.dumps(result, ensure_ascii=False))

    # 理智：极端结果时提示
    if is_fumble:
        print("!!! 大失败！检定自动失败，将触发严重后果。", file=sys.stderr)
    if is_critical:
        print("!!! 大成功！检定自动成功，将获得额外叙事收益。", file=sys.stderr)


def main():
    if len(sys.argv) < 3:
        print("用法: python skill_check.py <skill_name> <dc> [advantage|disadvantage]")
        print("  skill_name: investigation, spot_hidden, persuasion, stealth, dodge, occult, first_aid, firearms, athletics")
        print("  dc: 5=琐碎, 10=简单, 15=中等, 20=困难, 25=极难, 30=近乎不可能")
        print("示例:")
        print("  python skill_check.py investigation 15")
        print("  python skill_check.py persuasion 20 advantage")
        sys.exit(1)

    skill_name = sys.argv[1]
    dc = int(sys.argv[2])
    advantage = sys.argv[3] if len(sys.argv) > 3 else None

    if advantage and advantage not in ("advantage", "disadvantage"):
        print(f"ERROR: 无效的优势参数 '{advantage}'，可用: advantage, disadvantage", file=sys.stderr)
        sys.exit(1)

    skill_check(skill_name, dc, advantage)


if __name__ == "__main__":
    main()
