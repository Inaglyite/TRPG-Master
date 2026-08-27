"""Chat-style preflight for violence against a non-hostile target."""

from __future__ import annotations

from typing import Any

from src.gameplay.combat import preview_player_escalation


def resolve_player_escalation(engine: Any, content: str) -> str | None:
    """Ask for confirmation before any model token, using normal chat UI."""
    try:
        world = engine.context.world_store.load()
    except Exception:
        return content
    preview = preview_player_escalation(world, content)
    if preview is None:
        return content

    decision = preview["decision"]
    warning = "\n\n".join(
        part.strip()
        for part in (str(decision.get("title") or ""), str(decision.get("description") or ""))
        if part.strip()
    )
    if warning:
        engine.cb.on_narrative(f"{warning}\n\n")
    selected = engine.cb.on_decision(decision)
    valid_options = {
        option.get("id") for option in decision.get("options", []) if isinstance(option, dict)
    }
    if selected not in valid_options:
        selected = decision.get("default_option")
    selected_option = next(
        (option for option in decision.get("options", []) if option.get("id") == selected),
        {},
    )
    selected_label = str(selected_option.get("label") or selected or "")
    engine.record_turn_event({"type": "player_reply", "text": selected_label})

    authorization = preview["authorization"]
    if selected != authorization["confirm_option"]:
        return None
    engine._preconfirmed_escalation = authorization
    engine.__dict__["_preflight_narrative"] = warning
    engine.__dict__["_preflight_player_followups"] = [
        {"text": selected_label, "after_narrative_segment": 1}
    ]
    return f"{content}\n{preview['prompt_suffix']}"
