"""Structured combat/ending domain foundations, not yet exposed as commands.

Use the existing rules; never interpret player prose here. The service must
execute these functions on its transaction-local world copy. Protocol wiring,
player roll/decision authority and career settlement are separate milestones.
"""

from __future__ import annotations

import copy
import re

from src.gameplay import combat as rules
from src.gameplay.case_settlement import settle_roster_case
from src.gameplay.endings import validate_ending
from src.storage.database import utcnow

from .combat_vitals import require_live_action
from .combat_weapons import call_with_weapon, decision_action
from .domains import CommandContext, CommandResult, EventSpec
from .errors import StructuredError


class ContextRandom:
    """Bridge rule-engine randint calls to the service's injectable RNG."""

    def __init__(self, ctx: CommandContext):
        self.ctx = ctx

    def randint(self, start: int, end: int) -> int:
        return start + self.ctx.rng(end - start + 1)


def combat_projection(state: dict) -> dict:
    """Public encounter state excludes sheets, tactics and private decisions."""
    combat = state.get("combat_state")
    if not isinstance(combat, dict):
        return {"active": False}
    public = {
        key: copy.deepcopy(combat.get(key))
        for key in (
            "active",
            "encounter_id",
            "round",
            "phase",
            "turn_order",
            "current_actor",
            "outcome",
        )
    }
    public["participants"] = [
        {
            key: copy.deepcopy(participant.get(key))
            for key in (
                "id",
                "name",
                "kind",
                "hp",
                "max_hp",
                "conditions",
            )
        }
        for participant in combat.get("participants", [])
        if isinstance(participant, dict)
    ]
    pending = combat.get("pending_decision")
    # Only the presence of a decision is public. Its personalised warning and
    # options belong to the responding investigator and the keeper.
    public["awaiting_decision"] = isinstance(pending, dict)
    public["awaiting_roll"] = isinstance(state.get("combat_pending_roll"), dict)
    return public


def decision_projection(state: dict) -> dict | None:
    if isinstance(state.get("combat_pending_roll"), dict):
        return None  # the choice has been submitted; only the roll remains actionable
    combat = state.get("combat_state") or {}
    pending = combat.get("pending_decision")
    if not isinstance(pending, dict):
        return None
    return {
        key: copy.deepcopy(pending.get(key))
        for key in (
            "id",
            "kind",
            "title",
            "description",
            "options",
            "default_option",
            "responding_investigator_id",
        )
    }


def _open_game(state: dict) -> None:
    if state.get("game_over"):
        raise StructuredError("invalid_action", "本场游戏已结算，请读档或创建分支后继续。")


def _result(state: dict, result: dict) -> CommandResult:
    # Do not return the legacy full combat snapshot in acknowledgements either.
    # Both ack and event projections must be safe independently of transport.
    events = [EventSpec("combat_updated", combat_projection(state))]
    decision = decision_projection(state)
    if decision:
        recipient = decision.get("responding_investigator_id")
        if not recipient:
            raise StructuredError("invalid_action", "战斗决定缺少响应调查员。")
        events.append(
            EventSpec(
                "combat_decision_required",
                decision,
                {"kind": "investigators", "investigator_ids": [recipient]},
            )
        )
        events.append(EventSpec("combat_decision_required", decision, {"kind": "keeper"}))
    receipt = {
        key: copy.deepcopy(result[key])
        for key in (
            "ok",
            "event",
            "outcome",
            "action_type",
            "actor",
            "target",
            "attack_roll",
            "defense_roll",
            "cover_roll",
            "damage",
            "ammo",
            "action_consumed",
            "requires_decision",
        )
        if key in result
    }
    return CommandResult(result=receipt, events=events)


