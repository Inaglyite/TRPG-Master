"""Build deterministic public-name aliases for NPC speaker parsing."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


def current_scene_npc_ids(engine) -> set[str] | None:
    """当前场景在场 NPC id 集（读不到世界状态时返回 None，调用方按无提示处理）。"""
    try:
        world = engine.context.world_store.load()
    except Exception:  # noqa: BLE001 - 提示失败不得影响叙事
        return None
    scene = world.get("current_scene")
    if not isinstance(scene, dict):
        return None
    present = scene.get("npcs_present")
    if not isinstance(present, list):
        return None
    return {str(npc_id) for npc_id in present if npc_id}


def build_npc_speaker_aliases(
    world: dict,
    initial_state_file: Path,
    *,
    is_valid_npc_id: Callable[[str], bool],
) -> dict[str, str]:
    """Return every public NPC name that may identify a speaker."""
    aliases: dict[str, str] = {}

    def add_aliases(name: str, npc_id: str) -> None:
        name = name.strip()
        if not name or not npc_id:
            return
        aliases[name] = npc_id
        if "·" not in name:
            return
        short = name.rsplit("·", 1)[-1].strip()
        if not short:
            return
        aliases.setdefault(short, npc_id)
        for title in ("医生", "教授", "主任", "先生", "女士", "小姐"):
            if short.endswith(title) and len(short) > len(title):
                aliases.setdefault(short[: -len(title)], npc_id)

    for npc in world.get("npcs", []):
        if not isinstance(npc, dict):
            continue
        npc_id = str(npc.get("id") or "")
        if not npc_id:
            continue
        for key in ("name", "display_name"):
            name = str(npc.get(key) or "").strip()
            if name:
                add_aliases(name, npc_id)

    assets = (world.get("asset_map") or {}).get("npcs") or {}
    for npc_id, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("label") or asset.get("name") or "").strip()
        if name:
            add_aliases(name, str(npc_id))

    try:
        catalog = json.loads(initial_state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        catalog = {}
    for npc in catalog.get("npcs", []):
        if not isinstance(npc, dict):
            continue
        npc_id = str(npc.get("id") or "")
        name = str(npc.get("name") or npc.get("display_name") or "").strip()
        if npc_id and name and is_valid_npc_id(npc_id):
            add_aliases(name, npc_id)
    return aliases
