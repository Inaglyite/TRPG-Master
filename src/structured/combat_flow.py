"""Explicit consent and roll lifecycle before combat protocol registration.

No player prose matching. Probe the old rules on a disposable copy with dice
disabled, so their preconditions/confirmation gates stay authoritative without
spending RNG, ammo or health during preparation. Real execution is transactional.
"""

from __future__ import annotations

import copy

from src.gameplay import combat as rules
from src.gameplay.investigators import investigator_controller_user_id, stable_investigator_id

from .combat_endings import (
    ContextRandom,
    _open_game,
    _result,
    execute_combat_action,
    validate_combat_action,
)
from .combat_requests import linked_request_id, validate_linked_combat_action
from .combat_vitals import require_live_action
from .combat_weapons import call_with_weapon, decision_action
from .domains import CommandContext, CommandResult, EventSpec
from .errors import StructuredError
from .ids import canonical_digest, new_stable_id


class _DiceNeeded(Exception):
    pass


class _NoDice:
    def randint(self, start: int, end: int) -> int:
        raise _DiceNeeded


def _combat(state: dict) -> dict:
    _open_game(state)
    combat = state.get("combat_state")
    if not isinstance(combat, dict) or not combat.get("active"):
        raise StructuredError("invalid_action", "没有进行中的战斗。")
    return combat


def _require_keeper(ctx: CommandContext) -> None:
    if getattr(ctx.principal, "kind", None) not in {"keeper", "agent"}:
        raise StructuredError("keeper_required", "战斗动作须先由主持批准。")


def _require_player(state: dict, ctx: CommandContext, investigator_id: str) -> None:
    principal = ctx.principal
    if (
        getattr(principal, "kind", None) != "player"
        or investigator_id not in principal.investigator_ids
    ):
        raise StructuredError("not_authorized", "只有控制该调查员的玩家能响应。")
    controller = investigator_controller_user_id(state, investigator_id)
    if controller and controller != principal.user_id:
        raise StructuredError("not_authorized", "调查员控制权已变化，请重新连接。")


def _signature(state: dict) -> str:
    combat = _combat(state)
    return canonical_digest(
        {
            "encounter_id": combat.get("encounter_id"),
            "round": combat.get("round"),
            "actor": combat.get("current_actor"),
            "participants": combat.get("participants"),
            "decision": combat.get("pending_decision"),
            "scene": (state.get("current_scene") or {}).get("id"),
            "pc": state.get("pc"),
            "investigators": state.get("investigators"),
            "npcs": state.get("npcs"),
            "item_registry": state.get("item_registry"),
        }
    )


def roll_projection(state: dict) -> dict | None:
    pending = state.get("combat_pending_roll")
    if not isinstance(pending, dict):
        return None
    projection = {
        key: copy.deepcopy(pending[key])
        for key in (
            "roll_id",
            "investigator_id",
            "actor_id",
            "target_id",
            "action_type",
            "source",
        )
    }
    for key in ("weapon_item_id", "weapon_label"):
        if key in pending:
            projection[key] = copy.deepcopy(pending[key])
    return projection


def invalidate_combat_wait(state: dict) -> bool:
    """Restore/branch keeps committed combat, not a transport-era roll consent."""
    removed = state.pop("combat_pending_roll", None) is not None
    removed = state.pop("combat_pvp", None) is not None or removed
    combat = state.get("combat_state")
    if not isinstance(combat, dict):
        return removed
    if combat.get("pending_decision") is not None:
        combat["pending_decision"] = None
        removed = True
    if removed and combat.get("active"):
        combat["phase"] = "awaiting_action"
    return removed


