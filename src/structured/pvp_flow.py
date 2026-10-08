"""Player-to-player combat requires participation, defence and roll consent.

Both players explicitly ready their rolls; RNG and damage settle together only
after the second ready response. This avoids revealing one side's dice before
the other side commits, and reuses the existing combat rules unchanged.
"""

import copy

from sqlalchemy import select

from src.gameplay import combat as rules
from src.gameplay.investigators import investigator_controller_user_id
from src.storage.database import World, WorldInvestigator

from .combat_endings import _result, execute_combat_action
from .combat_flow import _combat, _DiceNeeded, _NoDice, _require_player, _signature, _stage_roll
from .combat_receipts import record_roll_result
from .combat_requests import linked_request_id
from .combat_weapons import call_with_weapon
from .domains import EventSpec
from .errors import StructuredError
from .ids import new_stable_id
from .principal import resolve_player_principal


def _conditions(state):
    copied = copy.deepcopy(state)
    copied["combat_state"]["pending_decision"] = None
    return _signature(copied)


def _controller(state, ctx, investigator_id):
    if ctx.session is not None:
        claim = ctx.session.scalar(
            select(WorldInvestigator).where(
                WorldInvestigator.world_id == ctx.world_id,
                WorldInvestigator.character_key == investigator_id,
                WorldInvestigator.status == "claimed",
            )
        )
        if claim and claim.controller_user_id:
            return claim.controller_user_id
        from .bootstrap import LOCAL_OPERATOR_USER_ID, local_player_investigator_ids

        world = ctx.session.get(World, ctx.world_id)
        if (
            world
            and world.created_by in {None, "", LOCAL_OPERATOR_USER_ID}
            and investigator_id in local_player_investigator_ids(ctx.session, ctx.world_id, state)
        ):
            return LOCAL_OPERATOR_USER_ID
        raise StructuredError("not_authorized", "对抗双方都必须由玩家控制。")
    controller = investigator_controller_user_id(state, investigator_id)
    if not controller:
        raise StructuredError("not_authorized", "对抗双方都必须由玩家控制。")
    return controller


def _verify(state, ctx, pending):
    if _conditions(state) != pending["conditions"]:
        raise StructuredError("check_conditions_changed", "对抗条件已变化，请主持重新批准动作。")
    for investigator_id, user_id in pending["controllers"].items():
        if _controller(state, ctx, investigator_id) != user_id:
            raise StructuredError("not_authorized", "对抗参与者控制权已变化，请重新批准。")
        if ctx.session is not None:
            from .bootstrap import LOCAL_OPERATOR_USER_ID, local_player_investigator_ids

            allowed = (
                local_player_investigator_ids(ctx.session, ctx.world_id, state)
                if user_id == LOCAL_OPERATOR_USER_ID
                else resolve_player_principal(ctx.session, ctx.world_id, user_id).investigator_ids
            )
            if investigator_id not in allowed:
                raise StructuredError("not_authorized", "对抗参与者已失去访问或角色控制权。")


def _clear(state):
    state.pop("combat_pvp", None)
    state.pop("combat_pending_roll", None)
    state["combat_state"]["pending_decision"] = None
    state["combat_state"]["phase"] = "awaiting_action"


def _source(state, ctx):
    pvp = state["combat_pvp"]
    return linked_request_id(ctx, pvp["action"]["actor_id"], pvp["source_request_id"])


def _cancel(state, ctx):
    source = _source(state, ctx)
    _clear(state)
    outcome = _result(
        state,
        {"ok": True, "event": "action_cancelled", "outcome": "cancelled", "action_consumed": False},
    )
    if source:
        outcome.result["source_request_id"] = source
    return outcome


def _consent(state, responding_id, *, first):
    pending = state["combat_pvp"]
    action = pending["action"]
    state["combat_state"]["pending_decision"] = {
        "id": new_stable_id("pvp-decision"),
        "kind": "pvp_consent",
        "title": "确认与另一位调查员对抗" if first else "是否参与这次调查员对抗？",
        "description": "只有双方明确同意才会进行对抗；同意后仍需选择防御并分别确认掷骰。取消不会消耗行动、弹药或生命。",
        "options": [
            {"id": "cancel_confrontation", "label": "不参与对抗"},
            {"id": "confirm_confrontation", "label": "同意参与对抗"},
        ],
        "default_option": "cancel_confrontation",
        "responding_investigator_id": responding_id,
        "action": copy.deepcopy(action),
    }
    state["combat_state"]["phase"] = "awaiting_decision"
    return _result(state, {"ok": True, "event": "decision_required", "requires_decision": True})


def prepare_pvp(state, action, ctx):
    if action["actor_id"] == action["target_id"]:
        raise StructuredError("invalid_action", "不能把自己设为调查员对抗目标。")
    if action.get("defender_choice"):
        raise StructuredError("not_authorized", "防御方式必须由被攻击玩家自己选择。")
    probe = copy.deepcopy(state)
    try:
        call_with_weapon(
            probe, action, lambda legacy: rules.combat_action(probe, **legacy, rng=_NoDice())
        )
    except _DiceNeeded:
        pass
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    state["combat_pvp"] = {
        "action": copy.deepcopy(action),
        "stage": "attacker_consent",
        "controllers": {
            key: _controller(state, ctx, key) for key in [action["actor_id"], action["target_id"]]
        },
        "conditions": _conditions(state),
        "source_request_id": linked_request_id(ctx, action["actor_id"]),
    }
    return _consent(state, action["actor_id"], first=True)


