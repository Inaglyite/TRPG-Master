"""Private authored clue catalogue and live case-clock projection for keepers."""

import json

from .discoveries import condition_met, required_flags


def keeper_progress(state: dict) -> dict:
    catalog = state.get("clue_catalog") or {}
    registered = (state.get("clue_registry") or {}).get("clues") or {}
    items = (state.get("item_registry") or {}).get("items") or {}
    flags = state.get("flags") or {}
    clues = []
    for clue_id, entry in sorted(catalog.items()):
        if not isinstance(entry, dict):
            continue
        acquired = (state.get("structured_acquisitions") or {}).get(clue_id) or {}
        item = items.get(acquired.get("item_id")) or {}
        rules = []
        for index, rule in enumerate(entry.get("discovery_rules") or []):
            if not isinstance(rule, dict):
                continue
            rules.append(
                {
                    "index": index,
                    "intent": str(rule.get("intent") or ""),
                    "skill": str(rule.get("skill") or ""),
                    "difficulty": str(rule.get("difficulty") or "regular"),
                    "requires_success": rule.get("requires_success") is True,
                    "approach": str(rule.get("approach_text") or rule.get("approach") or ""),
                    "sanity_note": str(
                        rule.get("sanity_reason") or rule.get("sanity_severity") or ""
                    ),
                    "conditions": [
                        {
                            "flag_id": str(key),
                            "expected_text": json.dumps(value, ensure_ascii=False),
                            "current_text": json.dumps(flags[key], ensure_ascii=False)
                            if key in flags
                            else "",
                            "satisfied": condition_met(rule, flags, key, value),
                        }
                        for key, value in required_flags(rule).items()
                    ],
                }
            )
        clues.append(
            {
                "id": clue_id,
                "category": str(entry.get("category") or "investigation"),
                "text": str(entry.get("text") or ""),
                "discovered": clue_id in registered,
                "granted_item": str(entry.get("granted_item") or ""),
                "item_id": str(item.get("item_id") or ""),
                "holder_id": str((item.get("holder") or {}).get("id") or ""),
                "related_scenes": [str(s) for s in entry.get("related_scenes") or []],
                "rules": rules,
            }
        )
    clocks = []
    values, definitions = state.get("case_clocks") or {}, state.get("case_clock_definitions") or {}
    for clock_id, definition in sorted(definitions.items()):
        if not isinstance(definition, dict):
            continue
        value, maximum = values.get(clock_id), definition.get("max")
        levels = definition.get("levels") or {}
        clocks.append(
            {
                "id": clock_id,
                "title": str(definition.get("name") or clock_id),
                "value": value if type(value) is int else None,
                "max": maximum if type(maximum) is int and maximum >= 0 else None,
                "level": str(levels.get(str(value)) or ""),
                "next_level": str(levels.get(str(value + 1)) or "") if type(value) is int else "",
                "advance_when": [str(s) for s in definition.get("advance_when") or []],
            }
        )
    return {"clues": clues, "clocks": clocks}
