"""Canonical inventory registration shared by classic and structured domains."""

from __future__ import annotations

import secrets
from typing import Any

from src.gameplay.investigators import stable_investigator_id

ITEM_REGISTRY_VERSION = 1


def new_stable_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


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
    pc = state.get("pc")
    active_id = stable_investigator_id(state, "pc")
    if isinstance(investigators, dict):
        for investigator_id, sheet in investigators.items():
            if investigator_id == active_id and isinstance(pc, dict):
                continue  # the active pc is authoritative, not a second backpack
            inventory = sheet.get("inventory", []) if isinstance(sheet, dict) else []
            if isinstance(inventory, list):
                for raw in inventory:
                    add(raw, {"kind": "investigator", "id": str(investigator_id)})
    if isinstance(pc, dict):
        pc_id = active_id if active_id != "pc" else str(pc.get("id") or pc.get("stable_id") or "pc")
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
