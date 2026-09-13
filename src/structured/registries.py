"""稳定物品/线索 ID 注册表（协议 §7）。

旧世界里物品是字符串标签、线索按类别+文本拼接为键，都不能作为新协议的可写
实体 ID。首次在 structured_v1 世界使用时一次性迁移并随状态持久化（幂等，
不每次重算）；旧前端经兼容投影继续看到字符串背包与分类线索列表。
"""

from __future__ import annotations

from typing import Any

from .ids import new_stable_id

ITEM_REGISTRY_VERSION = 1
CLUE_REGISTRY_VERSION = 1


def _legacy_item_label(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("label") or item.get("name") or item.get("id") or "").strip()
    return ""


def _legacy_item_quantity(item: Any) -> int:
    if isinstance(item, dict):
        try:
            return max(0, int(item.get("quantity", 1)))
        except (TypeError, ValueError):
            return 1
    return 1


def ensure_item_registry(state: dict) -> dict:
    """把字符串背包一次性迁移为稳定 ID 注册表（幂等）。

    结构：state["item_registry"] = {
        "schema_version": 1,
        "items": {item_id: {"item_id", "label", "quantity", "holder",
                            "stack_key", "legacy_label"}},
    }
    holder: {"kind": "investigator"|"npc"|"scene", "id": str}。
    同一持有者的同名旧字符串物品合并为同一堆叠（quantity 累加）；不同持有者
    之间绝不合并。拆分/转移产生的新堆叠由命令层生成新 ID。
    """
    registry = state.get("item_registry")
    if isinstance(registry, dict) and registry.get("schema_version") == ITEM_REGISTRY_VERSION:
        return registry

    items: dict[str, dict] = {}
    by_stack: dict[str, dict] = {}

    def add(raw: Any, holder: dict) -> None:
        label = _legacy_item_label(raw)
        if not label:
            return
        holder_key = f"{holder['kind']}:{holder['id']}"
        stack_key = f"{holder_key}/{label}"
        # 同持有者同名物品折叠进既有堆叠（幂等保证来自 stack_key 稳定）。
        existing = by_stack.get(stack_key)
        if existing is not None:
            existing["quantity"] = int(existing["quantity"]) + _legacy_item_quantity(raw)
            return
        item_id = new_stable_id("item")
        entry = {
            "item_id": item_id,
            "label": label,
            "quantity": _legacy_item_quantity(raw),
            "holder": dict(holder),
            "stack_key": stack_key,
            "legacy_label": label,
            "operations": [],
        }
        items[item_id] = entry
        by_stack[stack_key] = entry

    investigators = state.get("investigators")
    if isinstance(investigators, dict):
        for investigator_id, sheet in investigators.items():
            inventory = sheet.get("inventory", []) if isinstance(sheet, dict) else []
            if isinstance(inventory, list):
                for raw in inventory:
                    add(raw, {"kind": "investigator", "id": str(investigator_id)})
    pc = state.get("pc")
    if isinstance(pc, dict):
        pc_id = str(pc.get("id") or pc.get("stable_id") or "pc")
        inventory = pc.get("inventory", [])
        if isinstance(inventory, list):
            for raw in inventory:
                add(raw, {"kind": "investigator", "id": pc_id})
    for npc in state.get("npcs", []) or []:
        if not isinstance(npc, dict) or not npc.get("id"):
            continue
        for raw in npc.get("inventory", []) or []:
            add(raw, {"kind": "npc", "id": str(npc["id"])})
    scene = state.get("current_scene")
    if isinstance(scene, dict):
        for raw in scene.get("items", []) or []:
            add(raw, {"kind": "scene", "id": str(scene.get("id") or "")})

    registry = {"schema_version": ITEM_REGISTRY_VERSION, "items": items}
    state["item_registry"] = registry
    return registry


def ensure_clue_registry(state: dict) -> dict:
    """线索稳定 ID：catalog_id 优先，否则按 legacy key 生成（幂等）。

    state["clue_registry"] = {
        "schema_version": 1,
        "clues": {clue_id: {"clue_id", "legacy_key", "category", "text",
                            "granted_to": [investigator_id, ...]}},
        "by_legacy_key": {legacy_key: clue_id},
    }
    """
    registry = state.get("clue_registry")
    if isinstance(registry, dict) and registry.get("schema_version") == CLUE_REGISTRY_VERSION:
        return registry

    clues: dict[str, dict] = {}
    by_legacy: dict[str, str] = {}
    catalog = state.get("clue_catalog", {})
    groups = state.get("clues_found", {})
    if isinstance(groups, dict):
        for category, entries in groups.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get("text") or "")
                legacy_key = f"{category}:{text}"
                if legacy_key in by_legacy:
                    clue_id = by_legacy[legacy_key]
                else:
                    catalog_id = str(entry.get("catalog_id") or entry.get("id") or "")
                    clue_id = catalog_id or new_stable_id("clue")
                    by_legacy[legacy_key] = clue_id
                granted = entry.get("granted_to")
                if not isinstance(granted, list):
                    owner = str(entry.get("owner_investigator_id") or "")
                    granted = [owner] if owner else []
                catalog_entry = catalog.get(clue_id, {}) if isinstance(catalog, dict) else {}
                clues[clue_id] = {
                    "clue_id": clue_id,
                    "legacy_key": legacy_key,
                    "category": str(category),
                    "text": text or str(catalog_entry.get("text") or ""),
                    "granted_to": [str(value) for value in granted],
                }
    registry = {
        "schema_version": CLUE_REGISTRY_VERSION,
        "clues": clues,
        "by_legacy_key": by_legacy,
    }
    state["clue_registry"] = registry
    return registry


def legacy_inventory_projection(state: dict, investigator_id: str) -> list[str]:
    """旧前端兼容投影：稳定注册表 → 字符串背包（含数量折叠）。"""
    registry = state.get("item_registry") or {}
    items = registry.get("items", {}) if isinstance(registry, dict) else {}
    labels: list[str] = []
    for entry in items.values():
        holder = entry.get("holder") or {}
        if holder.get("kind") == "investigator" and str(holder.get("id")) == investigator_id:
            quantity = int(entry.get("quantity") or 0)
            label = str(entry.get("label") or "")
            labels.extend([label] * max(quantity, 0))
    return labels


def find_item(state: dict, item_id: str) -> dict | None:
    registry = ensure_item_registry(state)
    entry = registry["items"].get(item_id)
    if entry is not None:
        return entry
    # 旧标签一次性映射：新协议请求只允许稳定 ID，这里仅为迁移窗口兜底。
    by_label = next(
        (
            entry
            for entry in registry["items"].values()
            if entry["legacy_label"] == item_id or entry["item_id"] == item_id
        ),
        None,
    )
    return by_label