def start_encounter(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    _open_game(state)
    participants = payload.get("participants")
    if not isinstance(participants, list) or not 1 <= len(participants) <= 32:
        raise StructuredError("invalid_action", "请选择1至32名参战者。")
    present = set((state.get("current_scene") or {}).get("npcs_present") or [])
    investigators = set((state.get("investigators") or {}).keys())
    ids = []
    for spec in participants:
        # Do not accept arbitrary NPC sheet/stat overrides from the model.
        if not isinstance(spec, dict) or set(spec) - {"id", "ready_firearm"}:
            raise StructuredError("invalid_action", "参战者只允许id和ready_firearm。")
        entity_id = spec.get("id")
        if entity_id not in present | investigators | {"pc"}:
            raise StructuredError("object_not_found", "参战者不在当前场景。")
        if "ready_firearm" in spec and not isinstance(spec["ready_firearm"], bool):
            raise StructuredError("invalid_action", "ready_firearm必须为布尔值。")
        ids.append(entity_id)
    if len(set(ids)) != len(ids):
        raise StructuredError("invalid_action", "参战者不能重复。")
    try:
        result = rules.start_combat(state, participants, reason=str(payload.get("reason") or ""))
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    return _result(state, result)


def execute_combat_action(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    """Internal execution primitive; player roll consent is enforced by wiring."""
    _open_game(state)
    validate_combat_action(payload)
    try:
        result = call_with_weapon(
            state,
            payload,
            lambda action: rules.combat_action(state, **action, rng=ContextRandom(ctx)),
        )
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    return _result(state, result)


def validate_combat_action(payload: dict) -> None:
    """Shared bounded validation for preparation and actual execution."""
    allowed = {
        "actor_id",
        "target_id",
        "action_type",
        "description",
        "skill",
        "weapon",
        "weapon_item_id",
        "damage_spec",
        "damage_mode",
        "defender_choice",
        "bonus_dice",
        "penalty_dice",
    }
    if set(payload) - allowed:
        raise StructuredError("invalid_action", "未知战斗行动字段。")
    if not payload.get("actor_id") or not payload.get("action_type"):
        raise StructuredError("invalid_action", "缺少行动者或行动类型。")
    item_id = payload.get("weapon_item_id")
    if item_id is not None and (not isinstance(item_id, str) or not 1 <= len(item_id) <= 160):
        raise StructuredError("invalid_action", "weapon_item_id 必须为有效的物品编号。")
    damage = payload.get("damage_spec")
    if damage is not None:
        match = re.fullmatch(r"(\d{0,2})d(\d{1,3})([+-]\d{1,3})?", str(damage))
        if (
            not match
            or not 1 <= int(match[1] or 1) <= 10
            or not 2 <= int(match[2]) <= 100
            or not -100 <= int(match[3] or 0) <= 100
        ):
            raise StructuredError(
                "invalid_action", "伤害骰必须为1至10个、每骰2至100面，修正值不超过100。"
            )
    for key in ("bonus_dice", "penalty_dice"):
        value = payload.get(key, 0)
        if type(value) is not int or not 0 <= value <= 2:
            raise StructuredError("invalid_action", "奖励/惩罚骰必须为0至2。")


def resolve_combat_decision(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    _open_game(state)
    pending = (state.get("combat_state") or {}).get("pending_decision") or {}
    if pending.get("id") != payload.get("decision_id"):
        raise StructuredError("request_not_found", "待确认决定已失效。")
    cancelling = payload.get("option_id") in {"cancel_violence", "cancel_threat"}
    if not cancelling:
        require_live_action(
            state,
            pending["action"],
            defender_id=str(pending.get("responding_investigator_id") or "")
            if pending.get("kind") == "combat_defense"
            else "",
        )
    try:
        result = call_with_weapon(
            state,
            decision_action(pending),
            lambda _action: rules.combat_decide(
                state,
                str(payload.get("decision_id") or ""),
                str(payload.get("option_id") or ""),
                rng=ContextRandom(ctx),
            ),
            cancelling=cancelling,
        )
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    return _result(state, result)


def finish_encounter(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    _open_game(state)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise StructuredError("invalid_action", "结束战斗须说明原因。")
    try:
        result = rules.end_combat(state, reason)
    except rules.CombatError as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    state.pop("combat_pending_roll", None)
    state.pop("combat_pvp", None)
    return _result(state, result)


def finish_game(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    _open_game(state)
    if (state.get("combat_state") or {}).get("active"):
        raise StructuredError("invalid_action", "先结算或明确结束当前战斗，再结算结局。")
    resolution = validate_ending(state, payload)
    if not resolution.get("ok"):
        raise StructuredError("invalid_action", str(resolution.get("error")))
    ending = {
        "id": resolution.get("ending_id"),
        "type": resolution["ending_type"],
        "title": resolution["title"],
        "summary": resolution["summary"],
    }
    try:
        settlements = settle_roster_case(
            state,
            world_id=ctx.world_id,
            ending=ending,
            completed_at=utcnow().isoformat(),
        )
    except (ValueError, TypeError) as exc:
        raise StructuredError("invalid_action", str(exc)) from exc
    events = [EventSpec("game_ended", copy.deepcopy(ending))]
    for investigator_id, receipt in settlements.items():
        events.append(
            EventSpec(
                "case_settled",
                receipt,
                {"kind": "investigators", "investigator_ids": [investigator_id]},
            )
        )
        events.append(EventSpec("case_settled", copy.deepcopy(receipt), {"kind": "keeper"}))
    state["game_over"] = ending
    from .case_closure import close_case_work

    events.extend(close_case_work(ctx))
    return CommandResult(
        result={"game_over": copy.deepcopy(ending), "settled_investigator_ids": list(settlements)},
        events=events,
    )
