"""Authoritative sheets govern encounter vitals and outstanding consent.

No prose inference or new damage/healing rules. The encounter is a projection;
stat commands and rule execution must not let its cached HP revive a character.
"""

import copy

from src.gameplay import combat as rules
from src.gameplay.investigators import stable_investigator_id

from .errors import StructuredError


def _entity(state: dict, entity_id: str) -> dict:
    try:
        return rules._entity_for(state, entity_id)[0]
    except rules.CombatError as exc:
        raise StructuredError("object_not_found", str(exc)) from exc


def project_combat_vitals(state: dict) -> bool:
    combat = state.get("combat_state")
    if not isinstance(combat, dict) or not combat.get("active"):
        return False
    changed = False
    updates = []
    for participant in combat.get("participants", []):
        entity = _entity(state, participant["id"])
        if "dead" in (participant.get("conditions") or []) and "dead" not in (
            entity.get("conditions") or []
        ):
            raise StructuredError(
                "check_conditions_changed", "死亡记录副本不一致，不能同步解除死亡；请核对存档。"
            )
        for field in ("hp", "max_hp", "conditions"):
            if field in entity and participant.get(field) != entity[field]:
                updates.append((participant, field, copy.deepcopy(entity[field])))
                changed = True
    for participant, field, value in updates:
        participant[field] = value
    return changed


def require_live_action(state: dict, action: dict, *, defender_id: str = "") -> None:
    """Check fresh sheets before probing/confirming an already prepared action."""
    combat = state.get("combat_state") or {}
    actor_id = stable_investigator_id(state, action.get("actor_id"))
    if combat.get("current_actor") != actor_id:
        raise StructuredError("stale_target", "行动次序已变化，请主持重新批准动作。")
    actor = _entity(state, actor_id)
    participants = {p["id"]: p for p in combat.get("participants", [])}
    roster = state.get("investigators") or {}
    # Divergent old copies are not permission to resurrect someone. Both the
    # encounter and character copies must allow action until explicitly synced
    # by a committed adjustment; never rewrite a downed cache during validation.
    if any(
        not rules._can_act(entry)
        for entry in (actor, participants.get(actor_id, {}), roster.get(actor_id, actor))
    ):
        raise StructuredError("invalid_action", "行动者当前无法行动，请重新处理战况。")
    if action.get("target_id"):
        target = _entity(state, action["target_id"])
        participant = participants.get(action["target_id"], {})
        if any(
            entry.get("hp", 0) <= 0
            or "dead" in (entry.get("conditions") or [])
            or (participant.get("kind") == "pc" and not rules._can_act(entry))
            for entry in (target, participant, roster.get(action["target_id"], target))
        ):
            raise StructuredError("stale_target", "目标已无法参与本次战斗动作。")
    if defender_id and not rules._can_act(_entity(state, defender_id)):
        raise StructuredError("invalid_action", "响应调查员当前无法行动，请主持重新处理战况。")


def reconcile_stat_adjustment(state: dict, outcome) -> None:
    """Publish synchronized vitals, retire old consent, and skip downed actors.

    Called inside the same transaction as adjust_stat (also nested approval).
    This does not complete player requests, heal conditions, or spend RNG/ammo.
    """
    if not project_combat_vitals(state):
        return
    from .combat_endings import combat_projection
    from .combat_flow import invalidate_combat_wait
    from .domains import EventSpec

    invalidated = invalidate_combat_wait(state)
    combat = state["combat_state"]
    rules._check_combat_end(combat)
    if combat.get("active"):
        actor = rules._find_participant(combat, combat["current_actor"])
        if not rules._can_act(actor):
            rules._advance_turn(combat)
    outcome.result["combat_wait_invalidated"] = invalidated
    outcome.events.append(EventSpec("combat_updated", combat_projection(state)))
