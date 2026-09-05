"""Author-owned horror identities and bounded model severity choices."""

from __future__ import annotations

SEVERITIES = ("trivial", "minor", "moderate", "major", "catastrophic")


def available_sanity_sources(world: dict) -> dict[str, dict]:
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    sources = {}
    for index, source in enumerate((world.get("module_rules") or {}).get("sanity_triggers", [])):
        if not isinstance(source, dict):
            continue
        if source.get("scene_ids") and scene_id not in source["scene_ids"]:
            continue
        source_id = str(source.get("id") or f"module:{index}")
        sources[source_id] = {
            **source,
            "already_exposed": source_id in (world.get("pc", {}).get("sanity_exposures") or {}),
        }
    return sources


def validate_sanity_source(world: dict, source_id: str, severity: str) -> dict:
    source = available_sanity_sources(world).get(source_id)
    if source is None or severity not in SEVERITIES:
        raise ValueError("恐怖源或严重度不在当前授权目录中")
    lower = source.get("min_severity") or source.get("severity")
    upper = source.get("max_severity") or source.get("severity")
    if lower not in SEVERITIES or upper not in SEVERITIES or not SEVERITIES.index(lower) <= SEVERITIES.index(severity) <= SEVERITIES.index(upper):
        raise ValueError("严重度超出模组对该恐怖源的裁量范围")
    return source


def exposure_id_for_clue(world: dict, clue_id: str) -> str:
    for index, source in enumerate((world.get("module_rules") or {}).get("sanity_triggers", [])):
        if isinstance(source, dict) and clue_id in source.get("clue_ids", []):
            return str(source.get("id") or f"module:{index}")
    return clue_id
