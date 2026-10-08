"""Explicit human rulings on declared narrative flags, not an arbitrary state setter."""

import copy
import json

from src.gameplay.endings import _ending_map, eligible_endings, validate_ending

from .domains import CommandContext, CommandResult, EventSpec
from .errors import StructuredError


def _value_type(value):
    if type(value) is bool:
        return "boolean"
    if type(value) is int and -1_000_000 <= value <= 1_000_000:
        return "integer"
    if type(value) is str and len(value) <= 200:
        return "string"
    return None


def flag_catalog(state: dict) -> list[dict]:
    """Current flags and authored ending requirements declare primitive types."""
    flags = state.get("flags") or {}
    types: dict[str, set[str]] = {}
    for key, value in flags.items():
        if kind := _value_type(value):
            types.setdefault(key, set()).add(kind)
    for ending in state.get("endings") or []:
        for key, value in (ending.get("required_flags") or {}).items():
            if kind := _value_type(value):
                types.setdefault(key, set()).add(kind)
    return [
        {"id": key, "type": next(iter(kinds)), "value": copy.deepcopy(flags.get(key))}
        for key, kinds in sorted(types.items())
        if len(kinds) == 1
        and 0 < len(key) <= 160
        and (key not in flags or _value_type(flags[key]) is not None)
    ]


def ending_catalog(state: dict) -> list[dict]:
    """Author facts and actual validator readiness, without state mutation.

    Textual JSON values keep bool/number/string distinctions readable, even for
    historical flags outside the bounded human-ruling value type. Missing is
    explicit, not silently displayed as false. No conditions are auto-filled.
    """
    flags = state.get("flags") or {}
    catalogue = []
    for ending_id, definition in _ending_map(state).items():
        eligible = bool(validate_ending(state, {"ending_id": ending_id}).get("ok"))
        reason = (
            "本场游戏已结算。"
            if state.get("game_over")
            else "请先结算或明确结束当前战斗。"
            if (state.get("combat_state") or {}).get("active")
            else "结局前置条件尚未满足。"
            if not eligible
            else ""
        )
        catalogue.append(
            {
                "id": ending_id,
                "title": str(definition.get("title") or ending_id),
                "ending_type": definition.get("ending_type", "neutral"),
                "description": str(definition.get("description") or ""),
                "trigger": str(definition.get("trigger") or ""),
                "eligible": eligible,
                "can_prepare": not bool(reason),
                "blocked_reason": reason,
                "conditions": [
                    {
                        "flag_id": str(key),
                        "expected_text": json.dumps(expected, ensure_ascii=False),
                        "current_text": json.dumps(flags[key], ensure_ascii=False)
                        if key in flags
                        else "",
                        "recorded": key in flags,
                        "satisfied": flags.get(key) == expected,
                    }
                    for key, expected in (definition.get("required_flags") or {}).items()
                ],
            }
        )
    return catalogue


def ruling_projection(state: dict) -> dict:
    return {
        "flags": flag_catalog(state),
        "recent": copy.deepcopy(state.get("keeper_rulings") or [])[-100:],
        "eligible_endings": eligible_endings(state),
        "ending_catalog": ending_catalog(state),
    }


def record_ruling(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    if ctx.principal.kind != "keeper":
        raise StructuredError("keeper_required", "剧情裁定只允许已授权的人类主持执行。")
    if state.get("game_over"):
        raise StructuredError("invalid_action", "游戏已结算，不能补写结局前置裁定。")
    key = payload.get("flag_id")
    declaration = next((entry for entry in flag_catalog(state) if entry["id"] == key), None)
    if declaration is None:
        raise StructuredError("unknown_target", "请选择模组已声明且类型明确的剧情状态。")
    value = payload.get("value")
    if _value_type(value) != declaration["type"]:
        raise StructuredError("invalid_action", "新值的类型必须与模组状态声明一致。")
    current = declaration["value"]
    expected = payload.get("expected_before")
    if type(current) is not type(expected) or current != expected:
        raise StructuredError(
            "ruling_conflict", "剧情状态已变化，请重新查看旧值后裁定。", retryable=True
        )
    basis = str(payload.get("basis") or "").strip()
    if not 1 <= len(basis) <= 1000:
        raise StructuredError("invalid_action", "必须填写裁定依据（1至1000字）。")
    entry = {
        "flag_id": key,
        "before": current,
        "after": copy.deepcopy(value),
        "basis": basis,
        "user_id": ctx.principal.user_id,
        "revision": ctx.revision + 1,
    }
    state.setdefault("flags", {})[key] = copy.deepcopy(value)
    history = state.setdefault("keeper_rulings", [])
    history.append(entry)
    del history[:-100]
    return CommandResult(
        result={"status": "success", "ruling": copy.deepcopy(entry)},
        events=[EventSpec("ruling_recorded", ruling_projection(state), {"kind": "keeper"})],
    )
