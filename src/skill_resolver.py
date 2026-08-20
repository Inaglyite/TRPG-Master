"""确定性 Skill 激活 resolver（H3：不再赌关键词）。

输入全部是权威状态：WorldState、ActionResolution、本回合已派发的工具名、
ruleset 与模组 capability。关键词只用于漏加载诊断，永远不参与注入决策。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .skill_manifest import SkillCatalog, SkillEntry


def _world_combat_active(world: dict[str, Any]) -> bool:
    combat = world.get("combat_state")
    return bool(isinstance(combat, dict) and combat.get("active"))


def _world_san(world: dict[str, Any]) -> int | None:
    pc = world.get("pc")
    if not isinstance(pc, dict):
        return None
    try:
        return int(pc.get("san"))
    except (TypeError, ValueError):
        return None


def _world_scene_id(world: dict[str, Any]) -> str:
    scene = world.get("current_scene")
    return str(scene.get("id") or "") if isinstance(scene, dict) else ""


def _activation_matches(
    entry: SkillEntry,
    *,
    world: dict[str, Any],
    action_phase: str | None,
    tool_name: str | None,
    ruleset: str,
    module_capabilities: set[str],
) -> bool:
    """谓词键之间 OR；只求值声明了的键。"""
    activation = entry.activation
    if activation.tools and tool_name and tool_name in activation.tools:
        return True
    if (
        activation.combat_active is not None
        and _world_combat_active(world) == activation.combat_active
    ):
        return True
    if activation.san_below is not None:
        san = _world_san(world)
        if san is not None and san < activation.san_below:
            return True
    if activation.phases and action_phase and action_phase in activation.phases:
        return True
    if activation.scenes and _world_scene_id(world) in activation.scenes:
        return True
    if activation.module_capabilities and set(activation.module_capabilities) & module_capabilities:
        return True
    if activation.rulesets and ruleset and ruleset in activation.rulesets:
        return True
    return False


def resolve_activations(
    catalog: SkillCatalog,
    *,
    world: dict[str, Any],
    action_resolution: Any = None,
    tool_name: str | None = None,
    ruleset: str = "",
    module_capabilities: Iterable[str] = (),
    available_ids: set[str] | None = None,
) -> list[SkillEntry]:
    """返回本回合必须加载的 deterministic Skill，按 catalog 顺序。

    ``available_ids`` 为世界 pin 的 skill id 集；给定后未 pin 的条目不参与
    激活（活跃世界的目录以 pin 为准）。
    """
    phase = None
    if action_resolution is not None:
        raw_phase = getattr(action_resolution, "phase", None)
        phase = str(getattr(raw_phase, "value", raw_phase) or "") or None
    capabilities = {str(cap) for cap in module_capabilities}
    selected = []
    for entry in catalog.skills:
        if entry.residency != "deterministic":
            continue
        if available_ids is not None and entry.id not in available_ids:
            continue
        if _activation_matches(
            entry,
            world=world,
            action_phase=phase,
            tool_name=tool_name,
            ruleset=ruleset,
            module_capabilities=capabilities,
        ):
            selected.append(entry)
    return selected


def keyword_misses(
    catalog: SkillCatalog,
    content: str,
    activated_ids: set[str],
) -> list[SkillEntry]:
    """diagnostic_keywords 命中但未被确定性激活的条目——只诊断，不注入。"""
    text = str(content or "")
    if not text:
        return []
    missed = []
    for entry in catalog.skills:
        if entry.id in activated_ids or not entry.diagnostic_keywords:
            continue
        if any(keyword and keyword in text for keyword in entry.diagnostic_keywords):
            missed.append(entry)
    return missed
