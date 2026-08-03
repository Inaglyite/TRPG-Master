"""Frontend-facing world-state payload enrichment."""

from __future__ import annotations

import copy

from src.asset_payload import asset_payload
from src.handouts import resolve_handout_asset
from src.runtime import RuntimeContext


def _collect_known_npc_ids(world_state: dict) -> list[str]:
    """Collect NPCs whose public handouts have already been revealed."""
    known: list[str] = []

    def add(npc_id: str) -> None:
        if npc_id and npc_id not in known:
            known.append(npc_id)

    seen = world_state.get("seen_handouts", {}) if isinstance(world_state, dict) else {}
    seen_npcs = seen.get("npcs", []) if isinstance(seen, dict) else []
    if isinstance(seen_npcs, list):
        for npc_id in seen_npcs:
            if isinstance(npc_id, str):
                add(npc_id)
    return known


def _append_npc_profiles(enriched: dict, world_state: dict) -> None:
    """Append public NPC profiles without leaking keeper-only fields."""
    if not isinstance(enriched, dict) or not isinstance(world_state, dict):
        return

    npc_assets = world_state.get("asset_map", {}).get("npcs", {})
    if not isinstance(npc_assets, dict):
        return

    npc_by_id = {
        npc.get("id"): npc
        for npc in world_state.get("npcs", [])
        if isinstance(npc, dict) and npc.get("id")
    }
    profiles = []
    for npc_id in _collect_known_npc_ids(world_state):
        npc = npc_by_id.get(npc_id)
        _, asset = resolve_handout_asset(world_state, "npc", npc_id)
        if not npc or not isinstance(asset, dict) or not asset.get("file"):
            continue

        tags = npc.get("visible_tags", [])
        public_tags = "、".join(str(tag) for tag in tags[:4]) if isinstance(tags, list) else ""
        name = npc.get("name") or asset.get("label") or npc_id
        text = f"{name}：{public_tags}" if public_tags else str(name)
        profiles.append(
            {
                "id": f"profile_{npc_id}",
                "text": text,
                "type": "profile",
                "tier": 0,
                "source": "npc_profile",
                "related_npcs": [npc_id],
                "related_scenes": [],
                "discovered_at": None,
                "asset": {
                    "id": npc_id,
                    "file": asset.get("file"),
                    "label": asset.get("label", name),
                },
            }
        )

    if not profiles:
        return

    existing = enriched.get("npc", [])
    if not isinstance(existing, list):
        existing = []
    existing_ids = {item.get("id") for item in existing if isinstance(item, dict)}
    new_profiles = [item for item in profiles if item["id"] not in existing_ids]
    enriched["npc"] = new_profiles + existing


def enrich_clues_for_frontend(
    clues: dict,
    world_state: dict | None = None,
    context: RuntimeContext | None = None,
) -> dict:
    """Attach public NPC cards and data-URI assets for the clue panel."""
    enriched = copy.deepcopy(clues) if isinstance(clues, dict) else clues
    if not isinstance(enriched, dict):
        return enriched
    if isinstance(world_state, dict):
        _append_npc_profiles(enriched, world_state)
    for items in enriched.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            asset = item.get("asset")
            if isinstance(asset, dict) and asset.get("file"):
                asset.update(asset_payload(asset["file"], context))
    return enriched
