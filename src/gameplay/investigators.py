"""Multi-investigator roster projected through the legacy active ``pc`` view."""

from __future__ import annotations

import copy
from typing import Any

from src.gameplay.characters import apply_character_to_state


class InvestigatorRosterError(RuntimeError):
    pass


def stable_investigator_id(state: dict, investigator_id: str | None = None) -> str:
    """Resolve legacy ``pc`` to the active stable roster id when one exists."""
    requested = str(investigator_id or "pc")
    investigators = state.get("investigators")
    active_id = str(state.get("active_investigator_id") or "")
    if (
        requested == "pc"
        and isinstance(investigators, dict)
        and active_id
        and isinstance(investigators.get(active_id), dict)
    ):
        return active_id
    return requested


def investigator_entity(state: dict, investigator_id: str) -> dict | None:
    """Return one investigator while treating the active ``pc`` as authoritative."""
    stable_id = stable_investigator_id(state, investigator_id)
    active_id = str(state.get("active_investigator_id") or "")
    pc = state.get("pc")
    if stable_id == active_id and isinstance(pc, dict):
        return pc
    investigators = state.get("investigators")
    if isinstance(investigators, dict):
        entity = investigators.get(stable_id)
        if isinstance(entity, dict):
            return entity
    if stable_id == "pc" and isinstance(pc, dict):
        return pc
    return None


def investigator_controller_user_id(state: dict, investigator_id: str) -> str | None:
    """Resolve the server-owned controller of a stable investigator id."""
    stable_id = stable_investigator_id(state, investigator_id)
    controllers = state.get("investigator_controllers")
    if isinstance(controllers, dict):
        for user_id, controlled_id in controllers.items():
            if str(controlled_id) == stable_id:
                return str(user_id)
    entity = investigator_entity(state, stable_id)
    if isinstance(entity, dict) and entity.get("controller_user_id"):
        return str(entity["controller_user_id"])
    return None


def decision_has_controller(context: Any, decision: dict) -> bool:
    """Whether a restored private decision still has an authoritative recipient."""
    investigator_id = str(decision.get("responding_investigator_id") or "")
    if not investigator_id:
        return True
    try:
        return investigator_controller_user_id(
            context.world_store.load(),
            investigator_id,
        ) is not None
    except Exception:
        return False


def normalize_legacy_combat_investigator_ids(state: dict) -> bool:
    """Upgrade an in-progress multiplayer combat from ``pc`` to its stable id."""
    stable_id = stable_investigator_id(state, "pc")
    combat = state.get("combat_state")
    if stable_id == "pc" or not isinstance(combat, dict):
        return False

    changed = False

    def replace(value: Any) -> Any:
        nonlocal changed
        if value == "pc":
            changed = True
            return stable_id
        return value

    participants = combat.get("participants")
    if isinstance(participants, list):
        for participant in participants:
            if not isinstance(participant, dict) or participant.get("id") != "pc":
                continue
            participant["id"] = stable_id
            participant["path"] = (
                "pc"
                if stable_id == str(state.get("active_investigator_id") or "")
                else f"investigators.{stable_id}"
            )
            changed = True
    order = combat.get("turn_order")
    if isinstance(order, list):
        combat["turn_order"] = [replace(value) for value in order]
    combat["current_actor"] = replace(combat.get("current_actor"))

    counts = combat.get("defense_counts")
    if isinstance(counts, dict) and "pc" in counts:
        counts[stable_id] = counts.pop("pc")
        changed = True

    pending = combat.get("pending_decision")
    if isinstance(pending, dict):
        action = pending.get("action")
        if isinstance(action, dict):
            action["actor_id"] = replace(action.get("actor_id"))
            action["target_id"] = replace(action.get("target_id"))
            responding_id = (
                action.get("target_id")
                if pending.get("kind") == "combat_defense"
                else action.get("actor_id")
            )
            if responding_id:
                pending["responding_investigator_id"] = responding_id
        if pending.get("target_investigator_id") == "pc":
            pending["target_investigator_id"] = stable_id
            changed = True
    return changed


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


