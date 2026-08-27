"""世界状态 schema 的显式版本迁移。"""

from __future__ import annotations

import copy
from collections.abc import Callable

CURRENT_WORLD_SCHEMA_VERSION = 2


class UnsupportedWorldSchemaError(ValueError):
    pass


def _migrate_v0_to_v1(state: dict) -> dict:
    """把无版本的单人存档补齐为首个显式 schema。"""
    state.setdefault("private_memory", {
        "goals_and_plans": "",
        "hidden_facts": {},
        "inference_notes": "（从旧存档迁移，请守秘人根据对话历史补充）",
    })

    for npc in state.get("npcs", []):
        if isinstance(npc, dict):
            npc.setdefault("revealed", {"level": 0, "entries": []})

    pc = state.setdefault("pc", {})
    if isinstance(pc, dict):
        pc.setdefault("psychological_profile", {
            "traits": [],
            "key_relationships": [],
            "phobias": [],
            "manias": [],
        })

    state["schema_version"] = 1
    state.setdefault("revision", 0)
    return state


def _migrate_v1_to_v2(state: dict) -> dict:
    """Declare stable aggregate ownership without duplicating mutable data."""
    state["state_meta"] = {
        "layout": "aggregate-v2",
        "domains": {
            "character": ["pc"],
            "scene": ["current_scene", "scene_catalog", "scene_cache"],
            "knowledge": ["clues_found", "clue_catalog", "clue_links"],
            "encounter": ["npcs", "encounter_history"],
            "clock": ["case_clocks", "flags"],
            "journal": ["narrative_memory", "private_memory"],
        },
    }
    history = state.setdefault("migration_history", [])
    if not isinstance(history, list):
        history = []
        state["migration_history"] = history
    history.append({
        "from_version": 1,
        "to_version": 2,
        "migration": "aggregate-domain-ownership",
    })
    state["schema_version"] = 2
    return state


WORLD_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    0: _migrate_v0_to_v1,
    1: _migrate_v1_to_v2,
}


def migrate_world_state(raw_state: dict) -> tuple[dict, bool]:
    """返回迁移后的深拷贝以及是否发生了迁移。"""
    if not isinstance(raw_state, dict):
        raise TypeError("世界状态根节点必须是 JSON object")

    state = copy.deepcopy(raw_state)
    try:
        version = int(state.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise UnsupportedWorldSchemaError("schema_version 必须是整数") from exc

    if version > CURRENT_WORLD_SCHEMA_VERSION:
        raise UnsupportedWorldSchemaError(
            f"世界状态版本 {version} 高于当前支持版本 {CURRENT_WORLD_SCHEMA_VERSION}"
        )

    changed = False
    if state.get("schema_version", 0) != version:
        state["schema_version"] = version
        changed = True

    while version < CURRENT_WORLD_SCHEMA_VERSION:
        migration = WORLD_MIGRATIONS.get(version)
        if migration is None:
            raise UnsupportedWorldSchemaError(f"缺少 v{version} 的迁移函数")
        state = migration(state)
        version = int(state.get("schema_version", version + 1))
        changed = True

    revision = state.get("revision", 0)
    if not isinstance(revision, int) or revision < 0:
        state["revision"] = 0
        changed = True
    narrative_memory = state.get("narrative_memory")
    if not isinstance(narrative_memory, dict):
        state["narrative_memory"] = {
            "turn_sequence": 0,
            "recent_lore": [],
        }
        changed = True
    else:
        try:
            turn_sequence = int(narrative_memory.get("turn_sequence", 0))
        except (TypeError, ValueError):
            turn_sequence = 0
        if turn_sequence < 0 or narrative_memory.get("turn_sequence") != turn_sequence:
            narrative_memory["turn_sequence"] = max(0, turn_sequence)
            changed = True
        recent_lore = narrative_memory.get("recent_lore", [])
        if "recent_lore" not in narrative_memory or not isinstance(recent_lore, list):
            narrative_memory["recent_lore"] = []
            changed = True
    return state, changed