def _stage_roll(
    state: dict,
    ctx: CommandContext,
    *,
    action: dict,
    investigator_id: str,
    source: str,
    decision: dict | None = None,
    source_request_id: str = "",
) -> CommandResult:
    pending = {
        "roll_id": new_stable_id("combat-roll"),
        "investigator_id": investigator_id,
        "actor_id": action["actor_id"],
        "target_id": action.get("target_id"),
        "action_type": action["action_type"],
        "source": source,
        "action": copy.deepcopy(action),
        "decision": copy.deepcopy(decision),
        "conditions": _signature(state),
        "source_request_id": linked_request_id(ctx, investigator_id, source_request_id),
    }
    if action.get("weapon_item_id"):
        pending["weapon_item_id"] = action["weapon_item_id"]
        pending["weapon_label"] = ((state.get("item_registry") or {}).get("items") or {})[
            action["weapon_item_id"]
        ]["label"]
    state["combat_pending_roll"] = pending
    state["combat_state"]["phase"] = "awaiting_roll"
    outcome = _result(state, {"ok": True, "event": "roll_required"})
    public = roll_projection(state)
    outcome.events.extend(
        [
            EventSpec(
                "combat_roll_required",
                public,
                {"kind": "investigators", "investigator_ids": [investigator_id]},
            ),
            EventSpec("combat_roll_required", copy.deepcopy(public), {"kind": "keeper"}),
        ]
    )
    outcome.result["roll_id"] = pending["roll_id"]
    return outcome


