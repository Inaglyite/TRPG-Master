"""TRPG Master v1 模组定义与 JSON Schema。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODULE_FORMAT_VERSION = "1.0"
ENGINE_VERSION = "1.0.0"
MANIFEST_SCHEMA_URI = "https://trpg-master.local/schemas/module-manifest-v1.json"
MODULE_SCHEMA_URI = "https://trpg-master.local/schemas/module-v1.json"

_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_ENTITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITIES = {"custom_skills", "bundled_characters", "scene_documents"}
CLUE_CATEGORIES = ("investigation", "event", "task", "npc")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def is_portable_path_component(value: str) -> bool:
    """Return whether a package path component is portable across desktop targets."""
    if (
        not value
        or value.endswith((" ", "."))
        or any(character in '<>:"|?*' for character in value)
    ):
        return False
    if any(ord(character) < 32 for character in value):
        return False
    return value.split(".", 1)[0].upper() not in _WINDOWS_RESERVED_NAMES


def engine_supports(minimum_version: str) -> bool:
    """Compare the SemVer core used for the package's minimum engine requirement."""
    requested = tuple(int(part) for part in minimum_version.split("+", 1)[0].split("-", 1)[0].split("."))
    current = tuple(int(part) for part in ENGINE_VERSION.split("."))
    return requested <= current


def _safe_relative_path(value: str, label: str) -> str:
    raw = str(value).strip()
    parts = raw.split("/")
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in parts)
        or any(not is_portable_path_component(part) for part in parts)
        or re.match(r"^[A-Za-z]:", raw)
    ):
        raise ValueError(f"{label} 必须是可跨平台使用的包内安全相对路径")
    return raw


def _validate_entity_id(value: str, label: str = "ID") -> str:
    value = str(value).strip()
    if not _ENTITY_ID_RE.fullmatch(value):
        raise ValueError(f"{label} 必须匹配 {_ENTITY_ID_RE.pattern}")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ModuleManifest(StrictModel):
    schema_uri: Literal[MANIFEST_SCHEMA_URI] = Field(
        default=MANIFEST_SCHEMA_URI,
        alias="$schema",
    )
    format_version: Literal["1.0"] = MODULE_FORMAT_VERSION
    id: str
    version: str
    title: str = Field(min_length=1, max_length=120)
    author: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=1000)
    system: str = Field(default="COC 第七版", max_length=100)
    era: str = Field(default="", max_length=100)
    language: str = Field(default="zh-CN", max_length=35)
    license: str = Field(default="", max_length=120)
    homepage: str = Field(default="", max_length=500)
    min_engine_version: str = Field(default="0.1.0")
    entry: Literal["module.json"] = "module.json"
    keeper_document: Literal["keeper.md"] | None = "keeper.md"
    theme: Literal["theme.json"] | None = "theme.json"
    capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=20)
    created_with: str = Field(default="", max_length=120)
    checksums: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        value = value.strip()
        if not _PACKAGE_ID_RE.fullmatch(value) or not is_portable_path_component(value):
            raise ValueError(f"模组 id 必须匹配 {_PACKAGE_ID_RE.pattern}")
        return value

    @field_validator("version", "min_engine_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        value = value.strip()
        if not _SEMVER_RE.fullmatch(value):
            raise ValueError("版本必须是 SemVer，例如 1.2.0")
        return value

    @field_validator("keeper_document", "theme")
    @classmethod
    def validate_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else _safe_relative_path(value, "文件路径")

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - _CAPABILITIES)
        if unknown:
            raise ValueError(f"未知 capability: {', '.join(unknown)}")
        if len(value) != len(set(value)):
            raise ValueError("capabilities 不能重复")
        return value

    @field_validator("checksums")
    @classmethod
    def validate_checksums(cls, value: dict[str, str]) -> dict[str, str]:
        result = {}
        for path, digest in value.items():
            safe_path = _safe_relative_path(path, "checksum 路径")
            digest = digest.lower()
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"{safe_path} 的 checksum 不是 SHA-256")
            result[safe_path] = digest
        return result


class AssetDefinition(StrictModel):
    file: str
    label: str = Field(default="", max_length=200)
    alt: str = Field(default="", max_length=500)
    media_type: str = Field(default="", max_length=100)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        value = _safe_relative_path(value, "素材路径")
        if not value.startswith("assets/"):
            raise ValueError("素材必须放在 assets/ 目录")
        return value


