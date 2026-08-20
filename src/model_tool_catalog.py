"""Request-time construction of the model-visible tool catalog.

``src.tools`` remains the authoritative collection of schema definitions and
handlers.  This module owns the *projection* sent to a model: engine-only
definitions are removed, every object schema is closed, role exclusions are
applied, and ``load_skill`` is bound to the exact frozen ids for one request.
Keeping that projection separate prevents a large handler module from also
becoming the policy implementation.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

_ENGINE_ONLY_TOOL_NAMES = frozenset(
    {
        "cache_scene",
        "read_file",
        "state_get",
        "state_set",
        "state_npcs",
        "state_clues",
        "get_npc_secret",
        "get_private_memory",
        "show_handout",
        "sanity_trigger",
        "sanity_loss",
        "update_private_memory",
    }
)

_STORY_EXCLUDED_MODEL_TOOLS = frozenset(
    {
        "create_character",
        "load_character",
        "combat_status",
        "combat_action",
        "combat_end",
    }
)

_COMBAT_EXCLUDED_MODEL_TOOLS = frozenset(
    {
        "create_character",
        "load_character",
        "combat_start",
        "suggest_check",
        "link_clues",
        "set_psychological_trait",
        "get_npc_secret",
    }
)


SkillAllowlist = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ModelCatalogHelpers:
    """Stable exports derived from the authoritative full tool definitions."""

    tool_schema_by_name: dict[str, dict[str, Any]]
    model_tool_names: frozenset[str]
    model_tools_for: Callable[..., list[dict[str, Any]]]
    tool_catalog_for_names: Callable[..., list[dict[str, Any]]]


def _strict_schema(schema: object) -> object:
    """Clone a provider schema and close every object shape recursively."""
    if not isinstance(schema, dict):
        return schema
    result = copy.deepcopy(schema)
    if result.get("type") == "object":
        result["additionalProperties"] = False
        properties = result.get("properties")
        if isinstance(properties, dict):
            result["properties"] = {
                str(key): _strict_schema(value) for key, value in properties.items()
            }
    items = result.get("items")
    if isinstance(items, dict):
        result["items"] = _strict_schema(items)
    return result


def build_model_catalog_helpers(tools: Iterable[dict[str, Any]]) -> ModelCatalogHelpers:
    """Bind model catalog helpers to the process-owned full ``TOOLS`` list."""
    all_tools = tuple(tools)
    templates = tuple(
        tool
        for tool in all_tools
        if str((tool.get("function") or {}).get("name") or "") not in _ENGINE_ONLY_TOOL_NAMES
    )
    schemas = {
        str(function["name"]): dict(function.get("parameters") or {})
        for tool in all_tools
        if isinstance((function := tool.get("function")), dict) and function.get("name")
    }

    def strict_catalog(
        selected: Iterable[dict[str, Any]], *, skill_allowlist: SkillAllowlist = ()
    ) -> list[dict[str, Any]]:
        allowed_skill_ids = sorted({skill_id for skill_id, _digest in skill_allowlist})
        result: list[dict[str, Any]] = []
        for template in selected:
            function = template.get("function") if isinstance(template, dict) else None
            name = str(function.get("name") or "") if isinstance(function, dict) else ""
            # No frozen id means there is no model capability to load a Skill.
            if name in {"load_skill"} and not allowed_skill_ids:
                continue
            tool = copy.deepcopy(template)
            copied_function = tool.get("function")
            if isinstance(copied_function, dict):
                parameters = _strict_schema(copied_function.get("parameters") or {})
                copied_function["parameters"] = parameters
                if name in {"load_skill"} and isinstance(parameters, dict):
                    properties = parameters.get("properties", {})
                    if isinstance(properties, dict) and isinstance(
                        properties.get("skill_id"), dict
                    ):
                        properties["skill_id"]["enum"] = allowed_skill_ids
            result.append(tool)
        return result

    def model_tools_for(role: str, *, skill_allowlist: SkillAllowlist = ()) -> list[dict[str, Any]]:
        excluded = _COMBAT_EXCLUDED_MODEL_TOOLS if role == "combat" else _STORY_EXCLUDED_MODEL_TOOLS
        return strict_catalog(
            (
                tool
                for tool in templates
                if str((tool.get("function") or {}).get("name") or "") not in excluded
            ),
            skill_allowlist=skill_allowlist,
        )

    def tool_catalog_for_names(
        names: tuple[str, ...] | list[str] | set[str],
        *,
        skill_allowlist: SkillAllowlist = (),
    ) -> list[dict[str, Any]]:
        """Build a closed compatibility/test catalog from model-visible tools."""
        allowed = set(names)
        return strict_catalog(
            (
                tool
                for tool in templates
                if str((tool.get("function") or {}).get("name") or "") in allowed
            ),
            skill_allowlist=skill_allowlist,
        )

    return ModelCatalogHelpers(
        tool_schema_by_name=schemas,
        model_tool_names=frozenset(
            str((tool.get("function") or {}).get("name") or "") for tool in templates
        ),
        model_tools_for=model_tools_for,
        tool_catalog_for_names=tool_catalog_for_names,
    )
