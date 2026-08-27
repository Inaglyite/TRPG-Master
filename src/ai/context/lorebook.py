"""Character Card V3 Lorebook models and deterministic runtime retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOREBOOK_SPEC = "lorebook_v3"
LOREBOOK_SCHEMA_URI = "https://trpg-master.local/schemas/lorebook-v3.json"
DEFAULT_TOKEN_BUDGET = 600
MAX_LOREBOOK_ENTRIES = 2000
MAX_SELECTED_ENTRIES = 5


class CompatibleModel(BaseModel):
    """Accept future standard fields while keeping known fields validated."""

    model_config = ConfigDict(extra="allow")


class TrpgLoreExtension(CompatibleModel):
    kind: Literal[
        "fact",
        "sensory_palette",
        "npc_voice",
        "scene_pressure",
        "style",
    ] = "fact"
    scene_ids: list[str] = Field(default_factory=list, max_length=64)
    npc_ids: list[str] = Field(default_factory=list, max_length=64)
    required_flags: dict[str, Any] = Field(default_factory=dict)
    forbidden_flags: dict[str, Any] = Field(default_factory=dict)
    required_clue_ids: list[str] = Field(default_factory=list, max_length=64)
    visibility: Literal["public", "gated"] = "public"
    group: str | None = Field(default=None, max_length=80)
    cooldown_turns: int = Field(default=0, ge=0, le=100)
    weight: int = Field(default=1, ge=1, le=100)
    sensory_focus: str | None = Field(default=None, max_length=80)

    @field_validator("scene_ids", "npc_ids", "required_clue_ids")
    @classmethod
    def validate_ids(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if any(not value or len(value) > 120 for value in normalized):
            raise ValueError("扩展 ID 长度必须为 1-120 个字符")
        if len(normalized) != len(set(normalized)):
            raise ValueError("扩展 ID 不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_gated_visibility(self) -> TrpgLoreExtension:
        if self.visibility == "gated" and not (
            self.required_flags or self.required_clue_ids
        ):
            raise ValueError("gated 条目必须声明 required_flags 或 required_clue_ids")
        return self


class LorebookEntry(CompatibleModel):
    keys: list[str] = Field(max_length=64)
    content: str = Field(min_length=1, max_length=12000)
    extensions: dict[str, Any]
    enabled: bool
    insertion_order: int
    use_regex: bool
    constant: bool = False
    case_sensitive: bool = False
    name: str | None = Field(default=None, max_length=200)
    priority: int | None = None
    id: int | str | None = None
    comment: str | None = Field(default=None, max_length=1000)
    selective: bool = False
    secondary_keys: list[str] = Field(default_factory=list, max_length=64)
    position: Literal["before_char", "after_char"] | None = None

    @field_validator("keys", "secondary_keys")
    @classmethod
    def validate_keys(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if any(not value or len(value) > 120 for value in normalized):
            raise ValueError("Lorebook 触发词长度必须为 1-120 个字符")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Lorebook 触发词不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_activation(self) -> LorebookEntry:
        if not self.constant and not self.keys:
            raise ValueError("非常驻 Lorebook 条目至少需要一个 keys 触发词")
        if self.selective and not self.secondary_keys:
            raise ValueError("selective 条目至少需要一个 secondary_keys 触发词")
        extension = self.extensions.get("trpg_master")
        if extension is not None:
            TrpgLoreExtension.model_validate(extension)
        return self

    def trpg_extension(self) -> TrpgLoreExtension:
        return TrpgLoreExtension.model_validate(
            self.extensions.get("trpg_master") or {}
        )


class LorebookData(CompatibleModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scan_depth: int | None = Field(default=2, ge=0, le=50)
    token_budget: int | None = Field(default=DEFAULT_TOKEN_BUDGET, ge=0, le=8000)
    recursive_scanning: bool | None = False
    extensions: dict[str, Any]
    entries: list[LorebookEntry] = Field(max_length=MAX_LOREBOOK_ENTRIES)

    @model_validator(mode="after")
    def validate_entry_ids(self) -> LorebookData:
        explicit_ids = [str(entry.id) for entry in self.entries if entry.id is not None]
        if len(explicit_ids) != len(set(explicit_ids)):
            raise ValueError("Lorebook entry id 不能重复")
        return self


class LorebookEnvelope(CompatibleModel):
    schema_uri: Literal[LOREBOOK_SCHEMA_URI] | None = Field(
        default=None,
        alias="$schema",
    )
    spec: Literal["lorebook_v3"] = LOREBOOK_SPEC
    data: LorebookData


@dataclass(frozen=True)
class SelectedLoreEntry:
    entry_id: str
    kind: str
    content: str
    sensory_focus: str | None
    priority: int
    insertion_order: int


@dataclass(frozen=True)
class LoreTraceEntry:
    entry_id: str
    name: str | None
    kind: str
    group: str | None
    reason: str
    token_estimate: int
    matched_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoreSelection:
    entries: tuple[SelectedLoreEntry, ...]
    sequence: int
    token_estimate: int
    trace: tuple[LoreTraceEntry, ...] = ()

    @property
    def entry_ids(self) -> tuple[str, ...]:
        return tuple(entry.entry_id for entry in self.entries)

    @property
    def context(self) -> str:
        if not self.entries:
            return ""
        payload = [
            {
                "kind": entry.kind,
                "content": entry.content,
                **(
                    {"sensory_focus": entry.sensory_focus}
                    if entry.sensory_focus
                    else {}
                ),
            }
            for entry in self.entries
        ]
        return (
            "[本轮 Lorebook 检索素材｜仅供守秘人，不得复述标签]\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n用法：把素材自然融入本轮叙述；只选必要细节，不要逐条复述。"
            "sensory_palette/style 只控制表达，不得创造新事实；fact 仍须服从上方"
            "引擎权威状态与已揭示信息，冲突时丢弃本素材。"
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        reason_counts: dict[str, int] = {}
        for item in self.trace:
            reason_counts[item.reason] = reason_counts.get(item.reason, 0) + 1
        return {
            "sequence": self.sequence,
            "token_estimate": self.token_estimate,
            "selected": [
                {
                    "entry_id": entry.entry_id,
                    "kind": entry.kind,
                    "priority": entry.priority,
                    "insertion_order": entry.insertion_order,
                    "token_estimate": estimate_text_tokens(entry.content),
                }
                for entry in self.entries
            ],
            "reason_counts": reason_counts,
            "trace": [
                {
                    "entry_id": item.entry_id,
                    "name": item.name,
                    "kind": item.kind,
                    "group": item.group,
                    "reason": item.reason,
                    "token_estimate": item.token_estimate,
                    "matched_keys": list(item.matched_keys),
                }
                for item in self.trace
            ],
        }


def lorebook_json_schema() -> dict[str, Any]:
    schema = LorebookEnvelope.model_json_schema(
        by_alias=True,
        ref_template="#/$defs/{model}",
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = LOREBOOK_SCHEMA_URI
    return schema


def load_lorebook(path: Path) -> LorebookEnvelope | None:
    path = Path(path)
    if not path.is_file():
        return None
    return LorebookEnvelope.model_validate_json(path.read_text(encoding="utf-8"))


def validate_lorebook_references(
    lorebook: LorebookEnvelope,
    *,
    scene_ids: set[str],
    npc_ids: set[str],
    clue_ids: set[str],
    flag_ids: set[str],
) -> list[str]:
    """Validate TRPG Master extensions against one authoring module."""
    errors: list[str] = []
    for index, entry in enumerate(lorebook.data.entries):
        extension = entry.trpg_extension()
        label = str(entry.id) if entry.id is not None else str(index)
        missing_scenes = sorted(set(extension.scene_ids) - scene_ids)
        missing_npcs = sorted(set(extension.npc_ids) - npc_ids)
        missing_clues = sorted(set(extension.required_clue_ids) - clue_ids)
        referenced_flags = {
            path.split(".", 1)[0]
            for path in (*extension.required_flags, *extension.forbidden_flags)
        }
        missing_flags = sorted(referenced_flags - flag_ids)
        if missing_scenes:
            errors.append(f"entries[{label}] 引用了不存在的场景: {missing_scenes}")
        if missing_npcs:
            errors.append(f"entries[{label}] 引用了不存在的 NPC: {missing_npcs}")
        if missing_clues:
            errors.append(f"entries[{label}] 引用了不存在的线索: {missing_clues}")
        if missing_flags:
            errors.append(f"entries[{label}] 引用了不存在的 flag: {missing_flags}")
    return errors


def estimate_text_tokens(text: str) -> int:
    """Cheap conservative estimate suitable for a small retrieval budget."""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    remainder = re.sub(r"[\u3400-\u9fff\s]", "", text)
    return max(1, cjk + math.ceil(len(remainder) / 4))


def _entry_id(entry: LorebookEntry, index: int) -> str:
    return str(entry.id) if entry.id is not None else f"entry-{index}"


def _known_clue_ids(world: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    groups = world.get("clues_found", {})
    if isinstance(groups, dict):
        collections = groups.values()
    elif isinstance(groups, list):
        collections = [groups]
    else:
        collections = []
    for clues in collections:
        if not isinstance(clues, list):
            continue
        for clue in clues:
            if not isinstance(clue, dict):
                continue
            clue_id = clue.get("catalog_id") or clue.get("id")
            if clue_id:
                result.add(str(clue_id))
    return result


def _nested_value(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _matches_term(text: str, term: str, case_sensitive: bool) -> bool:
    if not case_sensitive:
        text = text.casefold()
        term = term.casefold()
    return term in text


def _scan_text(messages: list[dict], player_action: str, depth: int) -> str:
    if depth <= 0:
        return ""
    recent: list[str] = []
    if player_action:
        recent.append(player_action)
    for message in reversed(messages):
        if len(recent) >= depth:
            break
        if message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            if content.startswith("[引擎控制指令｜非玩家发言]"):
                continue
            if message.get("role") == "user":
                for marker in (
                    "\n\n[引擎权威状态｜仅供守秘人，不得复述]",
                    "\n\n[本轮 Lorebook 检索素材｜仅供守秘人，不得复述标签]",
                ):
                    content = content.split(marker, 1)[0]
            recent.append(content)
    return "\n".join(recent)


def _extension_block_reason(
    extension: TrpgLoreExtension,
    world: dict[str, Any],
    known_clues: set[str],
) -> str | None:
    scene = world.get("current_scene") or {}
    scene_id = str(scene.get("id") or "")
    if extension.scene_ids and scene_id not in extension.scene_ids:
        return "scene_gate"

    if extension.npc_ids:
        present = {str(value) for value in scene.get("npcs_present", [])}
        if not present.intersection(extension.npc_ids):
            return "npc_gate"

    flags = world.get("flags") or {}
    if any(_nested_value(flags, key) != value for key, value in extension.required_flags.items()):
        return "required_flag_gate"
    if any(_nested_value(flags, key) == value for key, value in extension.forbidden_flags.items()):
        return "forbidden_flag_gate"
    if not set(extension.required_clue_ids).issubset(known_clues):
        return "required_clue_gate"
    return None


def _recent_usage(world: dict[str, Any]) -> tuple[int, dict[str, int]]:
    memory = world.get("narrative_memory") or {}
    if not isinstance(memory, dict):
        memory = {}
    try:
        sequence = max(0, int(memory.get("turn_sequence", 0)))
    except (TypeError, ValueError):
        sequence = 0
    usage: dict[str, int] = {}
    for item in memory.get("recent_lore", []):
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        try:
            usage[str(item["id"])] = int(item.get("turn", 0))
        except (TypeError, ValueError):
            continue
    return sequence, usage


def _weighted_group_pick(
    candidates: list[tuple[LorebookEntry, int, str, TrpgLoreExtension]],
    group: str,
    sequence: int,
    world: dict[str, Any],
) -> tuple[LorebookEntry, int, str, TrpgLoreExtension]:
    seed = f"{world.get('module', '')}:{group}:{sequence}".encode()
    number = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big")
    total = sum(candidate[3].weight for candidate in candidates)
    target = number % total
    cursor = 0
    for candidate in candidates:
        cursor += candidate[3].weight
        if target < cursor:
            return candidate
    return candidates[-1]


def select_lore(
    lorebook: LorebookEnvelope,
    world: dict[str, Any],
    messages: list[dict],
    player_action: str = "",
    *,
    max_entries: int = MAX_SELECTED_ENTRIES,
) -> LoreSelection:
    """Select a bounded set without model calls, embeddings, or mutable randomness."""
    data = lorebook.data
    depth = data.scan_depth if data.scan_depth is not None else 2
    text = _scan_text(messages, player_action, depth)
    sequence, recent_usage = _recent_usage(world)
    next_sequence = sequence + 1
    known_clues = _known_clue_ids(world)
    candidates: list[tuple[LorebookEntry, int, str, TrpgLoreExtension]] = []
    trace_by_id: dict[str, LoreTraceEntry] = {}
    trace_order: list[str] = []

    def trace(
        entry: LorebookEntry,
        index: int,
        extension: TrpgLoreExtension,
        reason: str,
        *,
        matched_keys: tuple[str, ...] = (),
    ) -> None:
        entry_id = _entry_id(entry, index)
        if entry_id not in trace_by_id:
            trace_order.append(entry_id)
        trace_by_id[entry_id] = LoreTraceEntry(
            entry_id=entry_id,
            name=entry.name,
            kind=extension.kind,
            group=extension.group,
            reason=reason,
            token_estimate=estimate_text_tokens(entry.content),
            matched_keys=matched_keys,
        )

    for index, entry in enumerate(data.entries):
        extension = entry.trpg_extension()
        if not entry.enabled:
            trace(entry, index, extension, "disabled")
            continue
        if entry.use_regex:
            trace(entry, index, extension, "regex_unsupported")
            continue
        block_reason = _extension_block_reason(extension, world, known_clues)
        if block_reason:
            trace(entry, index, extension, block_reason)
            continue
        entry_id = _entry_id(entry, index)
        last_turn = recent_usage.get(entry_id)
        if (
            last_turn is not None
            and extension.cooldown_turns > 0
            and next_sequence - last_turn <= extension.cooldown_turns
        ):
            trace(entry, index, extension, "cooldown")
            continue
        matched_primary = tuple(
            key for key in entry.keys
            if _matches_term(text, key, entry.case_sensitive)
        )
        primary_match = entry.constant or bool(matched_primary)
        if not primary_match:
            trace(entry, index, extension, "primary_key_miss")
            continue
        matched_secondary = tuple(
            key for key in entry.secondary_keys
            if _matches_term(text, key, entry.case_sensitive)
        )
        if entry.selective and not matched_secondary:
            trace(
                entry,
                index,
                extension,
                "secondary_key_miss",
                matched_keys=matched_primary,
            )
            continue
        trace(
            entry,
            index,
            extension,
            "candidate",
            matched_keys=matched_primary + matched_secondary,
        )
        candidates.append((entry, index, entry_id, extension))

    grouped: dict[str, list[tuple[LorebookEntry, int, str, TrpgLoreExtension]]] = {}
    ungrouped = []
    for candidate in candidates:
        group = candidate[3].group
        if group:
            grouped.setdefault(group, []).append(candidate)
        else:
            ungrouped.append(candidate)
    for group, group_candidates in grouped.items():
        winner = _weighted_group_pick(group_candidates, group, next_sequence, world)
        ungrouped.append(winner)
        for candidate in group_candidates:
            if candidate is winner:
                continue
            entry, index, _, extension = candidate
            previous = trace_by_id[_entry_id(entry, index)]
            trace(
                entry,
                index,
                extension,
                "group_not_selected",
                matched_keys=previous.matched_keys,
            )

    ungrouped.sort(key=lambda item: (
        -(item[0].priority if item[0].priority is not None else 100),
        item[0].insertion_order,
        item[2],
    ))
    budget = data.token_budget if data.token_budget is not None else DEFAULT_TOKEN_BUDGET
    chosen: list[SelectedLoreEntry] = []
    token_total = 0
    for entry, _, entry_id, extension in ungrouped:
        estimate = estimate_text_tokens(entry.content)
        previous = trace_by_id[entry_id]
        if len(chosen) >= max_entries:
            trace(
                entry,
                _,
                extension,
                "entry_limit",
                matched_keys=previous.matched_keys,
            )
            continue
        if token_total + estimate > budget:
            trace(
                entry,
                _,
                extension,
                "token_budget",
                matched_keys=previous.matched_keys,
            )
            continue
        chosen.append(SelectedLoreEntry(
            entry_id=entry_id,
            kind=extension.kind,
            content=entry.content.strip(),
            sensory_focus=extension.sensory_focus,
            priority=entry.priority if entry.priority is not None else 100,
            insertion_order=entry.insertion_order,
        ))
        trace(
            entry,
            _,
            extension,
            "selected",
            matched_keys=previous.matched_keys,
        )
        token_total += estimate
    chosen.sort(key=lambda item: (item.insertion_order, -item.priority, item.entry_id))
    return LoreSelection(
        tuple(chosen),
        next_sequence,
        token_total,
        tuple(trace_by_id[entry_id] for entry_id in trace_order),
    )


def record_lore_usage(world: dict[str, Any], entry_ids: tuple[str, ...]) -> None:
    """Advance the persisted narrative clock and retain a compact cooldown window."""
    memory = world.get("narrative_memory")
    if not isinstance(memory, dict):
        memory = {}
        world["narrative_memory"] = memory
    try:
        sequence = max(0, int(memory.get("turn_sequence", 0))) + 1
    except (TypeError, ValueError):
        sequence = 1
    memory["turn_sequence"] = sequence
    latest: dict[str, dict[str, Any]] = {}
    for item in memory.get("recent_lore", []):
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        entry_id = str(item["id"])
        latest.pop(entry_id, None)
        latest[entry_id] = item
    for entry_id in entry_ids:
        latest.pop(entry_id, None)
        latest[entry_id] = {"id": entry_id, "turn": sequence}
    memory["recent_lore"] = list(latest.values())[-512:]
