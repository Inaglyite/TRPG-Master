"""Explicit, side-effect-free authoring format migrations."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .module_format import (
    MANIFEST_V2_SCHEMA_URI,
    MODULE_V2_SCHEMA_URI,
    parse_manifest,
    parse_module,
)


@dataclass(frozen=True)
class ModuleMigrationResult:
    manifest: dict[str, Any]
    module: dict[str, Any]
    report: dict[str, Any]


def migrate_v1_to_v2(
    manifest_payload: dict[str, Any],
    module_payload: dict[str, Any],
    *,
    essential_clue_ids: list[str] | None = None,
) -> ModuleMigrationResult:
    """Return new v2 payloads and a report; never mutate the source objects."""
    manifest_v1 = parse_manifest(manifest_payload)
    module_v1 = parse_module(module_payload)
    if manifest_v1.format_version != "1.0" or module_v1.format_version != "1.0":
        raise ValueError("migrate_v1_to_v2 只接受 manifest/module 均为 1.0 的工程")

    manifest = copy.deepcopy(manifest_payload)
    module = copy.deepcopy(module_payload)
    if essential_clue_ids is None:
        essential_clue_ids = [
            clue_id
            for clue_id, clue in module_v1.clues.items()
            if clue.category == "task" and not clue.initially_known
        ]
    essential = list(dict.fromkeys(str(value) for value in essential_clue_ids))
    missing = sorted(set(essential) - set(module_v1.clues))
    if missing:
        raise ValueError(f"指定的主线线索不存在: {missing}")

    inserted_fallbacks: list[str] = []
    for clue_id in essential:
        clue = module["clues"][clue_id]
        rules = clue.get("discovery_rules") or []
        if not rules and not clue.get("initially_known"):
            raise ValueError(f"主线线索 {clue_id} 没有 discovery_rules，无法安全迁移")
        for index, rule in enumerate(rules):
            if rule.get("requires_success") and not rule.get("fallback"):
                rule["fallback"] = {
                    "mode": "grant_clue",
                    "narrative": "检定失败会带来叙事代价，但不会永久丢失主线线索。",
                }
                inserted_fallbacks.append(
                    f"module.clues.{clue_id}.discovery_rules[{index}].fallback"
                )

    manifest.update({
        "$schema": MANIFEST_V2_SCHEMA_URI,
        "format_version": "2.0",
    })
    module.update({
        "$schema": MODULE_V2_SCHEMA_URI,
        "format_version": "2.0",
        "progression": {"essential_clue_ids": essential},
    })
    # Validate the produced payload before returning it to a CLI or editor.
    parse_manifest(manifest)
    parse_module(module)
    return ModuleMigrationResult(
        manifest=manifest,
        module=module,
        report={
            "from_version": "1.0",
            "to_version": "2.0",
            "essential_clue_ids": essential,
            "inserted_fallbacks": inserted_fallbacks,
            "source_unchanged": True,
        },
    )
