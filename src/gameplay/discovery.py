"""Deterministic matching for module-authored clue discovery rules."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryMatch:
    clue_id: str
    clue: dict
    rule: dict


_INTENT_PATTERNS = {
    "examine": re.compile(r"(?:检查|检视|查看|察看|观察|研究|端详|掀开|揭开|看(?:看|一眼)?)"),
    "search": re.compile(r"(?:搜查|搜索|搜寻|翻找|寻找|查找|调查)"),
    "read": re.compile(r"(?:阅读|研读|翻阅|读|查看|检查)"),
    "take": re.compile(r"(?:拿起|拾取|捡起|取走|带走|收起|拿走)"),
    "talk": re.compile(r"(?:询问|盘问|交谈|对话|问|套话|打听)"),
    "enter": re.compile(r"(?:进入|走进|来到|前往|抵达|返回|回到)"),
    "use": re.compile(r"(?:使用|启动|打开|操作|尝试|用)"),
}
_NEGATED = re.compile(
    r"(?:不|别|不要|并未|没有|拒绝|暂时不)[^，。；、]{0,8}"
    r"(?:检查|检视|查看|观察|看(?:看|一眼)?|搜查|搜索|阅读|拿起|询问|进入|使用|打开)"
)
_DISCUSSED = re.compile(
    r"(?:想知道|请问|询问|追问|请教|(?:我)?问).{0,40}"
    r"(?:检查|查看|看(?:看|一眼)?|搜查|阅读|拿起|进入|使用|打开)"
    r"|(?:让|要求|命令|叫).{0,24}"
    r"(?:检查|查看|看(?:看|一眼)?|搜查|阅读|拿起|进入|使用|打开)"
)
_REMOTE_TARGET_MIN_CHARS = 4


def _known_clue_ids(world: dict) -> set[str]:
    known: set[str] = set()
    groups = world.get("clues_found", {})
    if not isinstance(groups, dict):
        return known
    for clues in groups.values():
        if not isinstance(clues, list):
            continue
        for clue in clues:
            if not isinstance(clue, dict):
                continue
            clue_id = clue.get("catalog_id") or clue.get("id")
            if clue_id:
                known.add(str(clue_id))
    return known


def _fold_target_text(text: object) -> str:
    """目标匹配忽略结构助词：玩家说「莱特的遗体」应命中声明的「莱特遗体」。"""
    return (
        str(text)
        .strip()
        .casefold()
        .replace("的", "")
        # 旧版《猩红文档》只声明了「遗体」，但玩家自然会说「尸体」。
        # 这是同一实体的词形归一，不是针对某个场景猜目的地。
        .replace("尸身", "遗体")
        .replace("尸体", "遗体")
    )


def _rule_matches(text: str, rule: dict, *, min_target_chars: int = 1) -> bool:
    intent = str(rule.get("intent") or "")
    pattern = _INTENT_PATTERNS.get(intent)
    if pattern is None or pattern.search(text) is None:
        return False
    targets = rule.get("targets", [])
    if not isinstance(targets, list):
        return False
    folded = _fold_target_text(text)
    return any(
        len(folded_target) >= min_target_chars and folded_target in folded
        for target in targets
        if (folded_target := _fold_target_text(target))
    )


def _references_carried_item(text: str, world: dict) -> bool:
    """A concrete item already in the investigator's inventory cannot be remote."""
    pc = world.get("pc", {})
    inventory = pc.get("inventory", []) if isinstance(pc, dict) else []
    if not isinstance(inventory, list):
        return False
    folded = _fold_target_text(text)
    return any(
        len(item_name) >= _REMOTE_TARGET_MIN_CHARS and item_name in folded
        for item in inventory
        if (item_name := _fold_target_text(item))
    )


def _rule_flags_met(rule: dict, world: dict) -> bool:
    """rule 声明的 requires_flags 须全部 truthy 才允许匹配。

    通用推进阀门：模组可要求「先搜过店面」才允许「发现活板门」，防止
    玩家一句「我找女巫审判文档」在店门口就直接命中终局线索。
    """
    required = rule.get("requires_flags")
    if not isinstance(required, list) or not required:
        return True
    flags = world.get("flags", {})
    if not isinstance(flags, dict):
        return False
    return all(bool(flags.get(str(flag))) for flag in required)