class AssetMapDefinition(StrictModel):
    npcs: dict[str, AssetDefinition] = Field(default_factory=dict)
    scenes: dict[str, AssetDefinition] = Field(default_factory=dict)
    clues: dict[str, AssetDefinition] = Field(default_factory=dict)


class NpcDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    visible_tags: list[str] = Field(default_factory=list)
    secret: str = ""
    hp: int = Field(default=10, ge=0)
    max_hp: int | None = Field(default=None, ge=0)
    disposition: str = Field(default="neutral", max_length=80)
    current_location: str | None = None
    attributes: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, int] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    spells: list[str] = Field(default_factory=list)
    notes: str = ""
    asset_id: str | None = None
    initial_reveal: int = Field(default=0, ge=0, le=3)
    initial_reveal_entries: list[dict[str, Any]] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("current_location", "asset_id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_entity_id(value)


class SceneDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1)
    exits: list[str] = Field(default_factory=list)
    npcs_present: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    document: str | None = None
    asset_id: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("exits", "npcs_present")
    @classmethod
    def validate_id_list(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value) for value in values]

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_entity_id(value)

    @field_validator("document")
    @classmethod
    def validate_document(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _safe_relative_path(value, "场景文档路径")
        if not value.startswith("scenes/"):
            raise ValueError("场景文档必须放在 scenes/ 目录")
        return value


class ClueDefinition(StrictModel):
    text: str = Field(min_length=1)
    category: Literal["investigation", "event", "task", "npc"] = "investigation"
    type: Literal["obvious", "hidden", "inferred"] = "obvious"
    tier: int = Field(default=1, ge=0, le=3)
    source: str | None = None
    related_npcs: list[str] = Field(default_factory=list)
    related_scenes: list[str] = Field(default_factory=list)
    asset_id: str | None = None
    initially_known: bool = False
    discovery_notes: str = ""
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("related_npcs", "related_scenes")
    @classmethod
    def validate_id_list(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value) for value in values]

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_entity_id(value)


class EndingDefinition(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    trigger: str = Field(min_length=1)
    description: str = Field(min_length=1)
    ending_type: Literal["good", "neutral", "bad", "secret"] = "neutral"


class ClueLinkDefinition(StrictModel):
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    reasoning: str = ""

    @field_validator("from_id", "to_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_entity_id(value, "线索 ID")


class PcTemplate(StrictModel):
    name: str = ""
    occupation: str = ""
    hp: int = Field(default=11, ge=0)
    max_hp: int = Field(default=11, ge=1)
    san: int = Field(default=65, ge=0)
    max_san: int = Field(default=65, ge=0)
    attributes: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, int] = Field(default_factory=dict)
    inventory: list[Any] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    psychological_profile: dict[str, list[Any]] = Field(default_factory=lambda: {
        "traits": [],
        "key_relationships": [],
        "phobias": [],
        "manias": [],
    })
    extensions: dict[str, Any] = Field(default_factory=dict)


class PrivateMemoryDefinition(StrictModel):
    goals_and_plans: str = ""
    hidden_facts: dict[str, str] = Field(default_factory=dict)
    inference_notes: str = "游戏刚开始。所有 NPC 秘密均未揭示。"


class InitialStateDefinition(StrictModel):
    pc: PcTemplate = Field(default_factory=PcTemplate)
    known_clue_ids: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)
    case_clocks: dict[str, int] = Field(default_factory=dict)
    private_memory: PrivateMemoryDefinition = Field(default_factory=PrivateMemoryDefinition)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("known_clue_ids")
    @classmethod
    def validate_known_clues(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value, "线索 ID") for value in values]


