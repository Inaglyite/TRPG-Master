#!/usr/bin/env python3
"""COC 第七版角色卡创建工具 —— 掷属性、分配技能、导出 JSON 角色卡"""

import json
import random
import sys
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"
CHARS_DIR = PROJECT_ROOT / "characters"

# ── COC 7e 职业库 ──────────────────────────────────────────

OCCUPATIONS = {
    "私家侦探": {
        "skill_points": "EDU*2 + DEX*2",
        "credit_range": [9, 30],
        "skills": ["spot_hidden", "library_use", "law", "photography", "psychology",
                   "stealth", "firearms_handgun", "drive_auto", "fast_talk", "locksmith",
                   "fighting_brawl", "navigate", "first_aid"],
        "equipment": ["笔记本与钢笔", "手电筒", "怀表", ".38口径左轮手枪（6发）"]
    },
    "记者": {
        "skill_points": "EDU*2 + APP*2",
        "credit_range": [9, 30],
        "skills": ["library_use", "fast_talk", "charm", "psychology", "spot_hidden",
                   "photography", "language_own", "history", "law", "stealth",
                   "drive_auto", "listen"],
        "equipment": ["记者证", "笔记本与钢笔", "柯达折叠相机", "手电筒"]
    },
    "医生": {
        "skill_points": "EDU*2 + DEX*2",
        "credit_range": [30, 80],
        "skills": ["first_aid", "medicine", "psychology", "science_biology", "spot_hidden",
                   "library_use", "persuade", "psychoanalysis", "pharmacy",
                   "language_latin", "credit_rating"],
        "equipment": ["医疗包", "听诊器", "处方笺", "手电筒"]
    },
    "教授": {
        "skill_points": "EDU*4",
        "credit_range": [20, 70],
        "skills": ["library_use", "history", "language_own", "occult", "psychology",
                   "archaeology", "anthropology", "credit_rating", "law",
                   "navigate", "science", "spot_hidden"],
        "equipment": ["教职徽章", "学术笔记本", "放大镜", "钢笔"]
    },
    "古董商": {
        "skill_points": "EDU*2 + APP*2",
        "credit_range": [30, 80],
        "skills": ["appraise", "history", "fast_talk", "spot_hidden", "library_use",
                   "occult", "credit_rating", "charm", "archaeology",
                   "locksmith", "drive_auto", "law"],
        "equipment": ["放大镜", "古董鉴定手册", "名片夹", "手电筒"]
    },
    "警察": {
        "skill_points": "EDU*2 + DEX*2",
        "credit_range": [9, 30],
        "skills": ["firearms_handgun", "fighting_brawl", "dodge", "law", "fast_talk",
                   "intimidate", "drive_auto", "first_aid", "spot_hidden",
                   "stealth", "psychology"],
        "equipment": [".38警用左轮手枪（6发）", "警徽", "手铐", "手电筒"]
    },
    "牧师": {
        "skill_points": "EDU*2 + POW*2",
        "credit_range": [9, 60],
        "skills": ["occult", "psychology", "persuade", "library_use", "psychoanalysis",
                   "history", "language_latin", "charm", "credit_rating",
                   "listen", "first_aid"],
        "equipment": ["圣经", "圣水", "十字架", "笔记本"]
    },
    "作家": {
        "skill_points": "EDU*2 + INT*2",
        "credit_range": [9, 40],
        "skills": ["language_own", "history", "library_use", "psychology", "occult",
                   "spot_hidden", "fast_talk", "credit_rating", "law",
                   "listen", "drive_auto"],
        "equipment": ["打字机", "笔记本与钢笔", "手电筒", "一大叠稿纸"]
    },
    "神秘学家": {
        "skill_points": "EDU*2 + POW*2",
        "credit_range": [5, 30],
        "skills": ["occult", "library_use", "cthulhu_mythos", "history", "psychology",
                   "archaeology", "anthropology", "spot_hidden", "persuade",
                   "language_latin", "appraise"],
        "equipment": ["泛黄的仪式书", "银质符咒", "蜡烛", "笔记本与炭笔"]
    }
}

# ── 创建角色 ──────────────────────────────────────────────

def roll_attribute(dice_formula: str) -> int:
    """掷属性: 3D6×5, 2D6+6×5 等"""
    # Normalize: uppercase→lowercase, ×→*
    formula = dice_formula.upper().replace("×", "*").replace("X", "*")
    multiplier = 5 if "*5" in formula else 1
    formula = formula.replace("*5", "").strip()

    add = 0
    if "+" in formula:
        dice_part, add_part = formula.split("+", 1)
        add = int(add_part.strip())
    else:
        dice_part = formula

    if "D" in dice_part:
        count_str, sides_str = dice_part.split("D", 1)
        count = int(count_str) if count_str else 1
        sides = int(sides_str)
    else:
        count, sides = 1, 6

    total = sum(random.randint(1, sides) for _ in range(count)) + add
    return total * multiplier


