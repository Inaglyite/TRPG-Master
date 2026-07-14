#!/usr/bin/env python3
"""TRPG 世界状态管理器 —— 统一读写 world_state.json"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import RuntimeContext  # noqa: E402
from src.handouts import (  # noqa: E402
    attach_matching_clue_asset,
    resolve_handout_asset,
)


CONTEXT = RuntimeContext.from_env()
STORE = CONTEXT.world_store
_TRANSACTION_STATE = None


def _load():
    if _TRANSACTION_STATE is not None:
        return _TRANSACTION_STATE
    return STORE.load()


def _save(data):
    if _TRANSACTION_STATE is not None:
        if data is not _TRANSACTION_STATE:
            _TRANSACTION_STATE.clear()
            _TRANSACTION_STATE.update(data)
        return
    STORE.restore(data)


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
                cid = c.get("id", "")
                ctype = c.get("type", "")
                asset = c.get("asset")
                prefix = ""
                if ctype == "hidden":
                    prefix = "[隐秘] "
                elif ctype == "inferred":
                    prefix = "[推理] "
                extra = f" ({cid})" if cid else ""
                print(f"  {i+1}. {prefix}{c['text']}{extra}")
                if asset and asset.get("file"):
                    print(f"     📎 {asset['file']}")


def _clue_counter(data: dict) -> int:
    """返回下一个线索 ID 编号。"""
    max_id = 0
    for cat in CLUE_CATEGORIES:
        for item in data.get("clues_found", {}).get(cat, []):
            cid = item.get("id", "")
            if cid.startswith("clue_"):
                try:
                    max_id = max(max_id, int(cid.split("_")[1]))
                except ValueError:
                    pass
    return max_id + 1


def _migrate_old_clue_format(data: dict):
    """将旧版扁平线索格式迁移到新版结构化格式。"""
    clues = data.get("clues_found", {})
    if isinstance(clues, list):
        # 数组 → 字典
        old = clues
        clues = {k: [] for k in CLUE_CATEGORIES}
        for item in old:
            if isinstance(item, str):
                clues["investigation"].append({"text": item})
            else:
                clues.setdefault("investigation", []).append(item)
        data["clues_found"] = clues
    # 给没有 id 的线索补上
    next_num = _clue_counter(data)
    for cat in CLUE_CATEGORIES:
        for item in clues.get(cat, []):
            if "id" not in item or not item["id"]:
                item["id"] = f"clue_{next_num:03d}"
                next_num += 1
            if "type" not in item:
                item["type"] = "obvious"
            if "tier" not in item:
                item["tier"] = 1
            if "source" not in item:
                item["source"] = None
            if "related_npcs" not in item:
                item["related_npcs"] = []
            if "related_scenes" not in item:
                item["related_scenes"] = []
            if "discovered_at" not in item:
                item["discovered_at"] = None
            if "asset" not in item:
                item["asset"] = None
    data.setdefault("clue_links", [])


def _find_clue_by_asset(data: dict, *, asset_id: str | None = None,
                        asset_file: str | None = None):
    """返回已使用同一线索资产的 clue，避免一张图挂到多条线索上。"""
    for cat in CLUE_CATEGORIES:
        for item in data.get("clues_found", {}).get(cat, []):
            asset = item.get("asset") or {}
            if asset_id and asset.get("id") == asset_id:
                return item
            if asset_file and asset.get("file") == asset_file:
                return item
    return None


def _find_clue_by_id(data: dict, clue_id: str):
    for cat in CLUE_CATEGORIES:
        for item in data.get("clues_found", {}).get(cat, []):
            if item.get("id") == clue_id:
                return item
    return None


def _apply_clue_flag_effects(data: dict, catalog_entry: dict | None) -> dict:
    if not isinstance(catalog_entry, dict):
        return {}
    effects = catalog_entry.get("flag_effects", {})
    flags = data.get("flags", {})
    if not isinstance(effects, dict) or not isinstance(flags, dict):
        return {}
    applied = {}
    for key, value in effects.items():
        if key not in flags or isinstance(value, (dict, list)):
            continue
        if flags.get(key) != value:
            flags[key] = value
            applied[key] = value
    return applied


def cmd_add_clue(text, category="investigation", clue_type="obvious", tier=1,
                  source=None, related_npcs="", related_scenes="", asset_file=None,
                  asset_id=None, clue_id=None):
    """添加线索。向后兼容旧版纯 text 调用。"""
    data = _load()
    _migrate_old_clue_format(data)
    catalog = data.get("clue_catalog", {})
    catalog_entry = catalog.get(clue_id) if clue_id and isinstance(catalog, dict) else None
    if clue_id:
        existing_clue = _find_clue_by_id(data, clue_id)
        if existing_clue:
            result = {
                "ok": True,
                "duplicate": True,
                "clue": existing_clue,
            }
            granted_item = (
                catalog_entry.get("granted_item")
                if isinstance(catalog_entry, dict)
                else None
            )
            changed = False
            if granted_item:
                inventory = data.setdefault("pc", {}).setdefault("inventory", [])
                item_added = granted_item not in inventory
                if item_added:
                    inventory.append(granted_item)
                    changed = True
                result["granted_item"] = granted_item
                result["item_added"] = item_added
            flag_effects = _apply_clue_flag_effects(data, catalog_entry)
            if flag_effects:
                result["flag_effects"] = flag_effects
                changed = True
            if changed:
                _save(data)
            print(json.dumps(result, ensure_ascii=False))
            return
    if isinstance(catalog_entry, dict):
        category = catalog_entry.get("category", category)
        clue_type = catalog_entry.get("type", clue_type)
        tier = catalog_entry.get("tier", tier)
        source = source or catalog_entry.get("source")
        text = text or catalog_entry.get("text", "")
    if category not in CLUE_CATEGORIES:
        category = "investigation"

    next_id = _clue_counter(data)
    asset = None
    skipped_asset = None
    if asset_id:
        mapped = data.get("asset_map", {}).get("clues", {}).get(asset_id)
        existing = _find_clue_by_asset(data, asset_id=asset_id)
        if existing:
            skipped_asset = {
                "id": asset_id,
                "reason": "asset_already_attached",
                "existing_clue_id": existing.get("id"),
            }
        elif mapped:
            asset = {
                "id": asset_id,
                "file": mapped.get("file"),
                "label": mapped.get("label", text[:80])
            }
    elif asset_file:
        existing = _find_clue_by_asset(data, asset_file=asset_file)
        if existing:
            skipped_asset = {
                "file": asset_file,
                "reason": "asset_already_attached",
                "existing_clue_id": existing.get("id"),
            }
        else:
            asset = {"file": asset_file, "label": text[:80]}
    elif isinstance(catalog_entry, dict):
        catalog_asset = catalog_entry.get("asset")
        if isinstance(catalog_asset, dict) and catalog_asset.get("id"):
            existing = _find_clue_by_asset(data, asset_id=catalog_asset["id"])
            if existing:
                skipped_asset = {
                    "id": catalog_asset["id"],
                    "reason": "asset_already_attached",
                    "existing_clue_id": existing.get("id"),
                }
            else:
                asset = {
                    "id": catalog_asset["id"],
                    "file": catalog_asset.get("file"),
                    "label": catalog_asset.get("label", text[:80]),
                }

    clue = {
        "id": clue_id or f"clue_{next_id:03d}",
        "text": text,
        "type": clue_type,
        "tier": int(tier),
        "source": source if source else None,
        "related_npcs": (
            [n.strip() for n in related_npcs.split(",") if n.strip()]
            if related_npcs
            else list(catalog_entry.get("related_npcs", []))
            if isinstance(catalog_entry, dict)
            else []
        ),
        "related_scenes": (
            [s.strip() for s in related_scenes.split(",") if s.strip()]
            if related_scenes
            else list(catalog_entry.get("related_scenes", []))
            if isinstance(catalog_entry, dict)
            else []
        ),
        "discovered_at": __import__("datetime").datetime.now().isoformat(),
        "asset": asset
    }
    if clue_id:
        clue["catalog_id"] = clue_id
    if asset is None and not skipped_asset:
        matched_asset_id = attach_matching_clue_asset(data, clue)
        if matched_asset_id:
            existing = _find_clue_by_asset(data, asset_id=matched_asset_id)
            if existing:
                clue["asset"] = None
                skipped_asset = {
                    "id": matched_asset_id,
                    "reason": "asset_already_attached",
                    "existing_clue_id": existing.get("id"),
                }
    data["clues_found"][category].append(clue)
    granted_item = (
        catalog_entry.get("granted_item")
        if isinstance(catalog_entry, dict)
        else None
    )
    item_added = False
    if granted_item:
        inventory = data.setdefault("pc", {}).setdefault("inventory", [])
        if granted_item not in inventory:
            inventory.append(granted_item)
            item_added = True
    flag_effects = _apply_clue_flag_effects(data, catalog_entry)
    _save(data)
    result = {"ok": True, "clue": clue}
    if granted_item:
        result["granted_item"] = granted_item
        result["item_added"] = item_added
    if flag_effects:
        result["flag_effects"] = flag_effects
    if skipped_asset:
        result["skipped_asset"] = skipped_asset
    print(json.dumps(result, ensure_ascii=False))


def cmd_link_clues(from_id, to_id, reasoning):
    """创建两条线索的关联，自动生成一条 TIER_2 推理线索。"""
    data = _load()
    _migrate_old_clue_format(data)

    # 校验两条线索都存在
    all_clue_ids = set()
    for cat in CLUE_CATEGORIES:
        for item in data.get("clues_found", {}).get(cat, []):
            if item.get("id"):
                all_clue_ids.add(item["id"])
    if from_id not in all_clue_ids or to_id not in all_clue_ids:
        print(json.dumps({"ok": False, "error": f"线索不存在: from={from_id} to={to_id}"}, ensure_ascii=False))
        return

    # 校验重复关联
    links = data.setdefault("clue_links", [])
    for link in links:
        if {link.get("from"), link.get("to")} == {from_id, to_id}:
            print(json.dumps({"ok": False, "error": "关联已存在", "link": link}, ensure_ascii=False))
            return

    link_id = f"link_{len(links)+1:03d}"
    link = {
        "id": link_id,
        "from": from_id,
        "to": to_id,
        "reasoning": reasoning,
        "created_by": "inference",
        "created_at": __import__("datetime").datetime.now().isoformat()
    }
    links.append(link)

    # 自动生成 TIER_2 推理线索
    next_id = _clue_counter(data)
    inference = {
        "id": f"clue_{next_id:03d}",
        "text": f"推理：{reasoning}",
        "type": "inferred",
        "tier": 2,
        "source": "inference",
        "related_npcs": [],
        "related_scenes": [],
        "discovered_at": __import__("datetime").datetime.now().isoformat(),
        "asset": None,
        "inference_from": [from_id, to_id]
    }
    data["clues_found"].setdefault("investigation", []).append(inference)
    _save(data)
    print(json.dumps({"ok": True, "link": link, "inference": inference}, ensure_ascii=False))


def cmd_show_handout(entity_type, entity_id, asset_id=None):
    """查找资产映射，返回图片文件信息。"""
    data = _load()
    resolved_asset_id, entry = resolve_handout_asset(
        data,
        entity_type,
        entity_id,
        asset_id=asset_id,
    )
    if entry:
        seen = data.setdefault("seen_handouts", {})
        seen_key = entity_type + "s"
        if not isinstance(seen.get(seen_key), list):
            seen[seen_key] = []
        seen_assets = data.setdefault("seen_handout_assets", {})
        if not isinstance(seen_assets.get(seen_key), list):
            seen_assets[seen_key] = []
        already_seen = (
            resolved_asset_id in seen_assets[seen_key]
            or resolved_asset_id in seen[seen_key]
        )
        changed = False
        if entity_id and entity_id not in seen[seen_key]:
            seen[seen_key].append(entity_id)
            changed = True
        if resolved_asset_id and resolved_asset_id not in seen_assets[seen_key]:
            seen_assets[seen_key].append(resolved_asset_id)
            changed = True
        if changed:
            _save(data)
        result = {"found": True, "entity_type": entity_type, "entity_id": entity_id,
                  "asset_id": resolved_asset_id, "file": entry["file"],
                  "label": entry.get("label", ""), "already_seen": already_seen}
    else:
        result = {"found": False, "entity_type": entity_type, "entity_id": entity_id,
                  "hint": f"资产映射中未找到 {entity_type}={entity_id}"}
    print(json.dumps(result, ensure_ascii=False))


def cmd_add_item(item_name):
    data = _load()
    inv = data.setdefault("pc", {}).setdefault("inventory", [])
    item_name = str(item_name).strip()
    if item_name in inv:
        print(json.dumps({
            "ok": True,
            "duplicate": True,
            "item": item_name,
        }, ensure_ascii=False))
        return
    inv.append(item_name)
    _save(data)
    print(json.dumps({
        "ok": True,
        "duplicate": False,
        "item": item_name,
    }, ensure_ascii=False))


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
            new_entry = {"tier": tier_int, "text": entry_text}
            if new_entry in revealed["entries"]:
                print(json.dumps({
                    "ok": True,
                    "duplicate": True,
                    "npc_id": npc_id,
                    "npc_name": npc["name"],
                    "revealed_level": revealed.get("level", 0),
                    "new_entry": new_entry,
                }, ensure_ascii=False))
                return
            revealed["entries"].append(new_entry)
            # 自动升级 level 到最高已揭示 tier
            max_tier = max(e["tier"] for e in revealed["entries"])
            revealed["level"] = max_tier
            found = True
            _save(data)
            print(json.dumps({
                "ok": True,
                "npc_id": npc_id,
                "npc_name": npc["name"],
                "duplicate": False,
                "revealed_level": revealed["level"],
                "new_entry": new_entry
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
    print("  python state_manager.py add-clue <text> [category] [asset_id] [clue_id]  添加线索")
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
    "link-clues": cmd_link_clues,
    "show-handout": cmd_show_handout,
}


def _dispatch():
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
            print("ERROR: add-clue 需要 <text> [category] [asset_id] [clue_id] 参数", file=sys.stderr)
            sys.exit(1)
        cat = sys.argv[3] if len(sys.argv) > 3 else "investigation"
        asset_id = sys.argv[4] if len(sys.argv) > 4 else None
        clue_id = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
        cmd_add_clue(sys.argv[2], cat, asset_id=asset_id, clue_id=clue_id)
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
    elif cmd == "link-clues":
        if len(sys.argv) < 5:
            print("ERROR: link-clues 需要 <from_id> <to_id> <reasoning>", file=sys.stderr)
            sys.exit(1)
        cmd_link_clues(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "show-handout":
        if len(sys.argv) < 4:
            print("ERROR: show-handout 需要 <entity_type> <entity_id>", file=sys.stderr)
            sys.exit(1)
        asset_id = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
        cmd_show_handout(sys.argv[2], sys.argv[3], asset_id)
    else:
        print(f"ERROR: 未知命令 '{cmd}'", file=sys.stderr)
        cmd_usage()
        sys.exit(1)


def main():
    global _TRANSACTION_STATE
    with STORE.transaction() as state:
        _TRANSACTION_STATE = state
        try:
            _dispatch()
        finally:
            _TRANSACTION_STATE = None


if __name__ == "__main__":
    main()
