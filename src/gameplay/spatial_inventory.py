"""Spatial item metadata on the existing canonical item registry."""

from __future__ import annotations

import copy
from collections import Counter

from src.gameplay.investigators import investigator_entity, project_active_investigator
from src.gameplay.spatial import SpatialError, actor_id, enabled


def bind_definitions(world: dict) -> None:
    if not enabled(world):
        return
    from src.gameplay.item_registry import ensure_item_registry

    ensure_item_registry(world)
    definitions = world["spatial_definition"].get("items", {})
    by_label = {item["label"]: key for key, item in definitions.items()}
    for item in (world.get("item_registry") or {}).get("items", {}).values():
        definition = item.get("definition_id") or by_label.get(item.get("label"))
        if definition not in definitions:
            continue
        if definitions[definition]["states"] and item.get("quantity") not in (0, 1):
            raise SpatialError("invalid_item_state", "带状态物品必须逐件登记，不能共用堆叠状态。")
        item["definition_id"] = definition
        states = item.setdefault("states", {})
        for key, spec in definitions[definition]["states"].items():
            states.setdefault(key, spec["initial"])
            if (
                type(states[key]) is not int
                or not spec["minimum"] <= states[key] <= spec["maximum"]
            ):
                raise SpatialError("invalid_item_state", "物品状态超出作者范围。")


def held_items(world: dict, definition: str, actor: str) -> list[dict]:
    bind_definitions(world)
    return sorted(
        (
            item
            for item in (world.get("item_registry") or {}).get("items", {}).values()
            if item.get("definition_id") == definition
            and item.get("quantity", 0) > 0
            and item.get("holder") == {"kind": "investigator", "id": actor}
        ),
        key=lambda item: item["item_id"],
    )


def project_inventory(world: dict, actor: str) -> None:
    sheet = investigator_entity(world, actor)
    if not isinstance(sheet, dict):
        raise SpatialError("unknown_investigator", "调查员不存在。")
    sheet["inventory"] = [
        item["label"]
        for item in (world.get("item_registry") or {}).get("items", {}).values()
        if item.get("holder") == {"kind": "investigator", "id": actor}
        for _ in range(max(0, item.get("quantity", 0)))
    ]
    roster = (world.get("investigators") or {}).get(actor)
    if isinstance(roster, dict):
        roster["inventory"] = copy.deepcopy(sheet["inventory"])
    project_active_investigator(world)


def reconcile_legacy_inventory(before: dict, after: dict) -> None:
    """Legacy sheet edits cannot leave a discarded key in the item registry."""
    if not enabled(before) or before.get("item_registry") != after.get("item_registry"):
        return
    actor = actor_id(after)
    old, new = investigator_entity(before, actor), investigator_entity(after, actor)
    if (
        not isinstance(old, dict)
        or not isinstance(new, dict)
        or old.get("inventory") == new.get("inventory")
    ):
        return

    def counts(sheet):
        return Counter(
            x if isinstance(x, str) else str(x.get("label") or x.get("name") or "")
            for x in sheet.get("inventory", [])
        )

    previous, current = counts(old), counts(new)
    items = (after.get("item_registry") or {}).get("items", {})
    for label in set(previous) | set(current):
        delta = current[label] - previous[label]
        if not delta:
            continue
        matches = [
            item
            for item in items.values()
            if item.get("label") == label
            and item.get("quantity", 0) > 0
            and item.get("holder") == {"kind": "investigator", "id": actor}
        ]
        if len(matches) != 1 or delta > 0:
            # New typed objects must come from author effects, not guessed names.
            if label in {
                item["label"] for item in after["spatial_definition"].get("items", {}).values()
            }:
                raise SpatialError(
                    "ambiguous_item_edit", "该物品须通过明确的调查动作或物品转移取得/移除。"
                )
            continue
        if matches[0]["quantity"] + delta < 0:
            raise SpatialError("item_quantity_invalid", "移除数量超过持有数量。")
        matches[0]["quantity"] += delta