def calc_derived(attrs: dict) -> dict:
    """计算衍生属性"""
    return {
        "HP": (attrs["SIZ"] + attrs["CON"]) // 10,
        "max_HP": (attrs["SIZ"] + attrs["CON"]) // 10,
        "SAN": attrs["POW"],
        "max_SAN": attrs["POW"],
        "MP": attrs["POW"] // 5,
        "MOV": 8,
        "DB": _damage_bonus(attrs["STR"] + attrs["SIZ"]),
        "BUILD": _build(attrs["STR"] + attrs["SIZ"]),
        "LUCK": sum(random.randint(1, 6) for _ in range(3)) * 5,
    }


def _damage_bonus(total: int) -> str:
    if total < 65: return "-2"
    if total < 85: return "-1"
    if total < 125: return "0"
    if total < 165: return "+1D4"
    return "+1D6"


def _build(total: int) -> int:
    if total < 65: return -2
    if total < 85: return -1
    if total < 125: return 0
    if total < 165: return 1
    return 2


def parse_skill_points(formula: str, attrs: dict) -> int:
    """解析职业技能点公式: EDU*2 + DEX*2 → int"""
    total = 0
    for part in formula.upper().split("+"):
        part = part.strip()
        if "*" in part:
            attr_name, mul = part.split("*")
            attr_name = attr_name.strip()
            mul = int(mul.strip())
            total += attrs.get(attr_name, 50) * mul
        else:
            total += attrs.get(part.strip(), 50)
    return total


