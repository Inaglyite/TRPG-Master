"""Deterministic scene encounter resolution for authored modules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class EncounterOutcome:
    encounter_id: str
    npc_id: str
    present: bool
    availability: str
    text: str = ""
    check_result: dict | None = None
    cached: bool = False


@dataclass(frozen=True)
class SceneEncounterResolution:
    scene_id: str
    present_npc_ids: tuple[str, ...]
    outcomes: tuple[EncounterOutcome, ...]

    @property
    def narrative_text(self) -> str:
        return "\n\n".join(outcome.text for outcome in self.outcomes if outcome.text)


def _conditions_match(rule: dict, flags: dict) -> bool:
    required = rule.get("required_flags", {})
    forbidden = rule.get("forbidden_flags", {})
    return (
        isinstance(required, dict)
        and isinstance(forbidden, dict)
        and all(flags.get(key) == value for key, value in required.items())
        and all(flags.get(key) != value for key, value in forbidden.items())
    )


def resolve_scene_encounters(
    scene_id: str,
    world: dict,
    *,
    luck_check: Callable[[str], dict] | None = None,
) -> SceneEncounterResolution:
    """Resolve actual presence without mutating NPC locations or leaking them."""
    scene = (world.get("scene_catalog", {}) or {}).get(scene_id, {})
    rules = scene.get("encounters", []) if isinstance(scene, dict) else []
    if not isinstance(rules, list):
        rules = []
    authored_ids = {
        str(rule.get("npc_id") or "")
        for rule in rules
        if isinstance(rule, dict)
    }
    present = {
        str(npc.get("id"))
        for npc in world.get("npcs", [])
        if isinstance(npc, dict)
        and npc.get("id")
        and str(npc.get("current_location") or "") == scene_id
        and str(npc.get("id")) not in authored_ids
    }
    flags = world.get("flags", {})
    flags = flags if isinstance(flags, dict) else {}
    outcomes: list[EncounterOutcome] = []
    history = world.get("encounter_history", {})
    scene_history = history.get(scene_id, {}) if isinstance(history, dict) else {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        npc_id = str(rule.get("npc_id") or "")
        encounter_id = str(rule.get("id") or npc_id)
        availability = str(rule.get("availability") or "guaranteed")
        repeat = str(rule.get("repeat") or "once")
        cached_result = (
            scene_history.get(encounter_id)
            if repeat == "once" and isinstance(scene_history, dict)
            else None
        )
        if isinstance(cached_result, dict) and "present" in cached_result:
            is_present = bool(cached_result["present"])
            if is_present:
                present.add(npc_id)
            text_key = "on_present_text" if is_present else "on_absent_text"
            outcomes.append(EncounterOutcome(
                encounter_id=encounter_id,
                npc_id=npc_id,
                present=is_present,
                availability=availability,
                text=str(rule.get(text_key) or "").strip(),
                check_result=cached_result.get("check_result"),
                cached=True,
            ))
            continue
        eligible = _conditions_match(rule, flags)
        check_result = None
        is_present = False
        if eligible and availability == "guaranteed":
            is_present = True
        elif eligible and availability == "conditional":
            is_present = True
        elif eligible and availability == "luck" and luck_check:
            difficulty = str(rule.get("luck_difficulty") or "regular")
            check_result = luck_check(difficulty)
            is_present = bool(check_result.get("success"))
        text_key = "on_present_text" if is_present else "on_absent_text"
        text = str(rule.get(text_key) or "").strip()
        if is_present:
            present.add(npc_id)
        outcomes.append(EncounterOutcome(
            encounter_id=encounter_id,
            npc_id=npc_id,
            present=is_present,
            availability=availability,
            text=text,
            check_result=check_result,
        ))
    return SceneEncounterResolution(
        scene_id=scene_id,
        present_npc_ids=tuple(sorted(present)),
        outcomes=tuple(outcomes),
    )
