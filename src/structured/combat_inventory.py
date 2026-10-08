"""Bridge authoritative item IDs to the legacy combat inventory operations."""

import copy

from src.gameplay.investigators import (
    investigator_entity,
    project_active_investigator,
    stable_investigator_id,
)

from .domains import CommandResult, EventSpec, _inventory_projection, _stat_projection
from .errors import StructuredError
from .ids import new_stable_id
from .registries import ensure_item_registry, legacy_inventory_projection


def project_combat_inventory(state: dict) -> None:
    """Read registry holdings, never re-import consumed/transferred sheet strings."""
    ensure_item_registry(state)
    ids = set((state.get("investigators") or {}).keys())
    ids.add(stable_investigator_id(state, "pc"))
    for investigator_id in ids:
        sheet = investigator_entity(state, investigator_id)
        if sheet is None:
            continue
        sheet["inventory"] = legacy_inventory_projection(state, investigator_id)
        roster_sheet = (state.get("investigators") or {}).get(investigator_id)
        if isinstance(roster_sheet, dict):
            roster_sheet["inventory"] = copy.deepcopy(sheet["inventory"])


def settle_combat_inventory(state: dict, before: dict, outcome: CommandResult) -> None:
    """Apply the rule engine's exact ammo receipt without rebuilding any IDs."""
    ammo = outcome.result.get("ammo")
    if isinstance(ammo, dict) and ammo.get("tracked"):
        investigator_id = stable_investigator_id(state, ammo["investigator_id"])
        registry = ensure_item_registry(state)
        selected_id = ammo.get("weapon_item_id")
        entry = (
            registry["items"].get(selected_id)
            if selected_id
            else next(
                (
                    item
                    for item in registry["items"].values()
                    if item.get("holder") == {"kind": "investigator", "id": investigator_id}
                    and item.get("label") == ammo.get("item_before")
                    and int(item.get("quantity") or 0) > 0
                ),
                None,
            )
        )
        if (
            entry is None
            or entry.get("holder") != {"kind": "investigator", "id": investigator_id}
            or entry.get("label") != ammo.get("item_before")
            or int(entry.get("quantity") or 0) <= 0
        ):
            raise StructuredError("object_not_held", "弹药凭证与物品注册表不一致，未结算。")
        if int(entry["quantity"]) > 1:
            entry["quantity"] -= 1
            entry = copy.deepcopy(entry)
            entry["item_id"] = new_stable_id("item")
            entry["quantity"] = 1
            registry["items"][entry["item_id"]] = entry
        entry["label"] = ammo["item_after"]
        entry["legacy_label"] = ammo["item_after"]
        entry["stack_key"] = f"investigator:{investigator_id}/{ammo['item_after']}"
        ammo["used_item_id"] = entry["item_id"]

    project_combat_inventory(state)
    project_active_investigator(state)

    ids = set((state.get("investigators") or {}).keys())
    ids.add(stable_investigator_id(state, "pc"))
    for investigator_id in sorted(ids):
        sheet = investigator_entity(state, investigator_id)
        old = investigator_entity(before, investigator_id)
        if sheet is None or old is None:
            continue
        audience = {"kind": "investigators", "investigator_ids": [investigator_id]}
        if sheet.get("inventory") != old.get("inventory"):
            payload = {
                "investigator_id": investigator_id,
                "items": _inventory_projection(state, investigator_id),
            }
            outcome.events.extend(
                [
                    EventSpec("inventory_changed", payload, audience),
                    EventSpec("inventory_changed", copy.deepcopy(payload), {"kind": "keeper"}),
                ]
            )
        if _stat_projection(sheet, investigator_id) != _stat_projection(old, investigator_id):
            payload = _stat_projection(sheet, investigator_id)
            outcome.events.extend(
                [
                    EventSpec("state_changed", payload, audience),
                    EventSpec("state_changed", copy.deepcopy(payload), {"kind": "keeper"}),
                ]
            )
