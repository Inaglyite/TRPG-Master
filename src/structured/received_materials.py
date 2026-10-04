"""Read-only, recipient-specific material index; no image bytes or file paths.

The caller must supply server-resolved investigator ownership, not client IDs.
This catalog is distinct from the keeper's complete authored material library.
"""

from __future__ import annotations

from .materials import IMAGE_SUFFIXES, asset_entries


def received_assets(state: dict, investigator_ids: tuple[str, ...]) -> list[dict]:
    own = set(investigator_ids)
    if not own:
        return []
    grants = state.get("asset_grants")
    if not isinstance(grants, list):
        return []
    ids = {
        grant["asset_id"]
        for grant in grants
        if isinstance(grant, dict)
        and isinstance(grant.get("asset_id"), str)
        and isinstance(grant.get("investigator_id"), str)
        and grant["investigator_id"] in own
    }
    return [
        {"id": key, "label": str(entry.get("label") or key)[:160]}
        for key, entry in sorted(asset_entries(state).items())
        if key in ids and entry["file"].rsplit(".", 1)[-1].lower() in IMAGE_SUFFIXES
    ]