def prepare_combat_action(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    _require_keeper(ctx)
    combat = _combat(state)
    if state.get("combat_pending_roll"):
        raise StructuredError("invalid_action", "请先等待当前玩家掷骰或取消。")
    if combat.get("pending_decision"):
        raise StructuredError("invalid_action", "请先等待当前玩家的决定。")
    validate_combat_action(payload)
    action = copy.deepcopy(payload)
    action["actor_id"] = stable_investigator_id(state, action["actor_id"])
    if action.get("target_id"):
        action["target_id"] = stable_investigator_id(state, action["target_id"])
    validate_linked_combat_action(state, action, ctx)
    actor = next(
        (p for p in combat.get("participants", []) if p.get("id") == action["actor_id"]), None
    )
    if actor is None:
        raise StructuredError("object_not_found", "行动者不是本场参战者。")
    target = next(
        (p for p in combat.get("participants", []) if p.get("id") == action.get("target_id")), None
    )
    if (
        actor.get("kind") == "pc"
        and target
        and target.get("kind") == "pc"
        and action["action_type"] in {"melee", "firearm", "threat"}
    ):
        from .pvp_flow import prepare_pvp

        return prepare_pvp(state, action, ctx)
    if actor.get("kind") == "npc":
        target = next(
            (p for p in combat.get("participants", []) if p.get("id") == action.get("target_id")),
            None,
        )
        if target and target.get("kind") == "pc" and action.get("defender_choice"):
            raise StructuredError("not_authorized", "主持不能替玩家选择战斗防御。")
        # The engine will still stop for player defence where applicable.
        return execute_combat_action(state, action, ctx)
    probe = copy.deepcopy(state)
    try:
        result = call_with_weapon(
            probe, action, lambda legacy: rules.combat_action(probe, **legacy, rng=_NoDice())
        )
    except _DiceNeeded:
        return _stage_roll(
            state, ctx, action=action, investigator_id=action["actor_id"], source="action"
        )
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    # Only a non-dice action or a confirmation gate reached this point.
    pending = (probe.get("combat_state") or {}).get("pending_decision")
    if isinstance(pending, dict):
        pending["source_request_id"] = linked_request_id(ctx, action["actor_id"])
    state.clear()
    state.update(probe)
    return _result(state, result)


def respond_combat_decision(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    combat = _combat(state)
    if state.get("combat_pending_roll"):
        raise StructuredError("invalid_action", "决定已选择，请先掷骰或取消。")
    if state.get("combat_pvp"):
        from .pvp_flow import decide_pvp

        return decide_pvp(state, payload, ctx)
    pending = combat.get("pending_decision")
    if not isinstance(pending, dict) or pending.get("id") != payload.get("decision_id"):
        raise StructuredError("request_not_found", "待确认决定已失效。")
    investigator_id = str(pending.get("responding_investigator_id") or "")
    _require_player(state, ctx, investigator_id)
    if payload.get("option_id") not in {"cancel_violence", "cancel_threat"}:
        require_live_action(
            state,
            pending["action"],
            defender_id=investigator_id if pending.get("kind") == "combat_defense" else "",
        )
    probe = copy.deepcopy(state)
    try:
        result = call_with_weapon(
            probe,
            decision_action(pending),
            lambda _legacy: rules.combat_decide(
                probe, pending["id"], str(payload.get("option_id") or ""), rng=_NoDice()
            ),
            cancelling=payload.get("option_id") in {"cancel_violence", "cancel_threat"},
        )
    except _DiceNeeded:
        return _stage_roll(
            state,
            ctx,
            action=decision_action(pending),
            investigator_id=investigator_id,
            source="decision",
            decision=payload,
            source_request_id=str(pending.get("source_request_id") or ""),
        )
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    state.clear()
    state.update(probe)
    outcome = _result(state, result)
    source = linked_request_id(ctx, investigator_id, str(pending.get("source_request_id") or ""))
    if source:
        outcome.result["source_request_id"] = source
    return outcome


def respond_combat_roll(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    combat = _combat(state)
    if state.get("combat_pvp"):
        from .pvp_flow import roll_pvp

        return roll_pvp(state, payload, ctx)
    pending = state.get("combat_pending_roll")
    if not isinstance(pending, dict) or pending.get("roll_id") != payload.get("roll_id"):
        raise StructuredError("request_not_found", "待掷骰行动已失效。")
    _require_player(state, ctx, pending["investigator_id"])
    if payload.get("response") not in {"roll", "cancel"}:
        raise StructuredError("invalid_action", "请选择掷骰或取消。")
    if payload["response"] == "roll" and pending["conditions"] != _signature(state):
        raise StructuredError("check_conditions_changed", "战斗条件已变化，请主持重新批准动作。")
    probe = copy.deepcopy(state)
    probe.pop("combat_pending_roll")
    probe["combat_state"]["phase"] = (
        "awaiting_decision" if combat.get("pending_decision") else "awaiting_action"
    )
    if payload["response"] == "cancel":
        outcome = _result(
            probe,
            {
                "ok": True,
                "event": "action_cancelled",
                "outcome": "cancelled",
                "action_consumed": False,
            },
        )
    elif pending["source"] == "decision":
        choice = pending["decision"]
        try:
            raw = call_with_weapon(
                probe,
                pending["action"],
                lambda _legacy: rules.combat_decide(
                    probe, choice["decision_id"], choice["option_id"], rng=ContextRandom(ctx)
                ),
            )
        except rules.CombatError as exc:
            raise StructuredError("invalid_action", str(exc)) from exc
        outcome = _result(probe, raw)
    else:
        outcome = execute_combat_action(probe, pending["action"], ctx)
    from .combat_receipts import record_roll_result

    source = linked_request_id(
        ctx, pending["investigator_id"], str(pending.get("source_request_id") or "")
    )
    if source:
        outcome.result["source_request_id"] = source
    receipt = record_roll_result(
        probe, pending, payload["response"], outcome.result, round_number=int(combat["round"])
    )
    # Publish only a fully resolved working copy, even before service wiring.
    state.clear()
    state.update(probe)
    outcome.events.append(
        EventSpec(
            "combat_roll_resolved",
            {
                "roll_id": pending["roll_id"],
                "investigator_id": pending["investigator_id"],
                "response": payload["response"],
                "result": receipt,
            },
            {"kind": "investigators", "investigator_ids": [pending["investigator_id"]]},
        )
    )
    outcome.events.append(
        EventSpec(
            "combat_roll_resolved",
            {
                "roll_id": pending["roll_id"],
                "investigator_id": pending["investigator_id"],
                "response": payload["response"],
                "result": copy.deepcopy(receipt),
            },
            {"kind": "keeper"},
        )
    )
    return outcome
