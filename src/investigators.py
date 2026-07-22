"""Multi-investigator roster projected through the legacy active ``pc`` view."""

from __future__ import annotations

import copy
from typing import Any

from .characters import apply_character_to_state


class InvestigatorRosterError(RuntimeError):
    pass


def initialize_investigator_roster(
    context: Any,
    roster: list[dict],
    *,
    active_investigator_id: str,
) -> None:
    """Create persistent investigators while keeping ``pc`` as active projection."""
    if not roster:
        raise InvestigatorRosterError("房间中没有已选择调查员的玩家")

    def apply(state: dict) -> None:
        investigators: dict[str, dict] = {}
        controllers: dict[str, str] = {}
        for entry in roster:
            investigator_id = str(entry.get("investigator_id") or "")
            user_id = str(entry.get("user_id") or "")
            character_ref = entry.get("character_ref")
            if not investigator_id or not user_id or not isinstance(character_ref, dict):
                raise InvestigatorRosterError("调查员绑定缺少有效角色资料")
            scratch = {"pc": {}}
            selected = apply_character_to_state(
                character_ref,
                scratch,
                context.module_name,
                context=context,
            )
            if selected is None:
                raise InvestigatorRosterError("无法读取房间中的调查员角色")
            inventory = scratch["pc"].setdefault("inventory", [])
            for item in state.get("module_starting_inventory", []):
                if item not in inventory:
                    inventory.append(copy.deepcopy(item))
            scratch["pc"]["controller_user_id"] = user_id
            scratch["pc"]["investigator_id"] = investigator_id
            investigators[investigator_id] = scratch["pc"]
            controllers[user_id] = investigator_id
        if active_investigator_id not in investigators:
            raise InvestigatorRosterError("当前行动者没有绑定调查员")
        state["investigators"] = investigators
        state["investigator_controllers"] = controllers
        state["active_investigator_id"] = active_investigator_id
        state["pc"] = copy.deepcopy(investigators[active_investigator_id])

    context.world_store.update(apply)


def activate_investigator(context: Any, investigator_id: str) -> None:
    """Persist the previous active PC and project the selected investigator."""
    investigator_id = str(investigator_id or "")

    def apply(state: dict) -> None:
        investigators = state.get("investigators")
        if not isinstance(investigators, dict) or investigator_id not in investigators:
            raise InvestigatorRosterError("当前账号没有可操作的调查员")
        previous = str(state.get("active_investigator_id") or "")
        if previous and previous in investigators and isinstance(state.get("pc"), dict):
            investigators[previous] = copy.deepcopy(state["pc"])
        state["active_investigator_id"] = investigator_id
        state["pc"] = copy.deepcopy(investigators[investigator_id])

    context.world_store.update(apply)


def sync_active_investigator(context: Any) -> None:
    """Copy mutations made through ``pc`` back into the persistent roster."""

    def apply(state: dict) -> None:
        investigator_id = str(state.get("active_investigator_id") or "")
        investigators = state.get("investigators")
        if not investigator_id or not isinstance(investigators, dict):
            return
        if investigator_id in investigators and isinstance(state.get("pc"), dict):
            investigators[investigator_id] = copy.deepcopy(state["pc"])

    context.world_store.update(apply)


def public_investigator_roster(state: dict) -> list[dict]:
    investigators = state.get("investigators")
    if not isinstance(investigators, dict):
        pc = state.get("pc")
        return [copy.deepcopy(pc)] if isinstance(pc, dict) and pc else []
    return [
        {
            "investigator_id": investigator_id,
            "controller_user_id": pc.get("controller_user_id"),
            "name": pc.get("name", ""),
            "occupation": pc.get("occupation", ""),
            "hp": pc.get("hp"),
            "max_hp": pc.get("max_hp"),
            "san": pc.get("san"),
            "max_san": pc.get("max_san"),
            "portrait": pc.get("portrait"),
            "active": investigator_id == state.get("active_investigator_id"),
        }
        for investigator_id, pc in investigators.items()
        if isinstance(pc, dict)
    ]
