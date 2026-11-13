"""稳定物品/线索 ID 注册表（协议 §7）。

旧世界里物品是字符串标签、线索按类别+文本拼接为键，都不能作为新协议的可写
实体 ID。首次在 structured_v1 世界使用时一次性迁移并随状态持久化（幂等，
不每次重算）；旧前端经兼容投影继续看到字符串背包与分类线索列表。
"""

from __future__ import annotations

from src.gameplay.item_registry import (
    ensure_item_registry,
)

from .ids import new_stable_id

CLUE_REGISTRY_VERSION = 1


def register_investigator_inventory(state: dict, investigators: dict[str, dict]) -> None:
    """Seed newly materialized PCs without rebuilding the authoritative registry.

    Lobby snapshots may have already created a registry before any character
    exists. Import each starting backpack once; never resync stale character
    strings after consumption/transfer. Existing IDs (including NPCs) survive.
    The marker includes empty backpacks, so empty does not mean uninitialized.
    """
    registry = ensure_item_registry(state)
    initialized = set(registry.get("initialized_investigators") or [])
    for investigator_id, sheet in investigators.items():
        if investigator_id in initialized:
            continue
        # Pre-marker worlds still have an original stack key after full transfer.
        # An existing stack, even zero quantity, proves prior initialization.
        existing = any(
            str(entry.get("stack_key") or "").startswith(f"investigator:{investigator_id}/")
            or entry.get("holder") == {"kind": "investigator", "id": investigator_id}
            for entry in registry["items"].values()
        )
        if not existing:
            scratch = {"investigators": {investigator_id: sheet}}
            registry["items"].update(ensure_item_registry(scratch)["items"])
        initialized.add(investigator_id)
    registry["initialized_investigators"] = sorted(initialized)


def ensure_clue_registry(state: dict) -> dict:
    """线索稳定 ID：catalog_id 优先，否则按 legacy key 生成（幂等）。

    state["clue_registry"] = {
        "schema_version": 1,
        "clues": {clue_id: {"clue_id", "legacy_key", "category", "text",
                            "granted_to": [investigator_id, ...]}},
        "by_legacy_key": {legacy_key: clue_id},
    }
    """
    registry = state.get("clue_registry")
    if isinstance(registry, dict) and registry.get("schema_version") == CLUE_REGISTRY_VERSION:
        return registry

    clues: dict[str, dict] = {}
    by_legacy: dict[str, str] = {}
    catalog = state.get("clue_catalog", {})
    groups = state.get("clues_found", {})
    if isinstance(groups, dict):
        for category, entries in groups.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get("text") or "")
                legacy_key = f"{category}:{text}"
                if legacy_key in by_legacy:
                    clue_id = by_legacy[legacy_key]
                else:
                    catalog_id = str(entry.get("catalog_id") or entry.get("id") or "")
                    clue_id = catalog_id or new_stable_id("clue")
                    by_legacy[legacy_key] = clue_id
                granted = entry.get("granted_to")
                if not isinstance(granted, list):
                    owner = str(entry.get("owner_investigator_id") or "")
                    granted = [owner] if owner else []
                catalog_entry = catalog.get(clue_id, {}) if isinstance(catalog, dict) else {}
                clues[clue_id] = {
                    "clue_id": clue_id,
                    "legacy_key": legacy_key,
                    "category": str(category),
                    "text": text or str(catalog_entry.get("text") or ""),
                    "granted_to": [str(value) for value in granted],
                }
    registry = {
        "schema_version": CLUE_REGISTRY_VERSION,
        "clues": clues,
        "by_legacy_key": by_legacy,
    }
    state["clue_registry"] = registry
    return registry


def legacy_inventory_projection(state: dict, investigator_id: str) -> list[str]:
    """旧前端兼容投影：稳定注册表 → 字符串背包（含数量折叠）。"""
    registry = state.get("item_registry") or {}
    items = registry.get("items", {}) if isinstance(registry, dict) else {}
    labels: list[str] = []
    for entry in items.values():
        holder = entry.get("holder") or {}
        if holder.get("kind") == "investigator" and str(holder.get("id")) == investigator_id:
            quantity = int(entry.get("quantity") or 0)
            label = str(entry.get("label") or "")
            labels.extend([label] * max(quantity, 0))
    return labels


def find_item(state: dict, item_id: str) -> dict | None:
    registry = ensure_item_registry(state)
    entry = registry["items"].get(item_id)
    if entry is not None:
        return entry
    # 旧标签一次性映射：新协议请求只允许稳定 ID，这里仅为迁移窗口兜底。
    by_label = next(
        (
            entry
            for entry in registry["items"].values()
            if entry["legacy_label"] == item_id or entry["item_id"] == item_id
        ),
        None,
    )
    return by_label
