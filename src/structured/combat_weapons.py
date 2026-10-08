"""Exact stable-item selection around the legacy label-based rule adapter.

Only a disposable, bounded inventory view is presented to legacy ammo matching;
the authoritative item registry is never guessed, reordered or mutated here.
The shared settlement bridge applies the resulting receipt to the selected ID.
"""

import copy

from src.gameplay.inventory import AMMO_RE, InventoryError, check_investigator_firearm_ammo
from src.gameplay.investigators import investigator_entity, stable_investigator_id

from .combat_vitals import require_live_action
from .errors import StructuredError


def selected_weapon(state: dict, actor_id: str, item_id: str) -> dict:
    actor_id = stable_investigator_id(state, actor_id)
    entry = ((state.get("item_registry") or {}).get("items") or {}).get(item_id)
    if not isinstance(entry, dict):
        raise StructuredError("object_not_found", "所选持有物品已不存在，请重新选择。")
    if (
        entry.get("holder") != {"kind": "investigator", "id": actor_id}
        or int(entry.get("quantity") or 0) <= 0
    ):
        raise StructuredError("object_not_held", "所选物品已不在该调查员身上，请重新批准。")
    if investigator_entity(state, actor_id) is None:
        raise StructuredError("object_not_found", "没有该调查员的角色资料。")
    return entry


def call_with_weapon(state: dict, action: dict, invoke, *, cancelling: bool = False) -> dict:
    """Invoke a rule/probe with exact holdings and preserve all other inventory.

    An explicit ID is retained outside the legacy action kwargs. Cancel does
    not require the old weapon still to exist. Exceptions always restore the
    sheet projection; the caller's disposable copy/transaction owns rollback.
    """
    item_id = action.get("weapon_item_id")
    legacy = {k: copy.deepcopy(v) for k, v in action.items() if k != "weapon_item_id"}
    if cancelling:
        return invoke(legacy)
    require_live_action(state, action)
    if not item_id:
        registry = (state.get("item_registry") or {}).get("items") or {}
        actor_id = stable_investigator_id(state, str(action.get("actor_id") or ""))
        trackers = [
            item
            for item in registry.values()
            if item.get("holder") == {"kind": "investigator", "id": actor_id}
            and int(item.get("quantity") or 0) > 0
            and AMMO_RE.search(str(item.get("label") or ""))
        ]
        if action.get("action_type") == "firearm" and len(trackers) > 1:
            raise StructuredError(
                "target_unresolved", "有多件带弹药记录的持有物品，请用 weapon_item_id 明确选择。"
            )
        return invoke(legacy)
    entry = selected_weapon(state, action["actor_id"], item_id)
    if action.get("weapon") and action["weapon"] != entry["label"]:
        raise StructuredError("invalid_action", "武器编号与名称不一致；指定编号时请省略兼容名称。")
    legacy["weapon"] = entry["label"]
    actor_id = stable_investigator_id(state, action["actor_id"])
    sheet = investigator_entity(state, actor_id)
    saved = copy.deepcopy(sheet.get("inventory") or [])
    sheet["inventory"] = [entry["label"]]
    try:
        if action.get("action_type") == "firearm":
            try:
                check_investigator_firearm_ammo(state, actor_id, entry["label"])
            except InventoryError as exc:
                raise StructuredError("invalid_action", str(exc)) from exc
        result = invoke(legacy)
        ammo = result.get("ammo")
        if isinstance(ammo, dict):
            ammo["weapon_item_id"] = item_id
        pending = (state.get("combat_state") or {}).get("pending_decision")
        if isinstance(pending, dict) and (pending.get("action") or {}).get("actor_id") == actor_id:
            pending["weapon_item_id"] = item_id
        return result
    finally:
        # Rule resolution can mutate HP/conditions, but not the identity of the
        # inventory view. Restoring only inventory preserves actual damage.
        current = investigator_entity(state, actor_id)
        if current is not None:
            current["inventory"] = saved


def decision_action(pending: dict) -> dict:
    action = copy.deepcopy(pending["action"])
    if pending.get("weapon_item_id"):
        action["weapon_item_id"] = pending["weapon_item_id"]
    return action
