#!/usr/bin/env python3
"""TRPG 世界状态管理器 —— 统一读写 world_state.json"""

import json
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.environ.get("TRPG_MODULE", "mansion_of_madness")
STATE_PATH = os.path.join(PROJECT_ROOT, "mod", MODULE, "world_state.json")


def _load():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _resolve_path(data, path):
    """按点分隔路径读取嵌套 dict/列表中的值"""
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, list):
            try:
                idx = int(p)
                current = current[idx]
            except (ValueError, IndexError):
                raise KeyError(f"列表索引 '{p}' 不存在于 {current}")
        elif isinstance(current, dict):
            if p not in current:
                raise KeyError(f"键 '{p}' 不存在于 {list(current.keys())}")
            current = current[p]
        else:
            raise KeyError(f"无法从 {type(current)} 中访问 '{p}'")
    return current


def _set_path(data, path, value):
    """按点分隔路径写入嵌套值"""
    parts = path.split(".")
    current = data
    for p in parts[:-1]:
        if isinstance(current, list):
            idx = int(p)
            current = current[idx]
        else:
            if p not in current:
                current[p] = {}
            current = current[p]
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def cmd_get(path):
    data = _load()
    try:
        result = _resolve_path(data, path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_set(path, value_str):
    data = _load()
    try:
        parsed = json.loads(value_str)
    except json.JSONDecodeError:
        parsed = value_str
    _set_path(data, path, parsed)
    _save(data)
    print(json.dumps({"ok": True, "path": path, "value": parsed}, ensure_ascii=False))


def cmd_list_npcs():
    data = _load()
    npcs = data.get("npcs", [])
    for i, npc in enumerate(npcs):
        revealed = npc.get("revealed", {})
        rlevel = revealed.get("level", 0)
        rentries = len(revealed.get("entries", []))
        level_label = {0: "未揭示", 1: "表层观察", 2: "部分推断", 3: "完全揭露"}.get(rlevel, "未知")
        extra = f" — 揭示: Lv.{rlevel}({level_label}), {rentries}条记录" if rentries > 0 else ""
        print(f"[{i}] {npc['name']} — tags: {', '.join(npc.get('visible_tags', []))}{extra}")


CLUE_CATEGORIES = ["investigation", "event", "task", "npc"]

CATEGORY_NAMES = {
    "investigation": "探案线索",
    "event": "事件线索",
    "task": "任务线索",
    "npc": "人物线索",
}


def cmd_list_clues():
    data = _load()
    clues = data.get("clues_found", {})
    if isinstance(clues, list):
        # 兼容旧格式
        if not clues:
            print("（尚未发现任何线索）")
        else:
            for c in clues:
                print(f"• {c}")
        return

    total = sum(len(v) for v in clues.values())
    if total == 0:
        print("（尚未发现任何线索）")
        return

    for cat in CLUE_CATEGORIES:
        items = clues.get(cat, [])
        if items:
            print(f"\n【{CATEGORY_NAMES.get(cat, cat)}】")
            for i, c in enumerate(items):
                print(f"  {i+1}. {c['text']}")


def cmd_add_clue(text, category="investigation"):
    if category not in CLUE_CATEGORIES:
        category = "investigation"
    data = _load()
    c = data.setdefault("clues_found", {})
    # 兼容旧格式（数组 → 字典）
    if isinstance(c, list):
        old = c
        c = {k: [] for k in CLUE_CATEGORIES}
        for item in old:
            c["investigation"].append({"text": item} if isinstance(item, str) else item)
    c.setdefault(category, []).append({"text": text})
    data["clues_found"] = c
    _save(data)
    print(f"[{CATEGORY_NAMES.get(category, category)}] {text}")


def cmd_add_item(item_name):
    data = _load()
    inv = data.setdefault("pc", {}).setdefault("inventory", [])
    inv.append(item_name)
    _save(data)
    print(f"物品已添加: {item_name}")


def cmd_remove_item(item_name):
    data = _load()
    inv = data.get("pc", {}).get("inventory", [])
    if item_name in inv:
        inv.remove(item_name)
        _save(data)
        print(f"物品已移除: {item_name}")
    else:
        print(f"物品不存在: {item_name}", file=sys.stderr)


def cmd_npc_reveal(npc_id, tier, entry_text):
    """记录 NPC 信息揭示。tier: 1=表层观察, 2=推断, 3=完全揭露"""
    data = _load()
    npcs = data.get("npcs", [])
    tier_int = int(tier)
    found = False
    for npc in npcs:
        if npc.get("id") == npc_id:
            revealed = npc.setdefault("revealed", {"level": 0, "entries": []})
            revealed["entries"].append({"tier": tier_int, "text": entry_text})
            # 自动升级 level 到最高已揭示 tier
            max_tier = max(e["tier"] for e in revealed["entries"])
            revealed["level"] = max_tier
            found = True
            _save(data)
            print(json.dumps({
                "ok": True,
                "npc_id": npc_id,
                "npc_name": npc["name"],
                "revealed_level": revealed["level"],
                "new_entry": {"tier": tier_int, "text": entry_text}
            }, ensure_ascii=False))
            break
    if not found:
        print(f"ERROR: NPC '{npc_id}' 不存在", file=sys.stderr)
        sys.exit(1)


def cmd_npc_secret(npc_id):
    """获取 NPC 完整秘密（仅守秘人使用）"""
    data = _load()
    npcs = data.get("npcs", [])
    for npc in npcs:
        if npc.get("id") == npc_id:
            revealed = npc.get("revealed", {"level": 0, "entries": []})
            print(json.dumps({
                "npc_id": npc_id,
                "name": npc["name"],
                "visible_tags": npc.get("visible_tags", []),
                "secret": npc.get("secret", ""),
                "disposition": npc.get("disposition", ""),
                "revealed_level": revealed.get("level", 0),
                "revealed_entries": revealed.get("entries", [])
            }, ensure_ascii=False, indent=2))
            return
    print(f"ERROR: NPC '{npc_id}' 不存在", file=sys.stderr)
    sys.exit(1)


def cmd_private_memory():
    """读取私有工作记忆"""
    data = _load()
    pm = data.get("private_memory", {})
    print(json.dumps(pm, ensure_ascii=False, indent=2))


def cmd_private_memory_update(section, value_str):
    """更新私有工作记忆的指定字段"""
    data = _load()
    pm = data.setdefault("private_memory", {})
    try:
        parsed = json.loads(value_str)
    except json.JSONDecodeError:
        parsed = value_str
    pm[section] = parsed
    _save(data)
    print(json.dumps({"ok": True, "section": section, "updated": True}, ensure_ascii=False))


def cmd_psychological_trait(category, name, context=""):
    """添加或覆盖心理特质（恐惧症/躁狂症/性格特质/重要关系）"""
    data = _load()
    pc = data.setdefault("pc", {})
    profile = pc.setdefault("psychological_profile", {
        "traits": [], "key_relationships": [],
        "phobias": [], "manias": []
    })

    if category == "phobia":
        entry = {"name": name, "acquired_from": context or "madness_bout"}
        profile["phobias"].append(entry)
    elif category == "mania":
        entry = {"name": name, "acquired_from": context or "madness_bout"}
        profile["manias"].append(entry)
    elif category == "trait":
        profile["traits"].append(name)
    elif category == "relationship":
        profile["key_relationships"].append({"name": name, "context": context or ""})
    else:
        print(f"ERROR: 未知分类 '{category}'。可选: phobia, mania, trait, relationship", file=sys.stderr)
        sys.exit(1)

    _save(data)
    print(json.dumps({
        "ok": True,
        "category": category,
        "name": name,
        "psychological_profile": profile
    }, ensure_ascii=False, indent=2))


def cmd_usage():
    print("用法:")
    print("  python state_manager.py get <json_path>        读取字段（如 pc.hp, npcs.0.name）")
    print("  python state_manager.py set <json_path> <val>  修改字段（值用 JSON 格式）")
    print("  python state_manager.py npcs                   列出所有 NPC（含揭示程度）")
    print("  python state_manager.py clues                  列出已发现线索")
    print("  python state_manager.py add-clue <text> [category]  添加线索")
    print("        category: investigation/event/task/npc，默认 investigation")
    print("  python state_manager.py add-item <name>        添加物品到背包")
    print("  python state_manager.py remove-item <name>     从背包移除物品")
    print("  python state_manager.py npc-reveal <id> <tier> <text>  记录NPC信息揭示")
    print("        tier: 1=表层观察, 2=推断, 3=完全揭露")
    print("  python state_manager.py npc-secret <id>        获取NPC完整秘密（守秘人专用）")
    print("  python state_manager.py private-memory         读取私有工作记忆")
    print("  python state_manager.py private-memory-update <section> <json>  更新私有记忆")


COMMANDS = {
    "get": cmd_get,
    "set": cmd_set,
    "npcs": lambda _=None: cmd_list_npcs(),
    "clues": lambda _=None: cmd_list_clues(),
    "add-clue": cmd_add_clue,
    "add-item": cmd_add_item,
    "remove-item": cmd_remove_item,
    "npc-reveal": cmd_npc_reveal,
    "npc-secret": cmd_npc_secret,
    "private-memory": lambda _=None: cmd_private_memory(),
    "private-memory-update": cmd_private_memory_update,
    "psych-trait": cmd_psychological_trait,
}


def main():
    if len(sys.argv) < 2:
        cmd_usage()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "get":
        if len(sys.argv) < 3:
            print("ERROR: get 需要一个 <json_path> 参数", file=sys.stderr)
            sys.exit(1)
        cmd_get(sys.argv[2])
    elif cmd == "set":
        if len(sys.argv) < 4:
            print("ERROR: set 需要 <json_path> 和 <value> 两个参数", file=sys.stderr)
            sys.exit(1)
        cmd_set(sys.argv[2], sys.argv[3])
    elif cmd == "add-clue":
        if len(sys.argv) < 3:
            print("ERROR: add-clue 需要 <text> [category] 参数", file=sys.stderr)
            sys.exit(1)
        cat = sys.argv[3] if len(sys.argv) > 3 else "investigation"
        cmd_add_clue(sys.argv[2], cat)
    elif cmd == "npcs":
        cmd_list_npcs()
    elif cmd == "clues":
        cmd_list_clues()
    elif cmd == "add-item":
        if len(sys.argv) < 3:
            print("ERROR: add-item 需要 <name>", file=sys.stderr)
            sys.exit(1)
        cmd_add_item(sys.argv[2])
    elif cmd == "remove-item":
        if len(sys.argv) < 3:
            print("ERROR: remove-item 需要 <name>", file=sys.stderr)
            sys.exit(1)
        cmd_remove_item(sys.argv[2])
    elif cmd == "npc-reveal":
        if len(sys.argv) < 5:
            print("ERROR: npc-reveal 需要 <npc_id> <tier> <text>", file=sys.stderr)
            sys.exit(1)
        cmd_npc_reveal(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "npc-secret":
        if len(sys.argv) < 3:
            print("ERROR: npc-secret 需要 <npc_id>", file=sys.stderr)
            sys.exit(1)
        cmd_npc_secret(sys.argv[2])
    elif cmd == "private-memory":
        cmd_private_memory()
    elif cmd == "private-memory-update":
        if len(sys.argv) < 4:
            print("ERROR: private-memory-update 需要 <section> <json_value>", file=sys.stderr)
            sys.exit(1)
        cmd_private_memory_update(sys.argv[2], sys.argv[3])
    elif cmd == "psych-trait":
        if len(sys.argv) < 4:
            print("ERROR: psych-trait 需要 <category> <name> [context]", file=sys.stderr)
            sys.exit(1)
        ctx = sys.argv[4] if len(sys.argv) > 4 else ""
        cmd_psychological_trait(sys.argv[2], sys.argv[3], ctx)
    else:
        print(f"ERROR: 未知命令 '{cmd}'", file=sys.stderr)
        cmd_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
