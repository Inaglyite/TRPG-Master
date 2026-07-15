"""Deterministic handout resolution and save-state reconciliation."""

from __future__ import annotations

import copy

ASSET_GROUPS = ("npcs", "scenes", "clues")
EVENT_BY_ENTITY_TYPE = {
    "npc": "npc_revealed",
    "scene": "scene_entered",
    "clue": "clue_discovered",
}


def _trigger_matches(
    trigger: dict,
    event: str,
    *,
    entity_id: str,
) -> bool:
    """Match only authoritative engine entity IDs.

    Older module packages may still contain ``match_all`` / ``match_any``
    metadata.  Free text is useful for lore retrieval, but it must never
    authorize a stateful handout because mentions and observations are not the
    same event.
    """
    if trigger.get("event") != event:
        return False
    expected_entity = str(trigger.get("entity_id") or "")
    return bool(expected_entity and entity_id and expected_entity == entity_id)


def matching_handouts(
    state: dict,
    event: str,
    *,
    entity_id: str = "",
    text: str = "",
    entity_type: str | None = None,
    include_seen: bool = False,
) -> list[dict[str, str]]:
    """Return handouts bound to one authoritative engine entity event.

    ``text`` remains in the public signature so older callers and packages can
    be loaded, but it is deliberately not used as display authorization.
    """
    asset_map = state.get("asset_map", {})
    if not isinstance(asset_map, dict):
        return []

    requested_group = f"{entity_type}s" if entity_type else None
    seen_entities = state.get("seen_handouts", {})
    seen_assets = state.get("seen_handout_assets", {})
    matches: list[dict[str, str]] = []
    for group in ASSET_GROUPS:
        if requested_group and group != requested_group:
            continue
        entries = asset_map.get(group, {})
        if not isinstance(entries, dict):
            continue
        group_seen_entities = (
            seen_entities.get(group, []) if isinstance(seen_entities, dict) else []
        )
        group_seen_assets = (
            seen_assets.get(group, []) if isinstance(seen_assets, dict) else []
        )
        for asset_id, asset in entries.items():
            if not isinstance(asset, dict) or not asset.get("file"):
                continue
            if not include_seen and (
                asset_id in group_seen_assets or asset_id in group_seen_entities
            ):
                continue
            triggers = asset.get("reveal_on", [])
            if not isinstance(triggers, list):
                triggers = []
            explicit_match = any(
                isinstance(trigger, dict)
                and _trigger_matches(
                    trigger,
                    event,
                    entity_id=entity_id,
                )
                for trigger in triggers
            )
            implicit_legacy_match = (
                not triggers
                and bool(entity_id)
                and str(asset_id) == entity_id
                and EVENT_BY_ENTITY_TYPE.get(group[:-1]) == event
            )
            if explicit_match or implicit_legacy_match:
                matches.append({
                    "entity_type": group[:-1],
                    "entity_id": entity_id or asset_id,
                    "asset_id": asset_id,
                })
    return matches


def resolve_handout_asset(
    state: dict,
    entity_type: str,
    entity_id: str,
    *,
    asset_id: str | None = None,
) -> tuple[str | None, dict | None]:
    """Resolve an entity ID to its possibly differently named asset ID."""
    group = f"{entity_type}s"
    entries = state.get("asset_map", {}).get(group, {})
    if not isinstance(entries, dict):
        return None, None

    if asset_id and isinstance(entries.get(asset_id), dict):
        return asset_id, entries[asset_id]
    if isinstance(entries.get(entity_id), dict):
        return entity_id, entries[entity_id]

    expected_event = EVENT_BY_ENTITY_TYPE.get(entity_type)
    if expected_event:
        for candidate_id, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            for trigger in entry.get("reveal_on", []):
                if (
                    isinstance(trigger, dict)
                    and trigger.get("event") == expected_event
                    and trigger.get("entity_id") == entity_id
                ):
                    return candidate_id, entry

    if entity_type == "clue":
        catalog = state.get("clue_catalog", {})
        catalog_entry = catalog.get(entity_id) if isinstance(catalog, dict) else None
        catalog_asset = catalog_entry.get("asset") if isinstance(catalog_entry, dict) else None
        if isinstance(catalog_asset, dict):
            candidate_id = catalog_asset.get("id")
            if candidate_id and isinstance(entries.get(candidate_id), dict):
                return candidate_id, entries[candidate_id]
    return None, None


def asset_reference(asset_id: str, entry: dict, fallback_label: str = "") -> dict:
    return {
        "id": asset_id,
        "file": entry.get("file"),
        "label": entry.get("label") or fallback_label,
    }


