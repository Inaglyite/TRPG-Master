"""Safety boundary for H2 narrative-context summaries.

The summary model is a lossy, non-authoritative helper. It must never turn a
keeper-only control instruction or an unrevealed authored fact into a durable
model-visible summary. This module uses deterministic, fail-closed checks; it
does not ask another model to decide what is safe.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

CONTROL_MESSAGE_PREFIX = "[引擎控制指令｜非玩家发言]"
_WHITESPACE = re.compile(r"\s+")
# A summary is an optional continuity cache.  It is therefore better to
# reject a few extra candidates than to accept a short authored secret such
# as a password, codename, location or one-line private memory.  Do not use a
# length threshold here: matching is exact after whitespace normalization and
# every non-empty private text fragment is protected.
_MIN_PROTECTED_CHARS = 1
_SUMMARY_KEYS = frozenset({"events", "known_facts", "open_threads", "current_scene", "checks"})
_SUMMARY_LIST_KEYS = frozenset({"events", "known_facts", "open_threads", "checks"})
_SUMMARY_ITEM_LIMIT = 48
_SUMMARY_TEXT_LIMIT = 1_500


def is_control_message(message: Mapping[str, Any]) -> bool:
    """Whether a message is an engine-only instruction, not player history."""
    return str(message.get("role") or "") == "user" and str(
        message.get("content") or ""
    ).startswith(CONTROL_MESSAGE_PREFIX)


def _normalise(value: str) -> str:
    return _WHITESPACE.sub("", value).casefold()


def _strings(value: object) -> Iterable[str]:
    """Yield text leaves from a JSON-shaped private payload."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def protected_summary_fragments(world_state: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return keeper-only text that must not be echoed into a summary.

    The primary prevention is that engine control instructions are excluded
    from the summariser input. This guard protects exact echoes from NPC
    secrets, private working memory and unrevealed authored clue prose.
    """
    if not isinstance(world_state, Mapping):
        return ()

    protected: set[str] = set()
    for npc in world_state.get("npcs") or []:
        if isinstance(npc, Mapping) and isinstance(npc.get("secret"), str):
            protected.add(str(npc["secret"]))
    protected.update(_strings(world_state.get("private_memory") or {}))

    known_clues: set[str] = set()
    clues_found = world_state.get("clues_found") or {}
    if isinstance(clues_found, Mapping):
        for entries in clues_found.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, Mapping):
                    clue_id = str(entry.get("catalog_id") or entry.get("id") or "")
                    if clue_id:
                        known_clues.add(clue_id)
    catalog = world_state.get("clue_catalog") or {}
    if isinstance(catalog, Mapping):
        for clue_id, clue in catalog.items():
            if str(clue_id) in known_clues or not isinstance(clue, Mapping):
                continue
            for key in ("text", "description", "secret", "summary"):
                value = clue.get(key)
                if isinstance(value, str):
                    protected.add(value)

    return tuple(
        sorted(
            {
                _normalise(item)
                for item in protected
                if isinstance(item, str) and len(_normalise(item)) >= _MIN_PROTECTED_CHARS
            }
        )
    )


@dataclass(frozen=True)
class SummarySafetyResult:
    allowed: bool
    reason: str | None = None


def validate_summary_shape(summary: str) -> SummarySafetyResult:
    """Accept only a bounded, non-authoritative structured summary shape.

    The summary is continuity aid, never a source of world truth.  Requiring a
    small fixed schema keeps malformed/free-form provider output from becoming
    a durable model instruction and gives later code an auditable surface.
    """
    try:
        value = json.loads(summary)
    except (TypeError, ValueError):
        return SummarySafetyResult(False, "invalid_summary_json")
    if not isinstance(value, dict) or not value or set(value) - _SUMMARY_KEYS:
        return SummarySafetyResult(False, "invalid_summary_shape")
    for key, item in value.items():
        if key in _SUMMARY_LIST_KEYS:
            if (
                not isinstance(item, list)
                or len(item) > _SUMMARY_ITEM_LIMIT
                or not all(isinstance(entry, str) and len(entry) <= _SUMMARY_TEXT_LIMIT for entry in item)
            ):
                return SummarySafetyResult(False, "invalid_summary_shape")
        elif key == "current_scene" and not (
            isinstance(item, str) and len(item) <= _SUMMARY_TEXT_LIMIT
        ):
            return SummarySafetyResult(False, "invalid_summary_shape")
    return SummarySafetyResult(True)


def validate_summary_visibility(
    summary: str,
    world_state: Mapping[str, Any] | None,
) -> SummarySafetyResult:
    """Fail closed if a candidate is malformed or echoes private authored text."""
    shape = validate_summary_shape(summary)
    if not shape.allowed:
        return shape
    normalised_summary = _normalise(summary)
    if not normalised_summary:
        return SummarySafetyResult(False, "empty_summary")
    for fragment in protected_summary_fragments(world_state):
        if fragment in normalised_summary:
            return SummarySafetyResult(False, "private_fragment")
    return SummarySafetyResult(True)