def reconcile_investigator_roster(
    context: Any,
    roster: list[dict],
    *,
    preferred_investigator_id: str | None = None,
) -> dict[str, str]:
    """Replace snapshot controller metadata with the control-plane roster.

    Save snapshots deliberately contain investigator state, but controller
    ownership is security metadata and must never be restored from a snapshot.
    Existing investigator state is retained; only a currently claimed
    investigator absent from an old snapshot is rebuilt from its character ref.
    """
    normalized: list[tuple[str, str, dict]] = []
    investigator_ids: set[str] = set()
    user_ids: set[str] = set()
    for entry in roster:
        investigator_id = str(entry.get("investigator_id") or "")
        user_id = str(entry.get("user_id") or "")
        character_ref = entry.get("character_ref")
        if not investigator_id or not user_id or not isinstance(character_ref, dict):
            raise InvestigatorRosterError("调查员绑定缺少有效角色资料")
        if investigator_id in investigator_ids or user_id in user_ids:
            raise InvestigatorRosterError("调查员控制关系存在重复绑定")
        investigator_ids.add(investigator_id)
        user_ids.add(user_id)
        normalized.append((investigator_id, user_id, character_ref))

    result: dict[str, str] = {}

    def apply(state: dict) -> None:
        nonlocal result
        # The active legacy projection may contain newer HP/SAN/inventory than
        # the roster copy stored alongside it in an older snapshot.
        project_active_investigator(state)
        investigators = state.get("investigators")
        if not isinstance(investigators, dict):
            investigators = {}
            state["investigators"] = investigators

        for entity in investigators.values():
            if isinstance(entity, dict):
                entity["controller_user_id"] = None
        pc = state.get("pc")
        if isinstance(pc, dict):
            pc["controller_user_id"] = None

        controllers: dict[str, str] = {}
        for investigator_id, user_id, character_ref in normalized:
            entity = investigators.get(investigator_id)
            if not isinstance(entity, dict):
                scratch = {"pc": {}}
                selected = apply_character_to_state(
                    character_ref,
                    scratch,
                    context.module_name,
                    context=context,
                )
                if selected is None:
                    raise InvestigatorRosterError(
                        "当前调查员不在存档中，且无法从角色资料恢复"
                    )
                entity = scratch["pc"]
                inventory = entity.setdefault("inventory", [])
                for item in state.get("module_starting_inventory", []):
                    if item not in inventory:
                        inventory.append(copy.deepcopy(item))
                investigators[investigator_id] = entity
            entity["investigator_id"] = investigator_id
            entity["controller_user_id"] = user_id
            controllers[user_id] = investigator_id

        state["investigator_controllers"] = controllers
        current_active = str(state.get("active_investigator_id") or "")
        preferred = str(preferred_investigator_id or "")
        active_id = (
            preferred
            if preferred in investigator_ids
            else current_active
            if current_active in investigator_ids
            else normalized[0][0]
            if normalized
            else current_active
        )
        if active_id and isinstance(investigators.get(active_id), dict):
            state["active_investigator_id"] = active_id
            state["pc"] = copy.deepcopy(investigators[active_id])

        normalize_legacy_combat_investigator_ids(state)
        combat = state.get("combat_state")
        if (
            isinstance(combat, dict)
            and combat.get("active")
            and not isinstance(combat.get("pending_decision"), dict)
        ):
            current_actor = str(combat.get("current_actor") or "")
            participant = next(
                (
                    item
                    for item in combat.get("participants", [])
                    if isinstance(item, dict) and str(item.get("id") or "") == current_actor
                ),
                None,
            )
            if (
                isinstance(participant, dict)
                and participant.get("kind") == "pc"
                and investigator_controller_user_id(state, current_actor) is None
            ):
                candidates = [preferred, *controllers.values()]
                replacement = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate
                        and any(
                            isinstance(item, dict)
                            and str(item.get("id") or "") == candidate
                            and item.get("kind") == "pc"
                            and int(item.get("hp", 0) or 0) > 0
                            for item in combat.get("participants", [])
                        )
                    ),
                    None,
                )
                if replacement:
                    # Local import avoids the combat -> investigators import cycle.
                    from src.gameplay.combat import assign_combat_actor

                    assign_combat_actor(
                        state,
                        replacement,
                        reason="控制权变更后跳过无控制者",
                    )
        result = controllers

    context.world_store.update(apply)
    return result