def create_character(name: str, occupation: str, quick: bool = False) -> dict:
    """创建 COC 7e 角色卡"""
    occ = OCCUPATIONS.get(occupation)
    if not occ:
        available = ", ".join(OCCUPATIONS.keys())
        return {"error": f"未知职业'{occupation}'，可用: {available}"}

    # 掷属性
    attr_formulas = {
        "STR": "3D6*5", "DEX": "3D6*5", "CON": "3D6*5",
        "INT": "2D6+6*5", "POW": "3D6*5",
        "SIZ": "2D6+6*5", "APP": "3D6*5", "EDU": "2D6+6*5"
    }

    if quick:
        # 快速创建：从预设值池分配
        pool = [80, 70, 60, 60, 50, 50, 50, 40]
        random.shuffle(pool)
        attrs = {k: pool[i] for i, k in enumerate(attr_formulas.keys())}
    else:
        attrs = {k: roll_attribute(v) for k, v in attr_formulas.items()}

    derived = calc_derived(attrs)

    # 职业技能点
    occ_skill_points = parse_skill_points(occ["skill_points"], attrs)
    interest_points = attrs["INT"] * 2  # 兴趣技能点

    # 信用评级范围
    cr_min, cr_max = occ["credit_range"]
    cr = random.randint(cr_min, cr_max)

    # 分配技能
    skills = {}
    # 基础值（部分技能有初始值）
    skill_bases = {
        "dodge": attrs["DEX"] // 2,
        "language_own": attrs["EDU"],
        "fighting_brawl": 25,
        "firearms_handgun": 20,
        "firearms_rifle": 25,
        "spot_hidden": 25,
        "listen": 20,
        "stealth": 20,
        "library_use": 20,
        "first_aid": 30,
        "psychology": 10,
        "persuade": 10,
        "charm": 15,
        "fast_talk": 5,
        "intimidate": 15,
        "climb": 20,
        "jump": 20,
        "swim": 20,
        "throw": 20,
        "occult": 5,
        "history": 5,
        "law": 5,
        "locksmith": 1,
        "navigate": 10,
        "drive_auto": 20,
        "ride": 5,
        "appraise": 5,
        "archaeology": 1,
        "anthropology": 1,
        "disguise": 5,
        "science": 1,
        "medicine": 1,
        "natural_world": 10,
        "track": 10,
        "survival": 10,
        "psychoanalysis": 1,
        "sleight_of_hand": 10,
        "mech_repair": 10,
        "elec_repair": 10,
        "op_hv_machine": 1,
        "accounting": 5,
        "pilot": 1,
        "arts_craft": 5,
        "language_latin": 1,
        "credit_rating": 0
    }

    for sk_id, base in skill_bases.items():
        skills[sk_id] = base

    # 选取 8 项职业技能
    occ_skills = occ["skills"][:8]
    remaining_points = occ_skill_points

    # 给职业技能分配点数
    skill_assignments = {}
    for sk_id in occ_skills:
        base = skills.get(sk_id, 0)
        # 掷 d10 决定给多少点
        allocation = min(random.randint(15, 35), remaining_points // (len(occ_skills)))
        skill_assignments[sk_id] = base + allocation
        remaining_points -= allocation

    # 剩余职业技能点随机分配
    while remaining_points > 0 and occ_skills:
        sk = random.choice(occ_skills)
        bonus = min(10, remaining_points)
        skill_assignments.setdefault(sk, skills.get(sk, 0))
        skill_assignments[sk] += bonus
        remaining_points -= bonus

    # 兴趣技能点
    int_skills = [s for s in skills if s not in occ_skills and s != "credit_rating"
                  and s != "cthulhu_mythos"]
    random.shuffle(int_skills)
    int_assign = random.sample(int_skills, min(4, len(int_skills)))
    for sk in int_assign:
        bonus = random.randint(10, 20)
        skill_assignments.setdefault(sk, skills.get(sk, 0))
        skill_assignments[sk] += bonus

    # 合并技能
    for sk_id, val in skill_assignments.items():
        skills[sk_id] = min(val, 99)
    skills["credit_rating"] = cr
    skills["cthulhu_mythos"] = 0  # 克苏鲁神话初始为 0

    character = {
        "name": name,
        "occupation": occupation,
        "age": 34,
        "created_at": datetime.now().isoformat(),
        "attributes": attrs,
        "derived": derived,
        "skills": skills,
        "credit_rating": cr,
        "inventory": occ.get("equipment", ["手电筒", "笔记本"]),
        "backstory": {
            "description": "",
            "beliefs": "",
            "important_person": "",
            "meaningful_place": "",
            "treasured_possession": "",
            "traits": "",
            "key_connection": ""
        }
    }

    return character


# ── 保存 / 加载 ────────────────────────────────────────────

def save_character(char: dict, filename: str | None = None) -> str:
    """保存角色卡为 JSON 文件"""
    CHARS_DIR.mkdir(parents=True, exist_ok=True)
    if filename is None:
        name_safe = "".join(c for c in char["name"] if c.isalnum() or c in "_ ")
        filename = f"{name_safe.strip()}_{char['created_at'][:10]}.json"
    filepath = CHARS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(char, f, ensure_ascii=False, indent=2)
    return str(filepath)


def load_character(path: str) -> dict | None:
    """从 JSON 文件加载角色卡"""
    full_path = Path(path)
    if not full_path.is_absolute():
        full_path = CHARS_DIR / path
    if not full_path.exists():
        return None
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_character(char: dict) -> dict:
    """将角色卡应用到当前世界状态"""
    state_path = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    pc = state["pc"]
    pc["name"] = char["name"]
    pc["occupation"] = char["occupation"]
    pc["attributes"] = char["attributes"]
    pc["skills"] = char["skills"]
    derived = char.get("derived", {})
    pc["hp"] = derived.get("HP", pc.get("hp", 11))
    pc["max_hp"] = derived.get("max_HP", pc.get("max_hp", 11))
    pc["san"] = derived.get("SAN", pc.get("san", 65))
    pc["max_san"] = derived.get("max_SAN", pc.get("max_san", 65))
    pc["inventory"] = char.get("inventory", pc.get("inventory", []))
    pc["credit_rating"] = char.get("credit_rating", 30)

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    return {"applied": True, "name": char["name"], "occupation": char["occupation"],
            "hp": pc["hp"], "san": pc["san"]}


# ── CLI ─────────────────────────────────────────────────────

def console():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python character.py create <名字> <职业>   创建新角色")
        print("  python character.py quick <名字> <职业>   快速创建（预设属性池）")
        print("  python character.py save <文件路径>       保存当前角色")
        print("  python character.py load <文件路径>       加载角色卡")
        print("  python character.py apply <文件路径>      加载角色卡并应用到游戏")
        print("  python character.py list                  列出所有职业")
        print("")
        print(f"角色卡保存在 {CHARS_DIR}/")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create" or cmd == "quick":
        if len(sys.argv) < 4:
            print("ERROR: 需要 <名字> 和 <职业>", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[2]
        occupation = sys.argv[3]
        char = create_character(name, occupation, quick=(cmd == "quick"))
        if "error" in char:
            print(json.dumps(char, ensure_ascii=False))
            sys.exit(1)
        path = save_character(char)
        result = {"created": True, "name": name, "occupation": occupation,
                  "path": path, "attributes": char["attributes"],
                  "hp": char["derived"]["HP"], "san": char["derived"]["SAN"],
                  "key_skills": {k: v for k, v in sorted(char["skills"].items(),
                                  key=lambda x: -x[1])[:8] if v > 30}}
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "load":
        if len(sys.argv) < 3:
            print("ERROR: 需要 <文件路径>", file=sys.stderr)
            sys.exit(1)
        char = load_character(sys.argv[2])
        if char is None:
            print(json.dumps({"error": "文件不存在"}, ensure_ascii=False))
            sys.exit(1)
        print(json.dumps(char, ensure_ascii=False, indent=2))

    elif cmd == "apply":
        if len(sys.argv) < 3:
            print("ERROR: 需要 <文件路径>", file=sys.stderr)
            sys.exit(1)
        char = load_character(sys.argv[2])
        if char is None:
            print(json.dumps({"error": "文件不存在"}, ensure_ascii=False))
            sys.exit(1)
        result = apply_character(char)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "list":
        print("可用职业:")
        for occ_name, info in OCCUPATIONS.items():
            print(f"  {occ_name} — 技能点数: {info['skill_points']}, "
                  f"信用评级: {info['credit_range'][0]}-{info['credit_range'][1]}")
            print(f"    职业技能: {', '.join(info['skills'][:6])}...")

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    console()