def match_discovery_rules(content: str, world: dict) -> list[DiscoveryMatch]:
    """Match undiscovered clues in the current scene against one player action."""
    text = " ".join(str(content).strip().split())
    if not text or _NEGATED.search(text) or _DISCUSSED.search(text):
        return []

    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    catalog = world.get("clue_catalog", {})
    if not scene_id or not isinstance(catalog, dict):
        return []
    known = _known_clue_ids(world)
    matches: list[DiscoveryMatch] = []
    for clue_id, clue in catalog.items():
        if str(clue_id) in known or not isinstance(clue, dict):
            continue
        related_scenes = clue.get("related_scenes", [])
        if clue.get("source") != scene_id and scene_id not in related_scenes:
            continue
        rules = clue.get("discovery_rules", [])
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if (
                isinstance(rule, dict)
                and _rule_flags_met(rule, world)
                and _rule_matches(text, rule)
            ):
                matches.append(DiscoveryMatch(str(clue_id), clue, rule))
                break
    return matches


def infer_discovery_target_destination(content: str, world: dict) -> str | None:
    """Route an explicit, unique undiscovered clue target to its authored scene.

    This is deliberately *not* discovery.  A player saying ``我想看看莱特教授的
    尸体`` has selected a concrete, module-authored physical target, but has not
    yet inspected it.  When that target belongs to exactly one reachable scene,
    the current action becomes an arrival; the next player action may resolve
    the actual clue.  Ambiguity and unknown targets fail closed.
    """
    text = " ".join(str(content).strip().split())
    if not text or _NEGATED.search(text) or _DISCUSSED.search(text):
        return None

    origin_scene_id = str((world.get("current_scene") or {}).get("id") or "")
    catalog = world.get("clue_catalog", {})
    scenes = world.get("scene_catalog", {})
    if not origin_scene_id or not isinstance(catalog, dict) or not isinstance(scenes, dict):
        return None
    if _references_carried_item(text, world):
        return None

    known = _known_clue_ids(world)
    destinations: set[str] = set()
    for clue_id, clue in catalog.items():
        if str(clue_id) in known or not isinstance(clue, dict):
            continue
        rules = clue.get("discovery_rules", [])
        if not isinstance(rules, list) or not any(
            isinstance(rule, dict)
            and _rule_flags_met(rule, world)
            # Discovery rules may contain convenient short aliases such as
            # "副本" or "墨迹".  They are safe inside the current scene, but
            # too ambiguous to manufacture a cross-scene movement edge.
            and _rule_matches(
                text,
                rule,
                min_target_chars=_REMOTE_TARGET_MIN_CHARS,
            )
            for rule in rules
        ):
            continue
        # ``source`` is the primary physical location.  ``related_scenes`` can
        # contain narrative associations, so only a single candidate is safe to
        # promote into a movement edge.
        candidate_ids = {
            str(scene_id)
            for scene_id in [clue.get("source"), *(clue.get("related_scenes") or [])]
            if isinstance(scene_id, str) and scene_id in scenes and scene_id != origin_scene_id
        }
        if len(candidate_ids) == 1:
            destinations.update(candidate_ids)

    return next(iter(destinations)) if len(destinations) == 1 else None


def preferred_check_skill(matches: list[DiscoveryMatch], world: dict) -> str | None:
    """Return the single module-declared skill when the PC can roll it."""
    skills = {str(match.rule.get("skill")) for match in matches if match.rule.get("skill")}
    if len(skills) != 1:
        return None
    skill = skills.pop()
    pc_skills = (world.get("pc") or {}).get("skills", {})
    return skill if isinstance(pc_skills, dict) and skill in pc_skills else None


def preferred_luck_difficulty(matches: list[DiscoveryMatch]) -> str | None:
    """Return one declared luck difficulty, or None for non-luck discoveries."""
    difficulties = {
        str(match.rule.get("difficulty") or "regular")
        for match in matches
        if match.rule.get("check_type") == "luck"
    }
    return difficulties.pop() if len(difficulties) == 1 else None