def decide_pvp(state, payload, ctx):
    combat = _combat(state)
    pending = state["combat_pvp"]
    decision = combat.get("pending_decision") or {}
    if decision.get("id") != payload.get("decision_id"):
        raise StructuredError("request_not_found", "对抗决定已失效。")
    responding_id = decision["responding_investigator_id"]
    _require_player(state, ctx, responding_id)
    option = payload.get("option_id")
    if option not in {o["id"] for o in decision["options"]}:
        raise StructuredError("invalid_action", "无效的对抗决定。")
    if option == "cancel_confrontation":
        return _cancel(state, ctx)  # Can cancel even after conditions/control changed.
    _verify(state, ctx, pending)
    action = pending["action"]
    if pending["stage"] == "attacker_consent":
        pending["stage"] = "target_consent"
        return _consent(state, action["target_id"], first=False)
    if pending["stage"] == "target_consent":
        if action["action_type"] == "threat":
            source = _source(state, ctx)
            _clear(state)
            outcome = execute_combat_action(state, action, ctx)
            if source:
                outcome.result["source_request_id"] = source
            return outcome
        actor = next(p for p in combat["participants"] if p["id"] == action["actor_id"])
        target = next(p for p in combat["participants"] if p["id"] == action["target_id"])
        rules._request_player_defense(combat, copy.deepcopy(action), actor, target)
        pending["stage"] = "defense_choice"
        return _result(state, {"ok": True, "event": "decision_required", "requires_decision": True})
    if pending["stage"] != "defense_choice":
        raise StructuredError("invalid_action", "请等待指定调查员确认掷骰。")
    pending["action"]["defender_choice"] = option
    pending["stage"] = "defense_ready"
    return _stage_roll(
        state,
        ctx,
        action=pending["action"],
        investigator_id=action["target_id"],
        source="pvp_defense",
    )


def _resolved_event(state, pending, response, result, round_number):
    receipt = record_roll_result(state, pending, response, result, round_number=round_number)
    payload = {
        "roll_id": pending["roll_id"],
        "investigator_id": pending["investigator_id"],
        "response": response,
        "result": receipt,
    }
    return [
        EventSpec(
            "combat_roll_resolved",
            payload,
            {"kind": "investigators", "investigator_ids": [pending["investigator_id"]]},
        ),
        EventSpec("combat_roll_resolved", copy.deepcopy(payload), {"kind": "keeper"}),
    ]


def roll_pvp(state, payload, ctx):
    combat = _combat(state)
    pvp = state["combat_pvp"]
    pending = state.get("combat_pending_roll") or {}
    if pending.get("roll_id") != payload.get("roll_id"):
        raise StructuredError("request_not_found", "对抗掷骰已失效。")
    _require_player(state, ctx, pending["investigator_id"])
    response = payload.get("response")
    if response not in {"roll", "cancel"}:
        raise StructuredError("invalid_action", "请选择确认掷骰或取消。")
    round_number = int(combat["round"])
    if response == "cancel":
        outcome = _cancel(state, ctx)
        for record in [pending, pvp.get("defense_ready")]:
            if record:
                outcome.events.extend(
                    _resolved_event(state, record, "cancel", outcome.result, round_number)
                )
        return outcome
    _verify(state, ctx, pvp)
    if pvp["stage"] == "defense_ready":
        pvp["defense_ready"] = copy.deepcopy(pending)
        pvp["stage"] = "attack_ready"
        combat["pending_decision"] = None
        state.pop("combat_pending_roll")
        outcome = _stage_roll(
            state,
            ctx,
            action=pvp["action"],
            investigator_id=pvp["action"]["actor_id"],
            source="pvp_attack",
            source_request_id=pvp["source_request_id"],
        )
        # This event retires a ready button, but carries no invented dice result.
        for audience in [
            {"kind": "investigators", "investigator_ids": [pending["investigator_id"]]},
            {"kind": "keeper"},
        ]:
            outcome.events.append(
                EventSpec(
                    "combat_roll_resolved",
                    {
                        "roll_id": pending["roll_id"],
                        "investigator_id": pending["investigator_id"],
                        "response": "roll",
                    },
                    audience,
                )
            )
        outcome.result["event"] = "waiting_other_player"
        return outcome
    if pvp["stage"] != "attack_ready" or not pvp.get("defense_ready"):
        raise StructuredError("invalid_action", "尚未取得双方掷骰确认。")
    probe = copy.deepcopy(state)
    _clear(probe)
    outcome = execute_combat_action(probe, pvp["action"], ctx)
    source = linked_request_id(ctx, pvp["action"]["actor_id"], pvp["source_request_id"])
    if source:
        outcome.result["source_request_id"] = source
    for record in [pending, pvp["defense_ready"]]:
        outcome.events.extend(_resolved_event(probe, record, "roll", outcome.result, round_number))
    state.clear()
    state.update(probe)
    return outcome
