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
    "examine": re.compile(r"(?:检查|检视|查看|察看|观察|研究|端详|掀开|揭开)"),
    "search": re.compile(r"(?:搜查|搜索|搜寻|翻找|寻找|查找|调查)"),
    "read": re.compile(r"(?:阅读|研读|翻阅|读|查看|检查)"),
    "take": re.compile(r"(?:拿起|拾取|捡起|取走|带走|收起|拿走)"),
    "talk": re.compile(r"(?:询问|盘问|交谈|对话|问|套话|打听)"),
    "enter": re.compile(r"(?:进入|走进|来到|前往|抵达|返回|回到)"),
    "use": re.compile(r"(?:使用|启动|打开|操作|尝试|用)"),
}
_NEGATED = re.compile(
    r"(?:不|别|不要|并未|没有|拒绝|暂时不).{0,8}"
    r"(?:检查|检视|查看|观察|搜查|搜索|阅读|拿起|询问|进入|使用|打开)"
)
_DISCUSSED = re.compile(
    r"(?:想知道|请问|询问|追问|请教|(?:我)?问).{0,40}"
    r"(?:检查|查看|搜查|阅读|拿起|进入|使用|打开)"
    r"|(?:让|要求|命令|叫).{0,24}"
    r"(?:检查|查看|搜查|阅读|拿起|进入|使用|打开)"
)


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


def _rule_matches(text: str, rule: dict) -> bool:
    intent = str(rule.get("intent") or "")
    pattern = _INTENT_PATTERNS.get(intent)
    if pattern is None or pattern.search(text) is None:
        return False
    targets = rule.get("targets", [])
    if not isinstance(targets, list):
        return False
    folded = text.casefold()
    return any(
        str(target).strip().casefold() in folded
        for target in targets
        if str(target).strip()
    )


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
            if isinstance(rule, dict) and _rule_matches(text, rule):
                matches.append(DiscoveryMatch(str(clue_id), clue, rule))
                break
    return matches


def preferred_check_skill(matches: list[DiscoveryMatch], world: dict) -> str | None:
    """Return the single module-declared skill when the PC can roll it."""
    skills = {
        str(match.rule.get("skill"))
        for match in matches
        if match.rule.get("skill")
    }
    if len(skills) != 1:
        return None
    skill = skills.pop()
    pc_skills = (world.get("pc") or {}).get("skills", {})
    return skill if isinstance(pc_skills, dict) and skill in pc_skills else None
