"""Translate spatial receipts through existing engine handout delivery."""

import json


def deliver_handouts(engine, output):
    try:
        data = json.loads(output)
    except (ValueError, TypeError):
        return
    if data.get("ok") is not True:
        return
    for clue in data.get("clues") or []:
        engine._handle_tool_handouts(
            "state_add_clue",
            {"clue_id": clue.get("id"), "category": clue.get("category", "investigation")},
            json.dumps({"ok": True, "clue": clue}, ensure_ascii=False),
        )
