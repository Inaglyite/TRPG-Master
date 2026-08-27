"""TRPG Master v1 模组定义与 JSON Schema。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODULE_FORMAT_VERSION = "1.0"
ENGINE_VERSION = "1.0.0"
MANIFEST_SCHEMA_URI = "https://trpg-master.local/schemas/module-manifest-v1.json"
MODULE_SCHEMA_URI = "https://trpg-master.local/schemas/module-v1.json"
MANIFEST_V2_SCHEMA_URI = "https://trpg-master.local/schemas/module-manifest-v2.json"
MODULE_V2_SCHEMA_URI = "https://trpg-master.local/schemas/module-v2.json"

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
HANDOUT_REVEAL_EVENTS = (
    "npc_revealed",
    "scene_entered",
    "clue_discovered",
    "sanity_triggered",
)
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
    lorebook: Literal["lorebook.json"] | None = None
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

    @field_validator("keeper_document", "theme", "lorebook")
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


class AssetRevealTrigger(StrictModel):
    event: Literal[
        "npc_revealed",
        "scene_entered",
        "clue_discovered",
        "sanity_triggered",
    ]
    entity_id: str | None = None
    match_all: list[str] = Field(default_factory=list, max_length=8)
    match_any: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_entity_id(value, "素材触发实体 ID")

    @field_validator("match_all", "match_any")
    @classmethod
    def validate_match_terms(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if any(not value or len(value) > 120 for value in normalized):
            raise ValueError("素材触发词长度必须为 1-120 个字符")
        if len(normalized) != len(set(normalized)):
            raise ValueError("素材触发词不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_match_condition(self) -> AssetRevealTrigger:
        if not self.entity_id and not self.match_all and not self.match_any:
            raise ValueError("素材触发规则必须指定 entity_id 或文本匹配条件")
        if self.event == "sanity_triggered" and not self.match_all and not self.match_any:
            raise ValueError("sanity_triggered 必须指定文本匹配条件")
        if self.event == "sanity_triggered" and self.entity_id:
            raise ValueError("sanity_triggered 没有实体 ID，请使用文本匹配条件")
        return self


class AssetDefinition(StrictModel):
    file: str
    label: str = Field(default="", max_length=200)
    alt: str = Field(default="", max_length=500)
    media_type: str = Field(default="", max_length=100)
    reveal_on: list[AssetRevealTrigger] = Field(default_factory=list, max_length=16)

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


class EncounterDefinition(StrictModel):
    id: str
    npc_id: str
    availability: Literal["guaranteed", "conditional", "luck", "unavailable"] = (
        "guaranteed"
    )
    required_flags: dict[str, bool | int | str] = Field(default_factory=dict)
    forbidden_flags: dict[str, bool | int | str] = Field(default_factory=dict)
    luck_difficulty: Literal["regular", "hard", "extreme"] = "regular"
    repeat: Literal["once", "always"] = "once"
    on_present_text: str = Field(default="", max_length=500)
    on_absent_text: str = Field(default="", max_length=500)

    @field_validator("id", "npc_id")
    @classmethod
    def validate_npc_id(cls, value: str) -> str:
        return _validate_entity_id(value, "遭遇/NPC ID")

    @model_validator(mode="after")
    def validate_conditional_flags(self) -> EncounterDefinition:
        if (
            self.availability == "conditional"
            and not self.required_flags
            and not self.forbidden_flags
        ):
            raise ValueError("conditional 遭遇必须指定 required_flags 或 forbidden_flags")
        return self


class SceneEntryBeatDefinition(StrictModel):
    npc_id: str
    public_text: str = Field(min_length=1, max_length=1200)

    @field_validator("npc_id")
    @classmethod
    def validate_npc_id(cls, value: str) -> str:
        return _validate_entity_id(value, "入场节拍 NPC ID")


class ActionRouteDefinition(StrictModel):
    id: str
    aliases: list[str] = Field(min_length=1)
    destination_scene_id: str
    required_flags: dict[str, bool | int | str] = Field(default_factory=dict)
    forbidden_flags: dict[str, bool | int | str] = Field(default_factory=dict)
    departure_text: str = Field(default="", max_length=1200)
    travel_text: str = Field(default="", max_length=1200)
    entry_text: str = Field(default="", max_length=1200)

    @field_validator("id", "destination_scene_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _validate_entity_id(value, "行动路线 ID/目的地")

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        aliases = [str(value).strip() for value in values]
        if any(len(value) < 2 for value in aliases):
            raise ValueError("行动路线别名至少需要两个字符")
        return list(dict.fromkeys(aliases))


class ActionPreviewTriggerDefinition(StrictModel):
    always: bool = False
    skill_below: dict[str, int] = Field(default_factory=dict)
    attribute_below: dict[str, int] = Field(default_factory=dict)
    occupation_contains_any: list[str] = Field(default_factory=list)
    traits_contain_any: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_trigger(self) -> ActionPreviewTriggerDefinition:
        values = [*self.skill_below.values(), *self.attribute_below.values()]
        if any(value < 0 or value > 100 for value in values):
            raise ValueError("行动预演技能/属性阈值必须在 0 到 100 之间")
        if not (
            self.always
            or self.skill_below
            or self.attribute_below
            or self.occupation_contains_any
            or self.traits_contain_any
        ):
            raise ValueError("行动预演 trigger_if 至少声明一个公开条件")
        return self


class ActionPrepareOptionDefinition(StrictModel):
    id: str
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=160)
    action_text: str = Field(min_length=1, max_length=500)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_entity_id(value, "准备选项 ID")


class ActionAdvisoryDefinition(StrictModel):
    id: str
    destination_scene_id: str
    transition_kinds: list[
        Literal["explicit_move", "authored_route", "discovery_target"]
    ] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)
    required_flags: dict[str, bool | int | str] = Field(default_factory=dict)
    forbidden_flags: dict[str, bool | int | str] = Field(default_factory=dict)
    trigger_if: ActionPreviewTriggerDefinition | None = None
    enabled: bool = True
    title: str = Field(default="", max_length=120)
    npc_id: str | None = None
    npc_text: str = Field(default="", max_length=1600)
    keeper_text: str = Field(default="", max_length=1600)
    public_hint: str = Field(default="", max_length=500)
    continue_label: str = Field(default="", max_length=80)
    continue_description: str = Field(default="", max_length=160)
    prepare_options: list[ActionPrepareOptionDefinition] = Field(
        default_factory=list,
        max_length=3,
    )
    cancel_label: str = Field(default="", max_length=80)
    cancel_description: str = Field(default="", max_length=160)

    @field_validator("id", "destination_scene_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _validate_entity_id(value, "行动预演 ID/目的地")

    @field_validator("npc_id")
    @classmethod
    def validate_optional_npc_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_entity_id(value, "行动预演 NPC ID")

    @field_validator("route_ids")
    @classmethod
    def validate_route_ids(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value, "行动路线 ID") for value in values]

    @model_validator(mode="after")
    def validate_public_warning(self) -> ActionAdvisoryDefinition:
        if self.npc_id and not self.npc_text:
            raise ValueError("行动预演声明 npc_id 时必须提供 npc_text")
        if not self.npc_text and not self.keeper_text and not self.public_hint:
            raise ValueError("行动预演必须提供 NPC、守秘人提醒或公开提示")
        option_ids = [option.id for option in self.prepare_options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("行动预演准备选项 ID 不能重复")
        return self


class SceneDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    exits: list[str] = Field(default_factory=list)
    npcs_present: list[str] = Field(default_factory=list)
    encounters: list[EncounterDefinition] = Field(default_factory=list)
    action_routes: list[ActionRouteDefinition] = Field(default_factory=list)
    action_advisories: list[ActionAdvisoryDefinition] = Field(default_factory=list)
    entry_beat: SceneEntryBeatDefinition | None = None
    tags: list[str] = Field(default_factory=list)
    document: str | None = None
    asset_id: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("exits", "npcs_present")
    @classmethod
    def validate_id_list(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value) for value in values]

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        aliases = [str(value).strip() for value in values]
        if any(len(value) < 2 for value in aliases):
            raise ValueError("场景别名至少需要两个字符")
        return list(dict.fromkeys(aliases))

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


class NpcRevealEffectDefinition(StrictModel):
    npc_id: str
    tier: int = Field(default=1, ge=1, le=3)
    entry_text: str = Field(min_length=1, max_length=500)

    @field_validator("npc_id")
    @classmethod
    def validate_npc_id(cls, value: str) -> str:
        return _validate_entity_id(value, "NPC ID")


class DiscoveryFallbackDefinition(StrictModel):
    mode: Literal["grant_clue", "alternate_clue"]
    clue_id: str | None = None
    narrative: str = Field(default="", max_length=500)
    cost_clock: str | None = None
    cost_amount: int = Field(default=0, ge=0, le=100)

    @field_validator("clue_id", "cost_clock")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_entity_id(value)

    @model_validator(mode="after")
    def validate_mode(self) -> DiscoveryFallbackDefinition:
        if self.mode == "alternate_clue" and not self.clue_id:
            raise ValueError("alternate_clue 保底必须指定 clue_id")
        if self.mode == "grant_clue" and self.clue_id:
            raise ValueError("grant_clue 保底自动发放当前线索，不能指定 clue_id")
        if self.cost_amount and not self.cost_clock:
            raise ValueError("failure cost_amount 必须同时指定 cost_clock")
        return self


class DiscoveryRuleDefinition(StrictModel):
    intent: Literal["examine", "search", "read", "take", "talk", "enter", "use"]
    targets: list[str] = Field(min_length=1)
    approach_text: str = Field(default="", max_length=500)
    skill: str | None = Field(default=None, min_length=1, max_length=100)
    check_type: Literal["skill", "luck"] | None = None
    difficulty: Literal["regular", "hard", "extreme"] = "regular"
    requires_success: bool = False
    sanity_severity: Literal["minor", "moderate", "major"] | None = None
    npc_reveals: list[NpcRevealEffectDefinition] = Field(default_factory=list)
    fallback: DiscoveryFallbackDefinition | None = None
    requires_flags: list[str] = Field(default_factory=list)

    @field_validator("requires_flags")
    @classmethod
    def validate_requires_flags(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value) for value in values]

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, values: list[str]) -> list[str]:
        targets = [str(value).strip() for value in values]
        if any(not value for value in targets):
            raise ValueError("发现规则的目标别名不能为空")
        return targets

    @model_validator(mode="after")
    def validate_required_skill(self) -> DiscoveryRuleDefinition:
        if self.check_type == "luck" and self.skill:
            raise ValueError("幸运发现规则不能同时指定 skill")
        if self.requires_success and self.check_type != "luck" and not self.skill:
            raise ValueError("requires_success=true 时必须指定 skill")
        if self.check_type == "luck" and not self.requires_success:
            raise ValueError("check_type=luck 时 requires_success 必须为 true")
        return self


class ClueDefinition(StrictModel):
    text: str = Field(min_length=1)
    category: Literal["investigation", "event", "task", "npc"] = "investigation"
    type: Literal["obvious", "hidden", "inferred"] = "obvious"
    tier: int = Field(default=1, ge=0, le=3)
    source: str | None = None
    related_npcs: list[str] = Field(default_factory=list)
    related_scenes: list[str] = Field(default_factory=list)
    asset_id: str | None = None
    granted_item: str | None = Field(default=None, max_length=200)
    flag_effects: dict[str, bool | int | str] = Field(default_factory=dict)
    discovery_rules: list[DiscoveryRuleDefinition] = Field(default_factory=list)
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

    @field_validator("granted_item")
    @classmethod
    def validate_granted_item(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class EndingDefinition(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    trigger: str = Field(min_length=1)
    description: str = Field(min_length=1)
    ending_type: Literal["good", "neutral", "bad", "secret"] = "neutral"
    required_flags: dict[str, bool | int | str] = Field(default_factory=dict)


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
    granted_items: list[Any] = Field(default_factory=list)
    known_clue_ids: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)
    case_clocks: dict[str, int] = Field(default_factory=dict)
    # 时钟等级表（键必须与 case_clocks 一致）：审计按此把叙事事件记成时钟
    # 数值，引擎按 max 封顶确定性推进。缺省为空表示该模组无时钟机制。
    case_clock_definitions: dict[str, Any] = Field(default_factory=dict)
    private_memory: PrivateMemoryDefinition = Field(default_factory=PrivateMemoryDefinition)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("known_clue_ids")
    @classmethod
    def validate_known_clues(cls, values: list[str]) -> list[str]:
        return [_validate_entity_id(value, "线索 ID") for value in values]

    @model_validator(mode="after")
    def validate_clock_definitions(self) -> InitialStateDefinition:
        unknown = sorted(set(self.case_clock_definitions) - set(self.case_clocks))
        if unknown:
            raise ValueError(f"case_clock_definitions 引用了未声明的时钟: {unknown}")
        for clock_id, definition in self.case_clock_definitions.items():
            if not isinstance(definition, dict):
                raise ValueError(f"时钟 {clock_id} 的定义必须是对象")
            levels = definition.get("levels")
            if not isinstance(levels, dict) or not levels:
                raise ValueError(f"时钟 {clock_id} 缺少 levels 等级表")
            max_value = definition.get("max")
            if max_value is not None and (
                isinstance(max_value, bool) or not isinstance(max_value, int) or max_value < 1
            ):
                raise ValueError(f"时钟 {clock_id} 的 max 必须是正整数")
        return self


class ProgressionDefinition(StrictModel):
    essential_clue_ids: list[str] = Field(default_factory=list)

    @field_validator("essential_clue_ids")
    @classmethod
    def validate_essential_clues(cls, values: list[str]) -> list[str]:
        normalized = [_validate_entity_id(value, "主线线索 ID") for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("essential_clue_ids 不能重复")
        return normalized


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
    def validate_references(self) -> ModuleDefinition:
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
        flag_ids = set(self.initial_state.flags)
        trigger_targets = {
            "npc_revealed": npc_ids,
            "scene_entered": scene_ids,
            "clue_discovered": clue_ids,
        }
        for group_name in ("npcs", "scenes", "clues"):
            for asset_id, asset in getattr(self.assets, group_name).items():
                for trigger in asset.reveal_on:
                    targets = trigger_targets.get(trigger.event)
                    if trigger.entity_id and targets is not None:
                        if trigger.entity_id not in targets:
                            raise ValueError(
                                f"素材 {asset_id} 的 {trigger.event} 触发实体不存在: "
                                f"{trigger.entity_id}"
                            )
        for scene_id, scene in self.scenes.items():
            missing_exits = sorted(set(scene.exits) - scene_ids)
            missing_npcs = sorted(set(scene.npcs_present) - npc_ids)
            if missing_exits:
                raise ValueError(f"场景 {scene_id} 引用了不存在的出口: {missing_exits}")
            if missing_npcs:
                raise ValueError(f"场景 {scene_id} 引用了不存在的 NPC: {missing_npcs}")
            missing_encounter_npcs = sorted({
                encounter.npc_id
                for encounter in scene.encounters
                if encounter.npc_id not in npc_ids
            })
            if missing_encounter_npcs:
                raise ValueError(
                    f"场景 {scene_id} 的遭遇引用了不存在的 NPC: "
                    f"{missing_encounter_npcs}"
                )
            encounter_ids = [encounter.id for encounter in scene.encounters]
            if len(encounter_ids) != len(set(encounter_ids)):
                raise ValueError(f"场景 {scene_id} 的遭遇 ID 重复")
            for encounter in scene.encounters:
                missing_flags = sorted(
                    (set(encounter.required_flags) | set(encounter.forbidden_flags))
                    - set(self.initial_state.flags)
                )
                if missing_flags:
                    raise ValueError(
                        f"场景 {scene_id} 的遭遇引用了不存在的 flag: {missing_flags}"
                    )
            route_ids = [route.id for route in scene.action_routes]
            if len(route_ids) != len(set(route_ids)):
                raise ValueError(f"场景 {scene_id} 的行动路线 ID 重复")
            for route in scene.action_routes:
                if route.destination_scene_id not in scene_ids:
                    raise ValueError(
                        f"场景 {scene_id} 的行动路线目的地不存在: "
                        f"{route.destination_scene_id}"
                    )
                missing_flags = sorted(
                    (set(route.required_flags) | set(route.forbidden_flags))
                    - flag_ids
                )
                if missing_flags:
                    raise ValueError(
                        f"场景 {scene_id} 的行动路线引用了不存在的 flag: "
                        f"{missing_flags}"
                    )
            advisory_ids = [advisory.id for advisory in scene.action_advisories]
            if len(advisory_ids) != len(set(advisory_ids)):
                raise ValueError(f"场景 {scene_id} 的行动预演 ID 重复")
            for advisory in scene.action_advisories:
                if advisory.destination_scene_id not in scene_ids:
                    raise ValueError(
                        f"场景 {scene_id} 的行动预演目的地不存在: "
                        f"{advisory.destination_scene_id}"
                    )
                if advisory.npc_id and advisory.npc_id not in scene.npcs_present:
                    raise ValueError(
                        f"场景 {scene_id} 的行动预演 NPC 不在初始场景中: "
                        f"{advisory.npc_id}"
                    )
                missing_routes = sorted(set(advisory.route_ids) - set(route_ids))
                if missing_routes:
                    raise ValueError(
                        f"场景 {scene_id} 的行动预演引用了不存在的路线: "
                        f"{missing_routes}"
                    )
                missing_flags = sorted(
                    (set(advisory.required_flags) | set(advisory.forbidden_flags))
                    - flag_ids
                )
                if missing_flags:
                    raise ValueError(
                        f"场景 {scene_id} 的行动预演引用了不存在的 flag: "
                        f"{missing_flags}"
                    )
            if scene.entry_beat:
                if scene.entry_beat.npc_id not in scene.npcs_present:
                    raise ValueError(
                        f"场景 {scene_id} 的 entry_beat NPC 不在初始场景中: "
                        f"{scene.entry_beat.npc_id}"
                    )
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
            for rule in clue.discovery_rules:
                missing_reveals = sorted({
                    reveal.npc_id
                    for reveal in rule.npc_reveals
                    if reveal.npc_id not in npc_ids
                })
                if missing_reveals:
                    raise ValueError(
                        f"线索 {clue_id} 的发现规则引用了不存在的 NPC: "
                        f"{missing_reveals}"
                    )

        known = set(self.initial_state.known_clue_ids)
        known.update(clue_id for clue_id, clue in self.clues.items() if clue.initially_known)
        missing_known = sorted(known - clue_ids)
        if missing_known:
            raise ValueError(f"初始线索不存在: {missing_known}")

        for clue_id, clue in self.clues.items():
            missing_flags = sorted(set(clue.flag_effects) - flag_ids)
            if missing_flags:
                raise ValueError(
                    f"线索 {clue_id} 的 flag_effects 不存在: {missing_flags}"
                )
            missing_rule_flags = sorted(
                {
                    str(flag)
                    for rule in clue.discovery_rules
                    for flag in rule.requires_flags
                }
                - flag_ids
            )
            if missing_rule_flags:
                raise ValueError(
                    f"线索 {clue_id} 的 discovery_rules.requires_flags 不存在: "
                    f"{missing_rule_flags}"
                )
        for ending_id, ending in self.endings.items():
            missing_flags = sorted(set(ending.required_flags) - flag_ids)
            if missing_flags:
                raise ValueError(
                    f"结局 {ending_id} 的 required_flags 不存在: {missing_flags}"
                )

        for link in self.clue_links:
            missing = [clue_id for clue_id in (link.from_id, link.to_id) if clue_id not in clue_ids]
            if missing:
                raise ValueError(f"线索关联引用了不存在的线索: {missing}")
        return self


class ModuleManifestV2(ModuleManifest):
    schema_uri: Literal[MANIFEST_V2_SCHEMA_URI] = Field(
        default=MANIFEST_V2_SCHEMA_URI,
        alias="$schema",
    )
    format_version: Literal["2.0"] = "2.0"


class ModuleDefinitionV2(ModuleDefinition):
    schema_uri: Literal[MODULE_V2_SCHEMA_URI] = Field(
        default=MODULE_V2_SCHEMA_URI,
        alias="$schema",
    )
    format_version: Literal["2.0"] = "2.0"
    progression: ProgressionDefinition = Field(default_factory=ProgressionDefinition)

    @model_validator(mode="after")
    def validate_progression_safety(self) -> ModuleDefinitionV2:
        clue_ids = set(self.clues)
        missing = sorted(set(self.progression.essential_clue_ids) - clue_ids)
        if missing:
            raise ValueError(f"主线线索不存在: {missing}")
        reachable = {self.entry_scene_id}
        frontier = [self.entry_scene_id]
        while frontier:
            scene_id = frontier.pop()
            for target in self.scenes[scene_id].exits:
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        unreachable = sorted(set(self.scenes) - reachable)
        if unreachable:
            raise ValueError(f"入口场景无法到达以下场景: {unreachable}")
        for clue_id in self.progression.essential_clue_ids:
            clue = self.clues[clue_id]
            if clue.initially_known:
                continue
            if not clue.discovery_rules:
                raise ValueError(f"主线线索 {clue_id} 没有发现规则")
            for rule in clue.discovery_rules:
                if rule.requires_success and rule.fallback is None:
                    raise ValueError(
                        f"主线线索 {clue_id} 的随机/检定失败路径缺少 fallback"
                    )
                fallback = rule.fallback
                if (
                    fallback
                    and fallback.cost_clock
                    and fallback.cost_clock not in self.initial_state.case_clocks
                ):
                    raise ValueError(
                        f"主线线索 {clue_id} 的 fallback cost_clock 不存在: "
                        f"{fallback.cost_clock}"
                    )
                if (
                    fallback
                    and fallback.mode == "alternate_clue"
                    and fallback.clue_id not in clue_ids
                ):
                    raise ValueError(
                        f"主线线索 {clue_id} 的 fallback 引用了不存在的线索: "
                        f"{fallback.clue_id}"
                    )
                if fallback and fallback.mode == "alternate_clue":
                    alternate = self.clues[fallback.clue_id]
                    if not alternate.initially_known and not alternate.discovery_rules:
                        raise ValueError(
                            f"主线线索 {clue_id} 的 alternate fallback "
                            f"{fallback.clue_id} 自身没有发现路径"
                        )
        return self


def parse_manifest(payload: Any) -> ModuleManifest | ModuleManifestV2:
    if isinstance(payload, dict) and payload.get("format_version") == "2.0":
        return ModuleManifestV2.model_validate(payload)
    return ModuleManifest.model_validate(payload)


def parse_module(payload: Any) -> ModuleDefinition | ModuleDefinitionV2:
    if isinstance(payload, dict) and payload.get("format_version") == "2.0":
        return ModuleDefinitionV2.model_validate(payload)
    return ModuleDefinition.model_validate(payload)


def compile_world_state(manifest: ModuleManifest, module: ModuleDefinition) -> dict[str, Any]:
    """兼容旧调用；新代码应从 ``module_compiler`` 导入。"""
    from src.modules.module_compiler import compile_world_state as compile_state

    return compile_state(manifest, module)


def render_keeper_prompt(
    manifest: ModuleManifest,
    module: ModuleDefinition,
    keeper_notes: str = "",
) -> str:
    """兼容旧调用；新代码应从 ``module_compiler`` 导入。"""
    from src.modules.module_compiler import render_keeper_prompt as render_prompt

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


def manifest_v2_json_schema() -> dict[str, Any]:
    schema = ModuleManifestV2.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = MANIFEST_V2_SCHEMA_URI
    return schema


def module_v2_json_schema() -> dict[str, Any]:
    schema = ModuleDefinitionV2.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = MODULE_V2_SCHEMA_URI
    return schema
