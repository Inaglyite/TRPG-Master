"""Guard classic state writes; author actions and snapshot restoration are explicit."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar

from src.gameplay.spatial import SpatialError, enabled, settle_transition
from src.gameplay.spatial_inventory import reconcile_legacy_inventory

_AUTHORED = ContextVar("spatial_authored_mutation", default=False)
_RESTORE = ContextVar("spatial_snapshot_restore", default=False)


@contextmanager
def authored_mutation():
    token = _AUTHORED.set(True)
    try:
        yield
    finally:
        _AUTHORED.reset(token)


@contextmanager
def restoring_snapshot():
    token = _RESTORE.set(True)
    try:
        yield
    finally:
        _RESTORE.reset(token)


def guard_update(before: dict, after: dict) -> None:
    if _RESTORE.get() or not enabled(before):
        return
    if before.get("spatial_definition") != after.get("spatial_definition"):
        raise SpatialError("spatial_definition_immutable", "作者空间定义不能由游戏状态工具修改。")
    if not _AUTHORED.get():
        if before.get("spatial_state") != after.get("spatial_state"):
            raise SpatialError(
                "spatial_progress_protected", "空间进度只能通过实际调查、检定或移动结算。"
            )
        if before.get("item_registry") != after.get("item_registry"):
            raise SpatialError(
                "spatial_inventory_protected", "空间物品只能通过实际调查或明确的物品操作结算。"
            )
        owned_flags = {
            effect["flag_id"]
            for point in before["spatial_definition"].get("points", {}).values()
            for action in point["actions"].values()
            for effect in [*action["success"], *action["failure"]]
            if effect["kind"] == "set_flag"
        }
        if any(
            before.get("flags", {}).get(k) != after.get("flags", {}).get(k) for k in owned_flags
        ):
            raise SpatialError("spatial_flag_protected", "调查旗标须通过作者动作结算。")
        from src.gameplay.spatial import protected_clues

        clues = protected_clues(before)
        if _protected_knowledge(before, clues) != _protected_knowledge(after, clues):
            raise SpatialError("spatial_knowledge_protected", "调查线索只能通过实际调查取得。")
        owned_clocks = {
            effect["clock_id"]
            for point in before["spatial_definition"].get("points", {}).values()
            for action in point["actions"].values()
            for effect in [*action["success"], *action["failure"]]
            if effect["kind"] == "advance_clock"
        }
        if any(
            before.get("case_clocks", {}).get(k) != after.get("case_clocks", {}).get(k)
            for k in owned_clocks
        ):
            raise SpatialError("spatial_clock_protected", "调查代价时钟只能通过作者动作结算。")
    reconcile_legacy_inventory(before, after)
    destination = str((after.get("current_scene") or {}).get("id") or "")
    if destination != str((before.get("current_scene") or {}).get("id") or ""):
        settled = copy.deepcopy(after)
        settle_transition(before, settled, destination)
        after.clear()
        after.update(settled)


def _protected_knowledge(world: dict, clue_ids: set[str]) -> dict:
    return {
        "legacy": [
            copy.deepcopy(clue)
            for rows in (world.get("clues_found") or {}).values()
            for clue in rows
            if isinstance(clue, dict) and str(clue.get("catalog_id") or clue.get("id")) in clue_ids
        ],
        "registry": {
            key: copy.deepcopy(clue)
            for key, clue in (world.get("clue_registry") or {}).get("clues", {}).items()
            if key in clue_ids
        },
    }
