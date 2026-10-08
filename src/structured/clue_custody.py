"""Knowledge grants and physical-original choices share committed provenance."""

import copy

from src.gameplay.handouts import resolve_handout_asset
from src.gameplay.investigators import stable_investigator_id

from .registries import ensure_clue_registry


def original_item_ids(state: dict, clue_id: str, holders: set[str]) -> list[str]:
    return sorted(
        item["item_id"]
        for item in ((state.get("item_registry") or {}).get("items") or {}).values()
        if item.get("source_clue_id") == clue_id
        and item.get("quantity", 0) > 0
        and (item.get("holder") or {}).get("kind") == "investigator"
        and str((item.get("holder") or {}).get("id")) in holders
    )


def _image_allowed(state: dict, clue_id: str, recipient: str) -> bool:
    asset_id, asset = resolve_handout_asset(state, "clue", clue_id)
    if not asset_id or not isinstance(asset, dict) or not asset.get("file"):
        return False
    seen = state.get("seen_handout_assets") or {}
    if isinstance(seen, dict) and asset_id in (seen.get("clues") or []):
        return True
    return any(
        isinstance(g, dict)
        and g.get("asset_id") == asset_id
        and g.get("investigator_id") == recipient
        for g in state.get("asset_grants") or []
    )


def clue_choices(state: dict) -> dict[tuple[str, str], dict]:
    # Copy-only initialization must not write a registry during ordinary rolls
    # or reference reading. IDs of authored clues remain canonical catalog IDs.
    registry = ensure_clue_registry(copy.deepcopy(state))["clues"]
    party = set(state.get("investigators") or {}) or {stable_investigator_id(state, "pc")}
    result = {}
    for clue_id, entry in registry.items():
        for recipient in set(entry.get("granted_to") or []) or party:
            physical = original_item_ids(state, clue_id, {recipient})
            presentation = ["describe"]
            if _image_allowed(state, clue_id, recipient):
                presentation.append("image")
            if physical:
                presentation.append("original")
            result[(clue_id, recipient)] = {
                "clue_id": clue_id,
                "investigator_id": recipient,
                "category": entry["category"],
                "text": str(entry.get("text") or "")[:500],
                "presentation": presentation,
                "allowed_physical_item_ids": physical,
            }
    return result


def sync_choices(state: dict, before: dict, outcome) -> None:
    from .domains import EventSpec

    after = clue_choices(state)
    emitted = set()
    for event in outcome.events:
        if event.type == "clue_granted":
            key = (event.payload.get("clue_id"), event.payload.get("investigator_id"))
            if key in after:
                event.payload = after[key]
                emitted.add(key)
    for key, projection in after.items():
        if key not in emitted and key in before and before[key] != projection:
            outcome.events.append(
                EventSpec(
                    "clue_updated",
                    projection,
                    {"kind": "investigators", "investigator_ids": [key[1]]},
                )
            )
