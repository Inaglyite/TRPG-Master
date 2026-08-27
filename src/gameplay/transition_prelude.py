"""Player-visible authored beats around one deterministic scene transition."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.gameplay.action_resolution import ActionResolution


def build_transition_prelude(
    world: dict,
    action: ActionResolution,
    scene_id: str | None,
    discovery_matches: Iterable[Any],
) -> str:
    """Order departure, travel, arrival, entry, then local approach beats."""
    parts: list[str] = []
    if scene_id:
        scenes = world.get("scene_catalog", {})
        scene = scenes.get(scene_id, {}) if isinstance(scenes, dict) else {}
        scene = scene if isinstance(scene, dict) else {}

        departure = str(action.departure_text or "").strip()
        travel = str(action.travel_text or "").strip()
        legacy_entry = str(action.entry_text or "").strip()
        if departure:
            parts.append(departure)
        if travel:
            parts.append(travel)

        name = str(scene.get("name") or "").strip()
        description = str(scene.get("description") or "").strip()
        if name:
            parts.append(f"你前往{name}。{description}" if description else f"你前往{name}。")
        if legacy_entry:
            parts.append(legacy_entry)

    for match in discovery_matches:
        approach = str(match.rule.get("approach_text") or "").strip()
        if approach and approach not in parts:
            parts.append(approach)
    return "\n\n".join(parts)


def build_scene_entry_beat(world: dict, scene_id: str) -> str:
    """Build an entry beat only after actual encounter presence is committed."""
    scenes = world.get("scene_catalog", {})
    scene = scenes.get(scene_id, {}) if isinstance(scenes, dict) else {}
    if not isinstance(scene, dict):
        return ""
    entry_beat = scene.get("entry_beat")
    if not isinstance(entry_beat, dict):
        return ""
    current_scene = world.get("current_scene", {})
    present = {
        str(value)
        for value in (
            current_scene.get("npcs_present", [])
            if isinstance(current_scene, dict)
            else []
        )
    }
    npc_id = str(entry_beat.get("npc_id") or "")
    text = str(entry_beat.get("public_text") or "").strip()
    return text[:1200] if npc_id in present and text else ""