def activate_investigator(context: Any, investigator_id: str) -> None:
    """Persist the previous active PC and project the selected investigator."""
    investigator_id = str(investigator_id or "")

    def apply(state: dict) -> None:
        investigators = state.get("investigators")
        if not isinstance(investigators, dict) or investigator_id not in investigators:
            raise InvestigatorRosterError("当前账号没有可操作的调查员")
        previous = str(state.get("active_investigator_id") or "")
        normalize_legacy_combat_investigator_ids(state)
        if previous and previous in investigators and isinstance(state.get("pc"), dict):
            investigators[previous] = copy.deepcopy(state["pc"])
        state["active_investigator_id"] = investigator_id
        state["pc"] = copy.deepcopy(investigators[investigator_id])

    context.world_store.update(apply)


def sync_active_investigator(context: Any) -> None:
    """Copy mutations made through ``pc`` back into the persistent roster."""

    def apply(state: dict) -> None:
        project_active_investigator(state)

    context.world_store.update(apply)


def release_investigator_controller(context: Any, user_id: str) -> str | None:
    """Remove a former player's private-state projection from authoritative state."""
    released_id: str | None = None

    def apply(state: dict) -> None:
        nonlocal released_id
        controllers = state.get("investigator_controllers")
        if isinstance(controllers, dict):
            value = controllers.pop(user_id, None)
            released_id = str(value) if value else None
        investigators = state.get("investigators")
        if (
            released_id
            and isinstance(investigators, dict)
            and isinstance(investigators.get(released_id), dict)
        ):
            investigators[released_id]["controller_user_id"] = None
        pc = state.get("pc")
        if isinstance(pc, dict) and pc.get("controller_user_id") == user_id:
            pc["controller_user_id"] = None

    context.world_store.update(apply)
    return released_id


def project_active_investigator(state: dict) -> bool:
    """Project the legacy active ``pc`` into a roster state in-place."""
    normalize_legacy_combat_investigator_ids(state)
    investigator_id = str(state.get("active_investigator_id") or "")
    investigators = state.get("investigators")
    if (
        not investigator_id
        or not isinstance(investigators, dict)
        or investigator_id not in investigators
        or not isinstance(state.get("pc"), dict)
    ):
        return False
    investigators[investigator_id] = copy.deepcopy(state["pc"])
    return True


def public_investigator_roster(state: dict) -> list[dict]:
    investigators = state.get("investigators")
    if not isinstance(investigators, dict):
        pc = state.get("pc")
        investigators = (
            {str(pc.get("investigator_id") or "legacy-pc"): pc}
            if isinstance(pc, dict) and pc
            else {}
        )
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


def visible_clues_for_investigator(clues: Any, investigator_id: str | None) -> dict:
    """Filter private clue records before building a player-facing state payload."""
    if not isinstance(clues, dict):
        return {}
    visible: dict[str, Any] = {}
    for category, entries in clues.items():
        if not isinstance(entries, list):
            visible[category] = copy.deepcopy(entries)
            continue
        visible[category] = [
            copy.deepcopy(clue)
            for clue in entries
            if not isinstance(clue, dict)
            or clue.get("visibility") != "private"
            or (
                investigator_id is not None and clue.get("owner_investigator_id") == investigator_id
            )
        ]
    return visible
