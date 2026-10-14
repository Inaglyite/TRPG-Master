"""Require a mechanical proposal before narrating an unresolved combat action."""

from __future__ import annotations

import json


def needs_combat_tool(host, role: str, thinking_type: str | None, tools: list[dict]) -> bool:
    # DeepSeek does not support required tool choice while thinking is enabled.
    # Limit this gate to the explicitly non-thinking execution route.
    if role != "combat" or thinking_type != "disabled":
        return False
    if not any(tool["function"]["name"] == "combat_action" for tool in tools):
        return False
    action = getattr(host, "_action_resolution", None)
    try:
        plan = json.loads(getattr(action, "adjudication_json", "") or "{}")
    except (ValueError, TypeError):
        return False
    if plan.get("intent") != "combat":
        return False
    ledger = getattr(host, "_tool_pipeline_ledger", None)
    for outcome in getattr(ledger, "outcomes", ()):
        if outcome.name not in {"combat_action", "combat_end", "combat_decide"}:
            continue
        try:
            result = json.loads(outcome.output)
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and result.get("ok") and result.get("event") in {"action_resolved", "combat_ended"}:
            return False
    return True
