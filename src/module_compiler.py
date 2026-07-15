"""无副作用的模组编译内核：作者态定义 -> 运行时模板与守秘人提示。"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from .lorebook import LorebookEnvelope, validate_lorebook_references
from .module_diagnostics import (
    ModuleDiagnostic,
    analyze_module,
    diagnostics_from_validation_error,
    has_blocking_diagnostics,
)
from .module_format import (
    CLUE_CATEGORIES,
    AssetDefinition,
    AssetRevealTrigger,
    ModuleDefinition,
    ModuleManifest,
    parse_manifest,
    parse_module,
)
from .world_migrations import CURRENT_WORLD_SCHEMA_VERSION

MODULE_COMPILER_VERSION = "1.0.0"


@dataclass(frozen=True)
class TraceEntry:
    output_path: str
    source_path: str
    operation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "output_path": self.output_path,
            "source_path": self.source_path,
            "operation": self.operation,
        }


@dataclass(frozen=True)
class CompilationResult:
    world_state: dict[str, Any]
    keeper_prompt: str
    diagnostics: tuple[ModuleDiagnostic, ...]
    trace: tuple[TraceEntry, ...]
    compiler_version: str = MODULE_COMPILER_VERSION

    @property
    def ok(self) -> bool:
        return not has_blocking_diagnostics(self.diagnostics)

    def to_dict(self, *, include_outputs: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "compiler_version": self.compiler_version,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "trace": [entry.to_dict() for entry in self.trace],
        }
        if include_outputs:
            payload["outputs"] = {
                "world_state_initial": self.world_state,
                "module_md": self.keeper_prompt,
            }
        return payload


@dataclass(frozen=True)
class CompilationPreview:
    diagnostics: tuple[ModuleDiagnostic, ...]
    result: CompilationResult | None = None
    compiler_version: str = MODULE_COMPILER_VERSION

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok

    def to_dict(self, *, include_outputs: bool = True) -> dict[str, Any]:
        if self.result is not None:
            payload = self.result.to_dict(
                include_outputs=include_outputs and self.result.ok
            )
            if include_outputs and not self.result.ok:
                payload["outputs"] = None
            return payload
        payload: dict[str, Any] = {
            "ok": False,
            "compiler_version": self.compiler_version,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "trace": [],
        }
        if include_outputs:
            payload["outputs"] = None
        return payload


def _merge_extensions(data: dict[str, Any]) -> dict[str, Any]:
    extensions = data.pop("extensions", {}) or {}
    for key, value in extensions.items():
        if key not in data:
            data[key] = value
    return data


def _runtime_asset(asset_id: str | None, assets: dict[str, AssetDefinition]) -> dict | None:
    if not asset_id:
        return None
    asset = assets[asset_id]
    return {
        "id": asset_id,
        "file": asset.file.removeprefix("assets/"),
        "label": asset.label,
    }


def _runtime_asset_map(
    assets: dict[str, AssetDefinition],
    bindings: dict[str, Any],
    event: str,
) -> dict[str, dict]:
    result = {
        asset_id: {
            "file": asset.file.removeprefix("assets/"),
            "label": asset.label or asset_id,
            "alt": asset.alt,
            "media_type": asset.media_type,
            "reveal_on": [trigger.model_dump() for trigger in asset.reveal_on],
        }
        for asset_id, asset in assets.items()
    }
    for entity_id, definition in bindings.items():
        asset_id = definition.asset_id
        if not asset_id:
            continue
        trigger = AssetRevealTrigger(event=event, entity_id=entity_id).model_dump()
        triggers = result[asset_id]["reveal_on"]
        if trigger not in triggers:
            triggers.append(trigger)
    return result


def compile_world_state(manifest: ModuleManifest, module: ModuleDefinition) -> dict[str, Any]:
    """把作者态模组定义编译为当前引擎可直接 reset 的世界模板。"""
    entry = module.scenes[module.entry_scene_id]
    current_scene = _merge_extensions(entry.model_dump(exclude={"asset_id", "document"}))
    current_scene["id"] = module.entry_scene_id

    npcs = []
    for npc_id, definition in module.npcs.items():
        data = definition.model_dump(exclude={
            "asset_id", "initial_reveal", "initial_reveal_entries", "notes"
        })
        data = _merge_extensions(data)
        data["id"] = npc_id
        if data.get("max_hp") is None:
            data["max_hp"] = data.get("hp", 0)
        data["revealed"] = {
            "level": definition.initial_reveal,
            "entries": copy.deepcopy(definition.initial_reveal_entries),
        }
        npcs.append(data)

    known_ids = set(module.initial_state.known_clue_ids)
    known_ids.update(clue_id for clue_id, clue in module.clues.items() if clue.initially_known)
    clues_found = {category: [] for category in CLUE_CATEGORIES}
    clue_catalog = {}
    for clue_id, definition in module.clues.items():
        data = definition.model_dump(exclude={
            "category", "asset_id", "initially_known", "discovery_notes"
        })
        data = _merge_extensions(data)
        data.update({
            "id": clue_id,
            "discovered_at": None,
            "asset": _runtime_asset(definition.asset_id, module.assets.clues),
        })
        clue_catalog[clue_id] = {
            **copy.deepcopy(data),
            "category": definition.category,
            "discovery_notes": definition.discovery_notes,
        }
        if clue_id in known_ids:
            clues_found[definition.category].append(copy.deepcopy(data))

    pc = _merge_extensions(module.initial_state.pc.model_dump())
    private_memory = module.initial_state.private_memory.model_dump()
    hidden_facts = private_memory.setdefault("hidden_facts", {})
    for npc_id, npc in module.npcs.items():
        if npc.secret and npc_id not in hidden_facts:
            hidden_facts[npc_id] = npc.secret[:150] + ("..." if len(npc.secret) > 150 else "")

    scene_catalog = {}
    for scene_id, definition in module.scenes.items():
        scene_data = _merge_extensions(
            definition.model_dump(exclude={"asset_id", "document"})
        )
        scene_data.update({"id": scene_id, "document": definition.document})
        scene_catalog[scene_id] = scene_data

    endings = [
        {"id": ending_id, **ending.model_dump()}
        for ending_id, ending in module.endings.items()
    ]
    world = {
        "schema_version": CURRENT_WORLD_SCHEMA_VERSION,
        "revision": 0,
        "module": manifest.id,
        "module_version": manifest.version,
        "module_meta": {
            "id": manifest.id,
            "version": manifest.version,
            "title": manifest.title,
            "system": manifest.system,
            "era": manifest.era,
            "language": manifest.language,
        },
        "current_scene": current_scene,
        "pc": pc,
        "module_starting_inventory": copy.deepcopy(module.initial_state.granted_items),
        "npcs": npcs,
        "clues_found": clues_found,
        "flags": copy.deepcopy(module.initial_state.flags),
        "case_clocks": copy.deepcopy(module.initial_state.case_clocks),
        "scene_cache": {module.entry_scene_id: entry.description},
        "scene_catalog": scene_catalog,
        "clue_catalog": clue_catalog,
        "endings": endings,
        "private_memory": private_memory,
        "narrative_memory": {
            "turn_sequence": 0,
            "recent_lore": [],
        },
        "encounter_history": {},
        "asset_map": {
            "npcs": _runtime_asset_map(
                module.assets.npcs, module.npcs, "npc_revealed"
            ),
            "scenes": _runtime_asset_map(
                module.assets.scenes, module.scenes, "scene_entered"
            ),
            "clues": _runtime_asset_map(
                module.assets.clues, module.clues, "clue_discovered"
            ),
        },
        "clue_links": [link.model_dump(by_alias=True) for link in module.clue_links],
        "module_rules": copy.deepcopy(module.rules),
        "module_opening": module.opening_prompt,
    }
    world.update(copy.deepcopy(module.initial_state.extensions))
    world.update(copy.deepcopy(module.extensions))
    return world


def render_keeper_prompt(
    manifest: ModuleManifest,
    module: ModuleDefinition,
    keeper_notes: str = "",
) -> str:
    """生成现有提示加载器可读取的 module.md。"""
    payload = module.model_dump(by_alias=True, exclude_none=True)
    payload.get("initial_state", {}).pop("pc", None)
    structured = json.dumps(payload, ensure_ascii=False, indent=2)
    notes = keeper_notes.strip() or "（本模组没有额外守秘人正文。）"
    frontmatter = {
        "module": manifest.id,
        "version": manifest.version,
        "title": manifest.title,
        "author": manifest.author,
        "system": manifest.system,
        "era": manifest.era,
        "description": manifest.description,
    }
    frontmatter_text = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in frontmatter.items()
    )
    return (
        "---\n"
        f"{frontmatter_text}\n"
        "---\n\n"
        "# 运行时身份约束\n\n"
        "玩家调查员身份只来自 `world://state.pc`。下述模组定义、示例和守秘人正文"
        "均不得覆盖玩家姓名、职业、背景或角色所有权。\n\n"
        "# 结构化模组定义\n\n"
        "```json\n"
        f"{structured}\n"
        "```\n\n"
        "# 守秘人正文\n\n"
        f"{notes}\n"
    )


def _build_trace(
    manifest: ModuleManifest,
    module: ModuleDefinition,
    world: dict[str, Any],
) -> tuple[TraceEntry, ...]:
    trace = [
        TraceEntry("world_state.schema_version", "engine.world_schema_version", "set"),
        TraceEntry("world_state.revision", "compiler.default", "set to zero"),
        TraceEntry("world_state.module", "manifest.id", "copy"),
        TraceEntry("world_state.module_version", "manifest.version", "copy"),
        TraceEntry("world_state.module_meta", "manifest", "copy runtime metadata"),
        TraceEntry(
            "world_state.current_scene",
            f"module.scenes.{module.entry_scene_id}",
            "select entry scene",
        ),
        TraceEntry(
            f"world_state.scene_cache.{module.entry_scene_id}",
            f"module.scenes.{module.entry_scene_id}.description",
            "seed entry scene cache",
        ),
        TraceEntry("world_state.pc", "module.initial_state.pc", "copy template"),
        TraceEntry(
            "world_state.module_starting_inventory",
            "module.initial_state.granted_items",
            "copy module-granted items",
        ),
        TraceEntry("world_state.flags", "module.initial_state.flags", "deep copy"),
        TraceEntry("world_state.case_clocks", "module.initial_state.case_clocks", "deep copy"),
        TraceEntry(
            "world_state.private_memory",
            "module.initial_state.private_memory",
            "copy and seed NPC hidden facts",
        ),
        TraceEntry("world_state.clue_links", "module.clue_links", "normalize aliases"),
        TraceEntry("world_state.module_rules", "module.rules", "deep copy"),
        TraceEntry("world_state.module_opening", "module.opening_prompt", "copy"),
        TraceEntry("module_md.frontmatter", "manifest", "render YAML frontmatter"),
        TraceEntry("module_md.structured_definition", "module", "render JSON"),
        TraceEntry(
            "module_md.keeper_document",
            manifest.keeper_document or "compiler.default",
            "append Markdown",
        ),
    ]
    for index, npc_id in enumerate(module.npcs):
        trace.append(TraceEntry(
            f"world_state.npcs[{index}]",
            f"module.npcs.{npc_id}",
            "normalize keyed entity",
        ))
    for scene_id in module.scenes:
        trace.append(TraceEntry(
            f"world_state.scene_catalog.{scene_id}",
            f"module.scenes.{scene_id}",
            "normalize scene",
        ))
    for index, ending_id in enumerate(module.endings):
        trace.append(TraceEntry(
            f"world_state.endings[{index}]",
            f"module.endings.{ending_id}",
            "normalize keyed entity",
        ))
    for clue_id, clue in module.clues.items():
        trace.append(TraceEntry(
            f"world_state.clue_catalog.{clue_id}",
            f"module.clues.{clue_id}",
            "normalize clue",
        ))
        if clue_id in module.initial_state.known_clue_ids or clue.initially_known:
            found = world["clues_found"][clue.category]
            index = next(
                index for index, item in enumerate(found)
                if item.get("id") == clue_id
            )
            trace.append(TraceEntry(
                f"world_state.clues_found.{clue.category}[{index}]",
                f"module.clues.{clue_id}",
                "copy clue into initially known collection",
            ))
            if clue_id in module.initial_state.known_clue_ids:
                source_index = module.initial_state.known_clue_ids.index(clue_id)
                source_path = f"module.initial_state.known_clue_ids[{source_index}]"
            else:
                source_path = f"module.clues.{clue_id}.initially_known"
            trace.append(TraceEntry(
                f"world_state.clues_found.{clue.category}[{index}]",
                source_path,
                "select as initially known",
            ))
    for group_name in ("npcs", "scenes", "clues"):
        for asset_id in getattr(module.assets, group_name):
            trace.append(TraceEntry(
                f"world_state.asset_map.{group_name}.{asset_id}",
                f"module.assets.{group_name}.{asset_id}",
                "normalize asset path",
            ))
    return tuple(trace)


def compile_module(
    manifest: ModuleManifest,
    module: ModuleDefinition,
    keeper_notes: str = "",
) -> CompilationResult:
    diagnostics = analyze_module(manifest, module)
    world = compile_world_state(manifest, module)
    effective_keeper_notes = keeper_notes if manifest.keeper_document else ""
    keeper_prompt = render_keeper_prompt(manifest, module, effective_keeper_notes)
    return CompilationResult(
        world_state=world,
        keeper_prompt=keeper_prompt,
        diagnostics=diagnostics,
        trace=_build_trace(manifest, module, world),
    )


def compile_payload(
    manifest_payload: Any,
    module_payload: Any,
    keeper_notes: str = "",
    lorebook_payload: Any | None = None,
) -> CompilationPreview:
    diagnostics: list[ModuleDiagnostic] = []
    if not isinstance(keeper_notes, str):
        diagnostics.append(ModuleDiagnostic(
            phase="compilation",
            level="error",
            code="string_type",
            path="keeper_document",
            message="守秘人正文必须是字符串",
        ))
    manifest = None
    module = None
    lorebook = None
    try:
        manifest = parse_manifest(manifest_payload)
    except ValidationError as exc:
        diagnostics.extend(diagnostics_from_validation_error(
            exc,
            phase="manifest_validation",
            root="manifest",
        ))
    try:
        module = parse_module(module_payload)
    except ValidationError as exc:
        diagnostics.extend(diagnostics_from_validation_error(
            exc,
            phase="module_validation",
            root="module",
        ))
    if lorebook_payload is not None:
        try:
            lorebook = LorebookEnvelope.model_validate(lorebook_payload)
        except ValidationError as exc:
            diagnostics.extend(diagnostics_from_validation_error(
                exc,
                phase="lorebook_validation",
                root="lorebook",
            ))
    if manifest is not None:
        if manifest.lorebook and lorebook_payload is None:
            diagnostics.append(ModuleDiagnostic(
                phase="lorebook_validation",
                level="error",
                code="missing_file",
                path="lorebook",
                message="manifest 声明了 lorebook.json，但未提供 Lorebook 数据",
            ))
        if not manifest.lorebook and lorebook is not None:
            diagnostics.append(ModuleDiagnostic(
                phase="lorebook_validation",
                level="error",
                code="undeclared_lorebook",
                path="manifest.lorebook",
                message="提供了 Lorebook 数据，但 manifest.lorebook 未声明 lorebook.json",
            ))
    if (
        manifest is not None
        and module is not None
        and manifest.format_version != module.format_version
    ):
        diagnostics.append(ModuleDiagnostic(
            phase="compatibility",
            level="error",
            code="format_version_mismatch",
            path="module.format_version",
            message=(
                f"manifest 使用 {manifest.format_version}，"
                f"module 使用 {module.format_version}，两者必须一致"
            ),
        ))
    if module is not None and lorebook is not None:
        for message in validate_lorebook_references(
            lorebook,
            scene_ids=set(module.scenes),
            npc_ids=set(module.npcs),
            clue_ids=set(module.clues),
            flag_ids=set(module.initial_state.flags),
        ):
            diagnostics.append(ModuleDiagnostic(
                phase="lorebook_validation",
                level="error",
                code="invalid_reference",
                path="lorebook.data.entries",
                message=message,
            ))
    if lorebook is not None and lorebook.data.recursive_scanning:
        diagnostics.append(ModuleDiagnostic(
            phase="lorebook_validation",
            level="warning",
            code="recursive_scanning_unsupported",
            path="lorebook.data.recursive_scanning",
            message="当前运行时会保留但不会执行 recursive_scanning",
        ))
    if lorebook is not None and any(entry.use_regex for entry in lorebook.data.entries):
        diagnostics.append(ModuleDiagnostic(
            phase="lorebook_validation",
            level="warning",
            code="regex_matching_unsupported",
            path="lorebook.data.entries",
            message="当前运行时会保留但不会执行 use_regex 条目",
        ))
    if manifest is None or module is None or has_blocking_diagnostics(tuple(diagnostics)):
        return CompilationPreview(diagnostics=tuple(diagnostics))
    result = compile_module(manifest, module, keeper_notes)
    if diagnostics:
        result = replace(
            result,
            diagnostics=tuple(diagnostics) + result.diagnostics,
        )
    return CompilationPreview(diagnostics=result.diagnostics, result=result)
