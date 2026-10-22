"""Configured ending resolution and prerequisite validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def ending_readiness(state: dict) -> dict:
    """Keeper-private candidates and their exact state source; never a completion command."""
    flags = state.get("flags") or {}
    blockers = []
    if (state.get("combat_state") or {}).get("active"):
        blockers.append("combat_active")
    if state.get("pending_departure"):
        blockers.append("departure_pending")
    if state.get("combat_pending_roll"):
        blockers.append("combat_roll_pending")
    source = {key: state.get(key) for key in
              ("module_meta", "module", "module_version", "endings", "flags", "game_over",
               "combat_state", "pending_departure", "combat_pending_roll")}
    token = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return {"source_token": token, "source_revision": state.get("revision"),
            "module": state.get("module_meta") or {"id": state.get("module"), "version": state.get("module_version")},
            "blockers": blockers, "already_ended": bool(state.get("game_over")),
            "candidates": [{"id": ending_id,
                            "eligible": all(flags.get(key) == value for key, value in (ending.get("required_flags") or {}).items()),
                            "conditions": [{"flag_id": key, "expected": value, "recorded": key in flags,
                                            "actual": flags.get(key), "source": f"flags.{key}"}
                                           for key, value in (ending.get("required_flags") or {}).items()]}
                           for ending_id, ending in _ending_map(state).items()]}


def _ending_map(state: dict) -> dict[str, dict]:
    return {
        str(ending.get("id")): ending
        for ending in state.get("endings", [])
        if isinstance(ending, dict) and ending.get("id")
    }


def _select_ending(state: dict, args: dict) -> tuple[str | None, dict | None, str | None]:
    endings = _ending_map(state)
    if not endings:
        return None, None, None

    requested_id = str(args.get("ending_id") or "").strip()
    if requested_id:
        ending = endings.get(requested_id)
        if ending is None:
            available = ", ".join(sorted(endings))
            return None, None, f"模组不存在结局 {requested_id!r}；可用结局 ID: {available}"
        return requested_id, ending, None

    title = str(args.get("title") or "").strip()
    exact = [
        (ending_id, ending)
        for ending_id, ending in endings.items()
        if str(ending.get("title") or "").strip() == title
    ]
    if len(exact) == 1:
        return exact[0][0], exact[0][1], None

    ending_type = str(args.get("ending_type") or "neutral")
    same_type = [
        (ending_id, ending)
        for ending_id, ending in endings.items()
        if ending.get("ending_type", "neutral") == ending_type
    ]
    if len(same_type) == 1:
        return same_type[0][0], same_type[0][1], None
    return None, None, "模组定义了多个结局，请提供 ending_id"


def eligible_endings(state: dict) -> list[dict]:
    """Endings whose authored required_flags are all satisfied right now."""
    flags = state.get("flags") or {}
    return [
        {
            "id": str(ending.get("id")),
            "title": str(ending.get("title") or ""),
            "ending_type": ending.get("ending_type", "neutral"),
        }
        for ending in state.get("endings", [])
        if isinstance(ending, dict)
        and ending.get("id")
        and all(flags.get(key) == value for key, value in (ending.get("required_flags") or {}).items())
    ]


def validate_ending(state: dict, args: dict) -> dict[str, Any]:
    """Resolve a configured ending and verify its authoritative flags."""
    ending_id, definition, error = _select_ending(state, args)
    if error:
        return {"ok": False, "error": error}
    if definition is None:
        return {
            "ok": True,
            "ending_id": None,
            "ending_type": args.get("ending_type", "neutral"),
            "title": args.get("title", "故事结束"),
            "summary": args.get("summary", ""),
        }

    flags = state.get("flags", {})
    required = definition.get("required_flags", {})
    missing = {
        key: expected
        for key, expected in required.items()
        if flags.get(key) != expected
    }
    if missing:
        details = ", ".join(
            f"{key}={expected!r}（当前 {flags.get(key)!r}）"
            for key, expected in missing.items()
        )
        return {
            "ok": False,
            "ending_id": ending_id,
            "error": f"结局前置条件尚未满足: {details}",
            "missing_flags": missing,
        }

    return {
        "ok": True,
        "ending_id": ending_id,
        "ending_type": definition.get("ending_type", args.get("ending_type", "neutral")),
        "title": definition.get("title", args.get("title", "故事结束")),
        "summary": args.get("summary") or definition.get("description", ""),
    }
