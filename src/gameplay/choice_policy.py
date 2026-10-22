"""Validate suggested targets against facts, never interpret player free text."""

from __future__ import annotations

import re

from src.gameplay.adjudication_policy import check_conditions
from src.gameplay.guest_speakers import registered_guest_ids
from src.gameplay.turn_projection import known_clue_ids


def filter_choice_targets(choices: list[str], targets: list[dict], world: dict,
                          public_text: str, *, extra_present: set[str] | None = None) -> tuple[list[int], list[dict]]:
    """Return original one-based indices, preserving unknown/free action options.

    References are proposals. They grant no state-changing capability. Exact
    private fragments are guarded separately from semantic target validation.
    """
    from src.ai.context.context_summary import protected_summary_fragments

    references = {}
    for target in targets:
        index = target.get("choice_index")
        if type(index) is int and 1 <= index <= len(choices):
            references.setdefault(index, target)
    scene = world.get("current_scene") or {}
    present = set(scene.get("npcs_present") or []) | registered_guest_ids(world, scene.get("id")) | (extra_present or set())
    known = known_clue_ids(world)
    people = {npc.get("id"): npc for npc in world.get("npcs", []) if isinstance(npc, dict)}
    current_conditions = check_conditions(world)
    latest = {row.get("goal_id"): row for row in (world.get("pc") or {}).get("_check_history", [])
              if isinstance(row, dict) and row.get("condition_snapshot") == current_conditions}
    completed = {goal_id for goal_id, row in latest.items()
                 if row.get("goal_achieved") or row.get("success")}
    public_compact = re.sub(r"\s+", "", public_text).casefold()
    protected = [fragment for fragment in protected_summary_fragments(world)
                 if len(fragment) >= 8 and fragment not in public_compact]
    kept, dropped = [], []
    for index, label in enumerate(choices, 1):
        reference = references.get(index)
        kind = reference.get("kind") if reference else None
        target_id = reference.get("target_id") if reference else None
        reason = None
        # Exact authored secrets are not valid menu text, including metadata-free
        # old output. This is not an action-verb keyword matcher.
        compact = re.sub(r"\s+", "", label).casefold()
        if any(fragment in compact for fragment in protected):
            reason = "private_information"
        elif kind == "npc" and target_id not in present:
            reason = "speaker_not_present"
        elif kind == "npc" and target_id in people and not (people[target_id].get("revealed") or {}).get("level"):
            name = str(people[target_id].get("name") or "")
            if not name or name not in public_text:
                reason = "speaker_not_introduced"
        elif kind == "goal" and target_id in completed:
            reason = "goal_already_completed"
        elif kind == "clue" and target_id in known:
            reason = "clue_already_acquired"
        elif kind == "review" and target_id not in known:
            reason = "clue_not_known"
        elif kind == "scene" and target_id not in set(scene.get("exits") or []):
            reason = "destination_not_available"
        elif kind == "scene" and any(
            (world.get("flags") or {}).get(key) != value
            for key, value in ((world.get("scene_catalog") or {}).get(target_id, {}).get("required_flags") or {}).items()
        ):
            reason = "destination_locked"
        elif kind in {"clue", "goal"}:
            evidence = str(reference.get("evidence_quote") or "").strip()
            if not evidence or evidence not in public_text:
                reason = "no_public_evidence"
        if reason:
            dropped.append({"choice_index": index, "reason": reason})
        else:
            kept.append(index)
    return kept, dropped
