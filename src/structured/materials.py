"""Read-only authored keeper references and stable handout IDs.

No text recognition, reveal decisions, or filesystem paths in client catalogs.
The private projection must only be attached to an authorized keeper snapshot.
"""

from __future__ import annotations

import copy

from src.gameplay.investigators import investigator_entity, stable_investigator_id

IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "webp", "gif", "avif"}


def own_character(state: dict, investigator_id: str) -> dict | None:
    """Read the controlled character, never an arbitrary active PC fallback."""
    sheet = investigator_entity(state, investigator_id) if investigator_id else None
    if not isinstance(sheet, dict) or not sheet.get("name"):
        return None
    # No controller metadata, private memories or other investigators' sheets.
    fields = (
        "name",
        "occupation",
        "hp",
        "max_hp",
        "san",
        "max_san",
        "attributes",
        "skills",
        "conditions",
        "inventory",
        "luck",
        "mp",
        "max_mp",
    )
    return {key: copy.deepcopy(sheet[key]) for key in fields if key in sheet}


def keeper_investigators(state: dict) -> list[dict]:
    """Whitelisted current party sheets; never duplicate the active legacy PC.

    The canonical investigator lookup supplies the active PC's latest values.
    Controller metadata, private memories and arbitrary sheet extensions are
    deliberately excluded, just as in the own-character projection.
    """
    roster = state.get("investigators")
    ids = (
        sorted(str(key) for key in roster)
        if isinstance(roster, dict) and roster
        else [stable_investigator_id(state, "pc")]
    )
    return [
        {"investigator_id": key, **sheet}
        for key in ids
        if (sheet := own_character(state, key)) is not None
    ]


def asset_entries(state: dict) -> dict[str, dict]:
    """Merge supported legacy registries, rejecting ambiguous duplicate IDs."""
    result: dict[str, dict] = {}
    ambiguous: set[str] = set()
    sources = [state.get("assets"), state.get("handout_assets")]
    mapping = state.get("asset_map")
    if isinstance(mapping, dict):
        sources.extend(mapping.get(group) for group in ("scenes", "npcs", "clues"))
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, entry in source.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                continue
            asset_id = str(key)
            if not asset_id or not entry["file"]:
                continue
            if asset_id in result and result[asset_id]["file"] != entry["file"]:
                ambiguous.add(asset_id)
                continue
            result.setdefault(asset_id, entry)
    return {key: entry for key, entry in result.items() if key not in ambiguous}


def keeper_assets(state: dict) -> list[dict]:
    return [
        {"id": key, "label": str(entry.get("label") or key)[:160]}
        for key, entry in sorted(asset_entries(state).items())
        if entry["file"].rsplit(".", 1)[-1].lower() in IMAGE_SUFFIXES
    ]


def keeper_material(state: dict) -> list[dict]:
    """Author-defined references, not Agent memories or invented facts."""
    result: list[dict] = []
    current = str((state.get("current_scene") or {}).get("id") or "")

    def add(kind: str, key: str, title: str, text: str) -> None:
        if not text:
            return
        result.append(
            {
                "id": key,
                "kind": kind,
                "title": title[:160],
                "text": text,
                "current": kind == "scene" and key == current,
            }
        )

    catalog = state.get("scene_catalog")
    if isinstance(catalog, dict):
        for key, entry in catalog.items():
            if isinstance(entry, dict):
                parts = [str(entry.get("description") or ""), str(entry.get("keeper_notes") or "")]
                add(
                    "scene",
                    str(key),
                    str(entry.get("name") or key),
                    "\n\n".join(p for p in parts if p),
                )
    npcs = state.get("npcs")
    for npc in npcs if isinstance(npcs, list) else []:
        if not isinstance(npc, dict) or not npc.get("id"):
            continue
        tags = npc.get("visible_tags")
        visible = "、".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        parts = [visible, str(npc.get("description") or ""), str(npc.get("secret") or "")]
        add(
            "npc",
            str(npc["id"]),
            str(npc.get("name") or npc["id"]),
            "\n\n".join(p for p in parts if p),
        )
    result.sort(key=lambda item: (not item["current"], item["kind"], item["title"]))
    return result


def asset_is_granted(state: dict, asset_id: str, investigator_ids: tuple[str, ...]) -> bool:
    own = set(investigator_ids)
    return any(
        isinstance(grant, dict)
        and grant.get("asset_id") == asset_id
        and grant.get("investigator_id") in own
        for grant in state.get("asset_grants") or []
    )
