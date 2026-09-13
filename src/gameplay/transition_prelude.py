"""Player-visible authored beats around one deterministic scene transition."""

from __future__ import annotations

import copy
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
            current_scene.get("npcs_present", []) if isinstance(current_scene, dict) else []
        )
    }
    npc_id = str(entry_beat.get("npc_id") or "")
    text = str(entry_beat.get("public_text") or "").strip()
    return text[:1200] if npc_id in present and text else ""


def upgrade_legacy_entry_beats(state: dict, template: dict) -> list[str]:
    """Upgrade unmodified legacy entry beats to the module's current wording.

    Worlds seeded from pre-fix snapshots keep the old ``entry_beat`` wording:
    the whole-catalog refresh only fires when the recorded module file revision
    changes, and the advisory-level migration never touched scene entry beats.
    An old beat such as "医生正攥着病历夹等候" implies a prior arrangement,
    which contradicts the upgraded keeper fallback beat ("还没有安排，需要
    许可") whenever the notifying NPC is absent.

    Same exact-match philosophy as
    ``action_preflight.upgrade_legacy_advisories``: a world's entry beat is
    replaced only when it structurally equals one of the legacy payloads the
    author archived in the template beat's ``supersedes`` list.  Any hand edit
    at all keeps the world's wording untouched; template scenes without
    ``supersedes`` are no-ops.  The replacement drops the ``supersedes``
    bookkeeping, which keeps the step idempotent.  Only the entry beat text is
    rewritten: no progress, history, flags or scene state is touched.
    """

    upgraded: list[str] = []
    scenes = state.get("scene_catalog")
    template_scenes = template.get("scene_catalog")
    if not isinstance(scenes, dict) or not isinstance(template_scenes, dict):
        return upgraded

    for scene_id, scene in scenes.items():
        if not isinstance(scene, dict):
            continue
        beat = scene.get("entry_beat")
        template_scene = template_scenes.get(scene_id)
        if not isinstance(beat, dict) or not isinstance(template_scene, dict):
            continue
        template_beat = template_scene.get("entry_beat")
        if not isinstance(template_beat, dict):
            continue
        declared = template_beat.get("supersedes")
        if not isinstance(declared, list) or not any(
            isinstance(payload, dict) and payload and beat == payload for payload in declared
        ):
            continue
        scene["entry_beat"] = {
            key: copy.deepcopy(value) for key, value in template_beat.items() if key != "supersedes"
        }
        upgraded.append(f"{scene_id}/entry_beat")
    return upgraded