def matching_clue_asset(state: dict, clue: dict) -> tuple[str, dict] | None:
    """Resolve an authored clue asset by stable catalog ID only."""
    catalog = state.get("clue_catalog", {})
    catalog_id = clue.get("catalog_id") or clue.get("id")
    catalog_entry = catalog.get(catalog_id) if isinstance(catalog, dict) else None
    catalog_asset = catalog_entry.get("asset") if isinstance(catalog_entry, dict) else None
    if isinstance(catalog_asset, dict) and catalog_asset.get("id"):
        asset_id = catalog_asset["id"]
        mapped = state.get("asset_map", {}).get("clues", {}).get(asset_id)
        if isinstance(mapped, dict) and mapped.get("file"):
            return asset_id, mapped
    return None


def attach_matching_clue_asset(
    state: dict,
    clue: dict,
    *,
    used_asset_ids: set[str] | None = None,
) -> str | None:
    if isinstance(clue.get("asset"), dict) and clue["asset"].get("file"):
        return None
    matched = matching_clue_asset(state, clue)
    if not matched:
        return None
    asset_id, entry = matched
    if used_asset_ids is not None and asset_id in used_asset_ids:
        return None
    clue["asset"] = asset_reference(asset_id, entry, str(clue.get("text") or "")[:80])
    if used_asset_ids is not None:
        used_asset_ids.add(asset_id)
    return asset_id


def repair_discovered_clue_assets(state: dict) -> list[dict[str, str]]:
    """Attach newly configured assets to clues stored by older engine versions."""
    clues_found = state.get("clues_found", {})
    if not isinstance(clues_found, dict):
        return []
    used_asset_ids = {
        asset.get("id")
        for clues in clues_found.values()
        if isinstance(clues, list)
        for clue in clues
        if isinstance(clue, dict)
        for asset in [clue.get("asset")]
        if isinstance(asset, dict) and asset.get("id")
    }
    repaired = []
    for clues in clues_found.values():
        if not isinstance(clues, list):
            continue
        for clue in clues:
            if not isinstance(clue, dict):
                continue
            asset_id = attach_matching_clue_asset(
                state,
                clue,
                used_asset_ids=used_asset_ids,
            )
            if asset_id:
                repaired.append({
                    "entity_type": "clue",
                    "entity_id": str(clue.get("id") or asset_id),
                    "asset_id": asset_id,
                })
    return repaired


def refresh_static_handout_config(state: dict, template: dict) -> list[dict[str, str]]:
    """Refresh immutable module metadata without overwriting gameplay progress."""
    for key in (
        "module_meta",
        "scene_catalog",
        "clue_catalog",
        "endings",
        "module_rules",
        "module_opening",
        "module_starting_inventory",
    ):
        if key in template:
            state[key] = copy.deepcopy(template[key])

    template_flags = template.get("flags", {})
    if isinstance(template_flags, dict):
        state_flags = state.setdefault("flags", {})
        for key, value in template_flags.items():
            state_flags.setdefault(key, copy.deepcopy(value))

    template_map = template.get("asset_map", {})
    if isinstance(template_map, dict):
        state_map = state.setdefault("asset_map", {})
        for group in ASSET_GROUPS:
            template_entries = template_map.get(group, {})
            if not isinstance(template_entries, dict):
                continue
            state_entries = state_map.setdefault(group, {})
            for asset_id, template_entry in template_entries.items():
                if not isinstance(template_entry, dict):
                    continue
                current = state_entries.get(asset_id)
                if not isinstance(current, dict):
                    state_entries[asset_id] = copy.deepcopy(template_entry)
                else:
                    current.update(copy.deepcopy(template_entry))

    # Legacy modules commonly keyed portrait/scene assets by the entity ID but
    # predated reveal_on. Turn that convention into an explicit runtime trigger.
    state_map = state.get("asset_map", {})
    bindings = {
        "npcs": {
            str(npc.get("id"))
            for npc in state.get("npcs", [])
            if isinstance(npc, dict) and npc.get("id")
        },
        "scenes": set(state.get("scene_catalog", {})) | set(state.get("scene_cache", {})),
    }
    for group, entity_ids in bindings.items():
        entries = state_map.get(group, {}) if isinstance(state_map, dict) else {}
        for asset_id, entry in entries.items():
            if asset_id not in entity_ids or not isinstance(entry, dict):
                continue
            trigger = {
                "event": EVENT_BY_ENTITY_TYPE[group[:-1]],
                "entity_id": asset_id,
            }
            triggers = entry.setdefault("reveal_on", [])
            if trigger not in triggers:
                triggers.append(trigger)

    return repair_discovered_clue_assets(state)