class ModuleDefinition(StrictModel):
    schema_uri: Literal[MODULE_SCHEMA_URI] = Field(
        default=MODULE_SCHEMA_URI,
        alias="$schema",
    )
    format_version: Literal["1.0"] = MODULE_FORMAT_VERSION
    entry_scene_id: str
    opening_prompt: str = ""
    npcs: dict[str, NpcDefinition] = Field(default_factory=dict)
    scenes: dict[str, SceneDefinition]
    clues: dict[str, ClueDefinition] = Field(default_factory=dict)
    endings: dict[str, EndingDefinition] = Field(default_factory=dict)
    rules: dict[str, Any] = Field(default_factory=dict)
    assets: AssetMapDefinition = Field(default_factory=AssetMapDefinition)
    initial_state: InitialStateDefinition = Field(default_factory=InitialStateDefinition)
    clue_links: list[ClueLinkDefinition] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entry_scene_id")
    @classmethod
    def validate_entry_scene_id(cls, value: str) -> str:
        return _validate_entity_id(value, "入口场景 ID")

    @model_validator(mode="after")
    def validate_references(self) -> "ModuleDefinition":
        for mapping_name in ("npcs", "scenes", "clues", "endings"):
            for entity_id in getattr(self, mapping_name):
                _validate_entity_id(entity_id, f"{mapping_name} ID")
        for mapping_name in ("npcs", "scenes", "clues"):
            for asset_id in getattr(self.assets, mapping_name):
                _validate_entity_id(asset_id, f"assets.{mapping_name} ID")

        if self.entry_scene_id not in self.scenes:
            raise ValueError(f"入口场景不存在: {self.entry_scene_id}")

        npc_ids = set(self.npcs)
        scene_ids = set(self.scenes)
        clue_ids = set(self.clues)
        for scene_id, scene in self.scenes.items():
            missing_exits = sorted(set(scene.exits) - scene_ids)
            missing_npcs = sorted(set(scene.npcs_present) - npc_ids)
            if missing_exits:
                raise ValueError(f"场景 {scene_id} 引用了不存在的出口: {missing_exits}")
            if missing_npcs:
                raise ValueError(f"场景 {scene_id} 引用了不存在的 NPC: {missing_npcs}")
            if scene.asset_id and scene.asset_id not in self.assets.scenes:
                raise ValueError(f"场景 {scene_id} 的素材不存在: {scene.asset_id}")

        for npc_id, npc in self.npcs.items():
            if npc.current_location and npc.current_location not in scene_ids:
                raise ValueError(f"NPC {npc_id} 的场景不存在: {npc.current_location}")
            if npc.asset_id and npc.asset_id not in self.assets.npcs:
                raise ValueError(f"NPC {npc_id} 的素材不存在: {npc.asset_id}")

        for clue_id, clue in self.clues.items():
            missing_npcs = sorted(set(clue.related_npcs) - npc_ids)
            missing_scenes = sorted(set(clue.related_scenes) - scene_ids)
            if missing_npcs:
                raise ValueError(f"线索 {clue_id} 引用了不存在的 NPC: {missing_npcs}")
            if missing_scenes:
                raise ValueError(f"线索 {clue_id} 引用了不存在的场景: {missing_scenes}")
            if clue.asset_id and clue.asset_id not in self.assets.clues:
                raise ValueError(f"线索 {clue_id} 的素材不存在: {clue.asset_id}")

        known = set(self.initial_state.known_clue_ids)
        known.update(clue_id for clue_id, clue in self.clues.items() if clue.initially_known)
        missing_known = sorted(known - clue_ids)
        if missing_known:
            raise ValueError(f"初始线索不存在: {missing_known}")

        for link in self.clue_links:
            missing = [clue_id for clue_id in (link.from_id, link.to_id) if clue_id not in clue_ids]
            if missing:
                raise ValueError(f"线索关联引用了不存在的线索: {missing}")
        return self


def compile_world_state(manifest: ModuleManifest, module: ModuleDefinition) -> dict[str, Any]:
    """兼容旧调用；新代码应从 ``module_compiler`` 导入。"""
    from .module_compiler import compile_world_state as compile_state

    return compile_state(manifest, module)


def render_keeper_prompt(
    manifest: ModuleManifest,
    module: ModuleDefinition,
    keeper_notes: str = "",
) -> str:
    """兼容旧调用；新代码应从 ``module_compiler`` 导入。"""
    from .module_compiler import render_keeper_prompt as render_prompt

    return render_prompt(manifest, module, keeper_notes)


def manifest_json_schema() -> dict[str, Any]:
    schema = ModuleManifest.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = MANIFEST_SCHEMA_URI
    return schema


def module_json_schema() -> dict[str, Any]:
    schema = ModuleDefinition.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = MODULE_SCHEMA_URI
    return schema
