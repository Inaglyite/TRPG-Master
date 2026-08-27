"""Turn-end transaction audit for narrative/state consistency."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from src.ai.model.llm_concurrency import llm_call_slot
from src.app.config import JUDGEMENT_MODEL
from src.app.logger import error as log_error
from src.app.logger import game_event as log_game
from src.app.logger import model_call as log_model_call
from src.gameplay.action_checks import _scene_aliases
from src.gameplay.discovery import _known_clue_ids

COMMIT_TURN_TOOL = {
    "type": "function",
    "function": {
        "name": "commit_turn",
        "description": "Commit only state changes already completed in the visible narrative.",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "items_add": {"type": "array", "items": {"type": "string"}},
                "items_remove": {"type": "array", "items": {"type": "string"}},
                "clues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["investigation", "event", "task", "npc"],
                            },
                            "clue_id": {"type": "string"},
                            "asset_id": {"type": "string"},
                        },
                        "required": ["text", "category"],
                    },
                },
                "npc_reveals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "npc_id": {"type": "string"},
                            "tier": {"type": "integer", "minimum": 1, "maximum": 3},
                            "text": {"type": "string"},
                        },
                        "required": ["npc_id", "tier", "text"],
                    },
                },
                "flags_set": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value_json": {"type": "string"},
                        },
                        "required": ["key", "value_json"],
                    },
                },
                "clocks_set": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value_json": {"type": "string"},
                        },
                        "required": ["key", "value_json"],
                    },
                },
                "sanity_events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {
                                "type": "string",
                                "enum": ["trivial", "minor", "moderate", "major", "catastrophic"],
                            },
                            "description": {"type": "string"},
                        },
                        "required": ["severity", "description"],
                    },
                },
                "ending_id": {"type": "string"},
                "no_changes_reason": {"type": "string"},
            },
            "required": [
                "scene_id",
                "items_add",
                "items_remove",
                "clues",
                "npc_reveals",
                "flags_set",
                "clocks_set",
                "sanity_events",
                "ending_id",
            ],
        },
    },
}

_AUTHORITATIVE_CHANGE_TOOLS = {
    "state_set",
    "state_add_clue",
    "state_add_item",
    "state_remove_item",
    "npc_reveal",
    "use_item",
    "sanity_event",
    "sanity_loss",
    "sanity_restore",
    "apply_damage",
    "apply_heal",
    "combat_start",
    "combat_action",
    "combat_end",
    "set_psychological_trait",
    "end_game",
}

_STATEFUL_NARRATIVE_PATTERN = re.compile(
    r"(?:"
    r"你(?:发现|找到|取得|获得|捡起|收下|交出|失去|消耗)"
    r"|(?:承认|坦白|透露|供认|证实|交给你|递给你)"
    r"|(?:看见|看到|目睹|检视|检查).{0,24}(?:尸体|遗体|怪物|非人|超自然)"
    # 验尸/揭示类完成态：覆盖「揭开白布」「遗体显露」等不出现「发现/检查」的写法
    r"|(?:揭开|掀开|揭起|拉开|移开).{0,12}(?:白布|遮盖|覆盖|罩布|裹尸布)"
    r"|(?:遗体|尸体|证物|遗物).{0,8}(?:露出|显露|呈现|暴露|展现)"
    r"|你(?:受伤|中弹|流血|昏迷|死亡|理智崩溃)"
    r"|(?:案件|调查|故事).{0,12}(?:结束|告终)"
    r")"
)


# 人名中可剥离的称谓后缀：「惠特克罗夫特医生」在叙事里常被简写为「惠特克罗夫特」。
_NPC_TITLE_SUFFIXES = (
    "医生",
    "教授",
    "主任",
    "警官",
    "警长",
    "先生",
    "女士",
    "小姐",
    "船长",
    "博士",
    "护士",
    "神父",
    "牧师",
    "老师",
)


def _npc_name_aliases(name: str) -> set[str]:
    aliases = {name}
    parts = [part for part in name.replace("・", "·").split("·") if len(part) >= 2]
    aliases.update(parts)
    for part in parts:
        for suffix in _NPC_TITLE_SUFFIXES:
            stripped = part[: -len(suffix)] if part.endswith(suffix) else ""
            if len(stripped) >= 2:
                aliases.add(stripped)
    return {alias for alias in aliases if alias}


def _module_keyword_index(world: dict) -> dict[str, str]:
    """收割模组自己声明的关键词：场景别名、未揭示 NPC 名、未发现线索的发现目标。

    等价于「导入模组时提取关键词表」，但运行时从 world state 现算——catalog
    会随 refresh 同步，老存档自动受益，不需要迁移或额外存储。全程数据驱动，
    不含领域词，对任意题材（僵尸、科幻）的模组同样有效。
    """
    index: dict[str, str] = {}
    current_scene_id = str((world.get("current_scene") or {}).get("id") or "")
    scenes = world.get("scene_catalog", {})
    if isinstance(scenes, dict):
        for scene_id, scene in scenes.items():
            if str(scene_id) == current_scene_id or not isinstance(scene, dict):
                continue
            for alias in _scene_aliases(scene):
                index.setdefault(alias, "scene")
    for npc in world.get("npcs", []):
        if not isinstance(npc, dict):
            continue
        revealed = npc.get("revealed")
        level = 0
        if isinstance(revealed, dict):
            try:
                level = int(revealed.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
        if level > 0:
            continue
        name = str(npc.get("name") or "").strip()
        for alias in _npc_name_aliases(name):
            if len(alias) >= 2:
                index.setdefault(alias, "npc")
    known_clues = _known_clue_ids(world)
    catalog = world.get("clue_catalog", {})
    if isinstance(catalog, dict):
        for clue_id, clue in catalog.items():
            if str(clue_id) in known_clues or not isinstance(clue, dict):
                continue
            rules = clue.get("discovery_rules")
            if not isinstance(rules, list):
                continue
            for rule in rules:
                targets = rule.get("targets") if isinstance(rule, dict) else None
                if not isinstance(targets, list):
                    continue
                for target in targets:
                    keyword = str(target).strip()
                    if len(keyword) >= 2:
                        index.setdefault(keyword, "clue_target")
    return index


def _entity_mention_signal(world: dict, body: str) -> bool:
    """叙事提及了模组关键词表中、尚未进入权威状态的实体。"""
    if not body or not isinstance(world, dict):
        return False
    return any(keyword in body for keyword in _module_keyword_index(world))


_CLOCK_TERM_SPLIT = re.compile(r"[、，。；：！？/\s]+")
_CLOCK_TERM_CAP_PER_CLOCK = 16
_CLOCK_TERM_CAP_TOTAL = 64


def _clock_keywords(world: dict) -> list[str]:
    """从 case_clock_definitions 收割时钟征兆/推进词段（数据驱动，无领域词）。

    守秘人被要求按 next_level 原文叙述征兆，因此 levels 词段是可靠信号；
    advance_when 词段（如「枪战」「非法闯入」）覆盖玩家行动侧。词段切分
    只按标点与空白，对任意题材的模组同样有效。每个时钟单独配额——真机
    事故：全局共享配额被第一个时钟的 levels 占满，human_pressure 的
    「枪战」等词段从未进入信号表，审计整局未运行。
    """
    definitions = world.get("case_clock_definitions")
    if not isinstance(definitions, dict):
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for definition in definitions.values():
        if not isinstance(definition, dict):
            continue
        texts: list[str] = []
        # advance_when 优先：行动侧触发词（枪战/闯入/拖延）是时钟记账的主信号；
        # levels 征兆词数量多，先收割会把配额吃光让行动词永远进不了表。
        advance_when = definition.get("advance_when")
        if isinstance(advance_when, list):
            texts.extend(str(text) for text in advance_when)
        levels = definition.get("levels")
        if isinstance(levels, dict):
            texts.extend(str(text) for text in levels.values())
        per_clock = 0
        for text in texts:
            for term in _CLOCK_TERM_SPLIT.split(text):
                term = term.strip()
                if len(term) < 2 or term in seen:
                    continue
                seen.add(term)
                terms.append(term)
                per_clock += 1
                if len(terms) >= _CLOCK_TERM_CAP_TOTAL:
                    return terms
                if per_clock >= _CLOCK_TERM_CAP_PER_CLOCK:
                    break
            if per_clock >= _CLOCK_TERM_CAP_PER_CLOCK:
                break
    return terms


def _clock_signal(world: dict, body: str) -> bool:
    """叙事出现了案件时钟的征兆或推进情形——审计必须运行以记账。"""
    if not body or not isinstance(world, dict):
        return False
    return any(term in body for term in _clock_keywords(world))


def narrative_body(text: str) -> str:
    """Remove the final option menu before auditing completed events."""
    markers = (
        r"\n\s*(?:\*{1,2})?你可以(?:选择)?(?:——|--|：|:)(?:\*{1,2})?",
        r"\n\s*(?:\*{1,2})?(?:请选择|可选行动|接下来你可以)"
        r"(?:：|:|——|--)(?:\*{1,2})?",
    )
    end = len(text)
    for marker in markers:
        match = re.search(marker, text)
        if match:
            end = min(end, match.start())
    return text[:end].strip()


def _clip(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _compact_world(state: dict) -> dict:
    clues = []
    for category, entries in state.get("clues_found", {}).items():
        if not isinstance(entries, list):
            continue
        for clue in entries[-20:]:
            if isinstance(clue, dict):
                clues.append(
                    {
                        "id": clue.get("catalog_id") or clue.get("id"),
                        "category": category,
                        "text": _clip(clue.get("text"), 240),
                    }
                )

    clue_catalog = {}
    for clue_id, clue in state.get("clue_catalog", {}).items():
        if not isinstance(clue, dict):
            continue
        asset = clue.get("asset") or {}
        clue_catalog[clue_id] = {
            "text": _clip(clue.get("text"), 280),
            "category": clue.get("category", "investigation"),
            "asset_id": asset.get("id", "") if isinstance(asset, dict) else "",
            "discovery_notes": _clip(clue.get("discovery_notes"), 240),
        }

    scenes = {
        scene_id: {
            "name": scene.get("name", scene_id),
            "npcs_present": scene.get("npcs_present", []),
        }
        for scene_id, scene in state.get("scene_catalog", {}).items()
        if isinstance(scene, dict)
    }
    npcs = []
    for npc in state.get("npcs", []):
        if not isinstance(npc, dict):
            continue
        revealed = npc.get("revealed") or {}
        npcs.append(
            {
                "id": npc.get("id"),
                "name": npc.get("name"),
                "location": npc.get("current_location"),
                "revealed_level": revealed.get("level", 0),
                "revealed_entries": [
                    _clip(entry.get("text"), 180)
                    for entry in revealed.get("entries", [])[-8:]
                    if isinstance(entry, dict)
                ],
            }
        )

    pc = state.get("pc", {})
    return {
        "module_meta": state.get("module_meta", {}),
        "current_scene": state.get("current_scene", {}),
        "scene_catalog": scenes,
        "pc": {
            "name": pc.get("name"),
            "hp": pc.get("hp"),
            "san": pc.get("san"),
            "inventory": pc.get("inventory", []),
            "conditions": pc.get("conditions", []),
        },
        "flags": state.get("flags", {}),
        "case_clocks": state.get("case_clocks", {}),
        "case_clock_definitions": state.get("case_clock_definitions", {}),
        "known_clues": clues[-30:],
        "clue_catalog": clue_catalog,
        "npcs": npcs,
        "endings": state.get("endings", []),
        "module_rules": state.get("module_rules", {}),
        "game_over": state.get("game_over"),
    }


def _tool_event_summary(events: list[dict]) -> list[dict]:
    return [
        {
            "name": event.get("name", ""),
            "args": event.get("args", {}),
            "result": _clip(event.get("output"), 400),
        }
        for event in events
    ]


def _extract_commit(response: Any) -> dict | None:
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return None

    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        function = getattr(tool_calls[0], "function", None)
        raw = getattr(function, "arguments", "") if function else ""
    else:
        raw = getattr(message, "content", "") or ""
    if not raw:
        return None
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if match:
            raw = match.group(1)
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _name_mentioned(name: str, text: str) -> bool:
    if not name:
        return False
    aliases = {name}
    aliases.update(part for part in re.split(r"[·・\s]+", name) if len(part) >= 2)
    return any(alias in text for alias in aliases)


def _scene_transition_position(name: str, text: str) -> int:
    """Return an explicit arrival/location assertion, not a passing mention."""
    if not name:
        return -1
    positions = []
    transition = re.compile(
        r"(?:来到|抵达|进入|走进|踏入|返回|回到|赶到|走到|前往|身处|置身于|站在|坐在)"
        r"[^。！？\n]{0,18}$"
    )
    for match in re.finditer(re.escape(name), text):
        prefix = text[max(0, match.start() - 28) : match.start()]
        if transition.search(prefix):
            positions.append(match.start())
    return max(positions, default=-1)


def reconcile_narrative_entities(engine: Any, narrative: str) -> list[str]:
    """Deterministically sync scene and first-encounter NPCs from visible prose."""
    body = narrative_body(narrative)
    if not body:
        return []
    state = engine.context.world_store.load()
    scenes = state.get("scene_catalog", {})
    candidates = []
    if isinstance(scenes, dict):
        for scene_id, scene in scenes.items():
            if not isinstance(scene, dict):
                continue
            position = _scene_transition_position(str(scene.get("name") or ""), body)
            if position >= 0:
                candidates.append(
                    (
                        position,
                        len(str(scene.get("name") or "")),
                        str(scene_id),
                    )
                )

    applied: list[str] = []
    if candidates:
        scene_id = max(candidates)[2]
        current_scene = state.get("current_scene", {})
        target_scene = scenes[scene_id]
        if current_scene.get("id") != scene_id or current_scene.get("name") != target_scene.get(
            "name"
        ):
            engine._execute_tool(
                "state_set",
                {
                    "path": "current_scene.id",
                    "value": json.dumps(scene_id, ensure_ascii=False),
                },
            )
            applied.append(f"scene:{scene_id}")
            state = engine.context.world_store.load()

    current_scene = state.get("current_scene", {})
    present = set(current_scene.get("npcs_present", []))
    for npc in state.get("npcs", []):
        if not isinstance(npc, dict) or npc.get("id") not in present:
            continue
        revealed = npc.get("revealed") or {}
        if revealed.get("level", 0) > 0:
            continue
        name = str(npc.get("name") or "")
        if not _name_mentioned(name, body):
            continue
        tags = "、".join(str(tag) for tag in npc.get("visible_tags", [])[:6])
        entry = f"{name}：{tags}" if tags else f"调查员已见到{name}。"
        engine._execute_tool(
            "npc_reveal",
            {
                "npc_id": npc["id"],
                "tier": 1,
                "entry_text": entry,
            },
        )
        applied.append(f"npc:{npc['id']}")
    if applied:
        log_game("确定性叙事同步 | " + ", ".join(applied))
    return applied


def turn_needs_model_audit(
    executed_tools: list[dict] | None,
    *,
    player_action: str = "",
    narrative: str | None = None,
    has_authoritative_mutation: bool = False,
    world: dict | None = None,
) -> bool:
    """Audit only stateful prose that reached no authoritative transaction."""
    if has_authoritative_mutation:
        return False
    for event in executed_tools or []:
        if event.get("name") not in _AUTHORITATIVE_CHANGE_TOOLS:
            continue
        output = str(event.get("output") or "")
        if not output.startswith(("[错误]", "[异常]", "[超时]")):
            return False
    # Keep the legacy conservative behavior for callers that have no prose.
    if narrative is None:
        return True
    del player_action  # Intent alone must not be mistaken for a completed event.
    body = narrative_body(narrative)
    if _STATEFUL_NARRATIVE_PATTERN.search(body):
        return True
    # 数据驱动的通用信号：叙事提及未揭示 NPC / 非当前场景。关键词网覆盖不了
    # 所有模组（没有遗体/白布的模组），实体提及才是主阀门。
    if world is not None and _entity_mention_signal(world, body):
        return True
    # 案件时钟阀门：征兆/推进情形出现时必须审计，否则时钟永远不记账。
    if world is not None and _clock_signal(world, body):
        return True
    return False


def _parse_json_scalar(raw: str) -> Any:
    value = json.loads(raw)
    if isinstance(value, (dict, list)):
        raise ValueError("flags only accept scalar values")
    return value


def engine_turn_needs_model_audit(
    engine: Any,
    executed_tools: list[dict] | None,
    *,
    player_action: str = "",
    narrative: str | None = None,
) -> bool:
    """Engine-facing gate: enrich the audit decision with the live world state."""
    try:
        world = engine.context.world_store.load()
    except Exception:
        world = None
    if turn_needs_model_audit(
        executed_tools,
        player_action=player_action,
        narrative=narrative,
        has_authoritative_mutation=engine._turn_mutations.has_authoritative_mutation,
        world=world,
    ):
        return True
    # 末日钟周期审计：时钟是随时间发酵的机制（拖延本身推进显形），不能只靠
    # 关键词信号——真机里 1/3 回合有征兆叙事但一次都没命中信号，时钟全程为 0。
    # 声明了时钟表的模组每 3 回合强制对账一次；审计契约保守，无事可记就空提交。
    if world is not None and _has_clock_definitions(world):
        round_count = int(getattr(engine, "_round_count", 0) or 0)
        if round_count % 3 == 0:
            return True
    return False


def _has_clock_definitions(world: dict) -> bool:
    definitions = world.get("case_clock_definitions")
    return isinstance(definitions, dict) and bool(definitions)


def apply_turn_commit(
    engine: Any,
    commit: dict,
    *,
    player_action: str,
    narrative: str,
    executed_tools: list[dict] | None = None,
) -> dict:
    """Validate and apply a model-produced commit through authoritative tools."""
    executed_tools = executed_tools or []
    already_executed = {event.get("name") for event in executed_tools}
    body = narrative_body(narrative)
    combined_text = f"{player_action}\n{body}"
    state = engine.context.world_store.load()
    applied: list[str] = []
    skipped: list[str] = []

    scene_id = str(commit.get("scene_id") or "").strip()
    current_scene_id = str(state.get("current_scene", {}).get("id") or "")
    scenes = state.get("scene_catalog", {})
    if scene_id:
        scene = scenes.get(scene_id) if isinstance(scenes, dict) else None
        current_scene = state.get("current_scene", {})
        needs_sync = (
            scene_id != current_scene_id
            or not isinstance(current_scene, dict)
            or current_scene.get("name") != (scene or {}).get("name")
        )
        if (
            needs_sync
            and isinstance(scene, dict)
            and _name_mentioned(str(scene.get("name", "")), combined_text)
        ):
            scene_value = {key: value for key, value in scene.items() if key != "document"}
            engine._execute_tool(
                "state_set",
                {
                    "path": "current_scene",
                    "value": json.dumps(scene_value, ensure_ascii=False),
                },
            )
            applied.append(f"scene:{scene_id}")
        elif needs_sync:
            skipped.append(f"scene:{scene_id}")

    inventory = state.get("pc", {}).get("inventory", [])
    inventory_text = {str(item) for item in inventory}
    for item in commit.get("items_add", [])[:12]:
        item = _clip(item, 160).strip()
        if item and item not in inventory_text:
            engine._execute_tool("state_add_item", {"item": item})
            inventory_text.add(item)
            applied.append(f"item+:{item}")
    for item in commit.get("items_remove", [])[:12]:
        item = _clip(item, 160).strip()
        if item and item in inventory_text:
            engine._execute_tool("state_remove_item", {"item": item})
            inventory_text.remove(item)
            applied.append(f"item-:{item}")

    clue_catalog = state.get("clue_catalog", {})
    categories = {"investigation", "event", "task", "npc"}
    for clue in commit.get("clues", [])[:12]:
        if not isinstance(clue, dict):
            continue
        clue_id = str(clue.get("clue_id") or "").strip()
        if clue_id and clue_id not in clue_catalog:
            skipped.append(f"clue:{clue_id}")
            continue
        text = _clip(clue.get("text"), 500).strip()
        category = str(clue.get("category") or "investigation")
        if not text or category not in categories:
            continue
        args = {"text": text, "category": category}
        asset_id = str(clue.get("asset_id") or "").strip()
        if clue_id:
            args["clue_id"] = clue_id
        elif asset_id:
            args["asset_id"] = asset_id
        execute_model_tool = getattr(engine, "_execute_model_tool", None)
        if execute_model_tool:
            output = execute_model_tool(
                "state_add_clue",
                args,
                player_action=player_action,
            )
        else:
            output = engine._execute_tool("state_add_clue", args)
        try:
            clue_result = json.loads(output)
        except (TypeError, json.JSONDecodeError, AttributeError):
            clue_result = {}
        if clue_result.get("ok") is False:
            skipped.append(f"clue:{clue_id or text[:24]}")
            continue
        if not clue_result.get("duplicate"):
            applied.append(f"clue:{clue_id or text[:24]}")

    npcs = {
        str(npc.get("id")): npc
        for npc in state.get("npcs", [])
        if isinstance(npc, dict) and npc.get("id")
    }
    for reveal in commit.get("npc_reveals", [])[:12]:
        if not isinstance(reveal, dict):
            continue
        npc_id = str(reveal.get("npc_id") or "").strip()
        npc = npcs.get(npc_id)
        entry = _clip(reveal.get("text"), 400).strip()
        tier = int(reveal.get("tier") or 1)
        if npc and entry and 1 <= tier <= 3 and _name_mentioned(str(npc.get("name", "")), body):
            output = engine._execute_tool(
                "npc_reveal",
                {
                    "npc_id": npc_id,
                    "tier": tier,
                    "entry_text": entry,
                },
            )
            try:
                duplicate = bool(json.loads(output).get("duplicate"))
            except (TypeError, json.JSONDecodeError, AttributeError):
                duplicate = False
            if not duplicate:
                applied.append(f"npc:{npc_id}:{tier}")
        else:
            skipped.append(f"npc:{npc_id}")

    flags = state.get("flags", {})
    for change in commit.get("flags_set", [])[:16]:
        if not isinstance(change, dict):
            continue
        key = str(change.get("key") or "").strip()
        if key not in flags:
            skipped.append(f"flag:{key}")
            continue
        try:
            value = _parse_json_scalar(str(change.get("value_json", "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            skipped.append(f"flag:{key}")
            continue
        if flags.get(key) != value:
            args = {
                "path": f"flags.{key}",
                "value": json.dumps(value, ensure_ascii=False),
            }
            execute_model_tool = getattr(engine, "_execute_model_tool", None)
            output = (
                execute_model_tool("state_set", args, player_action=player_action)
                if execute_model_tool
                else engine._execute_tool("state_set", args)
            )
            try:
                flag_result = json.loads(output)
            except (TypeError, json.JSONDecodeError, AttributeError):
                flag_result = {}
            if flag_result.get("ok") is False:
                skipped.append(f"flag:{key}")
            else:
                applied.append(f"flag:{key}={value!r}")

    # 案件时钟：末日钟只增不减，键必须已在 case_clocks 中声明。
    # 叙事模型无工具，时钟记账只能由本审计完成——keeper 叙述了征兆，
    # 审计负责把它记成数值，否则模组压力机制永远不启动。
    clocks = state.get("case_clocks", {})
    clock_definitions = state.get("case_clock_definitions", {})
    for change in commit.get("clocks_set", [])[:8]:
        if not isinstance(change, dict):
            continue
        key = str(change.get("key") or "").strip()
        if key not in clocks:
            skipped.append(f"clock:{key}")
            continue
        try:
            value = _parse_json_scalar(str(change.get("value_json", "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            skipped.append(f"clock:{key}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            skipped.append(f"clock:{key}")
            continue
        current = clocks.get(key)
        if not isinstance(current, (int, float)) or value <= current:
            skipped.append(f"clock:{key}")
            continue
        # 模组声明了 max 时越界值收拢到 max（审计保守，不允许跳级爆表）。
        definition = clock_definitions.get(key)
        max_value = definition.get("max") if isinstance(definition, dict) else None
        if isinstance(max_value, int) and not isinstance(max_value, bool):
            value = min(value, max_value)
        args = {
            "path": f"case_clocks.{key}",
            "value": json.dumps(int(value), ensure_ascii=False),
        }
        execute_model_tool = getattr(engine, "_execute_model_tool", None)
        output = (
            execute_model_tool("state_set", args, player_action=player_action)
            if execute_model_tool
            else engine._execute_tool("state_set", args)
        )
        try:
            clock_result = json.loads(output)
        except (TypeError, json.JSONDecodeError, AttributeError):
            clock_result = {}
        if clock_result.get("ok") is False:
            skipped.append(f"clock:{key}")
        else:
            applied.append(f"clock:{key}={int(value)}")

    if not ({"sanity_event", "sanity_trigger", "sanity_loss"} & already_executed):
        events = commit.get("sanity_events", [])
        if isinstance(events, list) and events:
            event = events[0]
            severity = str(event.get("severity") or "").strip()
            description = _clip(event.get("description"), 500).strip()
            allowed = {"trivial", "minor", "moderate", "major", "catastrophic"}
            if severity in allowed and description:
                args = {
                    "description": description,
                    "severity": severity,
                }
                execute_model_tool = getattr(engine, "_execute_model_tool", None)
                output = (
                    execute_model_tool("sanity_event", args, player_action=player_action)
                    if execute_model_tool
                    else engine._execute_tool("sanity_event", args)
                )
                blocked = False
                try:
                    result = json.loads(output)
                    if result.get("ok") is False:
                        skipped.append(f"sanity:{severity}")
                        blocked = True
                    else:
                        roll = int(result["san_roll"])
                        success = bool(result["san_check_success"])
                        loss = int(result["actual_loss"])
                        engine.cb.on_dice(
                            f"理智检定 {roll}，{'成功' if success else '失败'}，SAN -{loss}",
                            {
                                "spec": "d100",
                                "sides": 100,
                                "count": 1,
                                "rolls": [roll],
                                "total": roll,
                                "sanity": True,
                            },
                        )
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    pass
                if not blocked:
                    applied.append(f"sanity:{severity}")

    ending_id = str(commit.get("ending_id") or "").strip()
    if ending_id and not state.get("game_over"):
        endings = {
            str(ending.get("id")): ending
            for ending in state.get("endings", [])
            if isinstance(ending, dict) and ending.get("id")
        }
        ending = endings.get(ending_id)
        if ending:
            output = engine._execute_tool(
                "end_game",
                {
                    "ending_id": ending_id,
                    "ending_type": ending.get("ending_type", "neutral"),
                    "title": ending.get("title", "故事结束"),
                    "summary": ending.get("description", ""),
                },
            )
            try:
                end_data = json.loads(output)
            except json.JSONDecodeError:
                end_data = {}
            if end_data.get("game_over"):
                engine.cb.on_game_over(
                    end_data.get("ending_type", "neutral"),
                    end_data.get("title", "故事结束"),
                    end_data.get("summary", ""),
                )
                applied.append(f"ending:{ending_id}")
            else:
                skipped.append(f"ending:{ending_id}")

    return {"applied": applied, "skipped": skipped}


def reconcile_turn(
    engine: Any,
    *,
    player_action: str,
    narrative: str,
    executed_tools: list[dict] | None = None,
) -> dict:
    """Ask the judgement model for one compact commit, then validate and apply it."""
    body = narrative_body(narrative)
    if not player_action.strip() or not body:
        return {"applied": [], "skipped": [], "reason": "no player narrative"}
    try:
        state = engine.context.world_store.load()
    except Exception as exc:
        log_error(f"回合审计无法读取世界状态: {exc}")
        return {"applied": [], "skipped": [], "error": str(exc)}

    payload = {
        "player_action": _clip(player_action, 1200),
        "visible_narrative_body": _clip(body, 5000),
        "authoritative_world": _compact_world(state),
        "already_executed_tools": _tool_event_summary(executed_tools or []),
    }
    prompt = (
        "你是 TRPG 引擎的事务审计器，不是故事作者。请调用 commit_turn。\n"
        "只提交可见叙事正文中已经明确完成的事实；玩家输入只是意图，选项不算发生。\n"
        "不要补写故事，不要推测隐藏事实，不要重复 already_executed_tools 已完成的效果。\n"
        "scene_id、clue_id、npc_id、ending_id 只能使用 authoritative_world 中已有 ID。\n"
        "NPC只提到某证物存在、存放地点或传闻，不等于玩家已经亲眼发现该证物；"
        "这类口述可记普通线索，但不得填写对应 clue_id 或 asset_id。\n"
        "只有明确拿在身上/收进口袋的物品才加入背包；留在现场的证物不加入。\n"
        "只有正文明确遭遇了 module_rules 所列恐怖源时才提交一次 sanity_event。\n"
        "结局必须已在正文中真正完成，而且配置的 required_flags 已满足；否则 ending_id 为空。\n"
        "case_clocks 时钟推进是你的固定职责而非推测：正文出现 case_clock_definitions 里"
        "更高等级 levels 描述的事件（征兆、异象、势力动作），或玩家行动/正文情形命中 "
        "advance_when（如拖延、夜间独处、公开指控）时，必须用 clocks_set 把该时钟设为"
        "对应等级（只增不减，键必须是已有的时钟）；两者都不沾时才留空。\n"
        "没有变化时所有数组与 ID 均为空。\n\n" + json.dumps(payload, ensure_ascii=False)
    )
    started_at = time.monotonic()
    audit_model = getattr(engine, "judgement_model", JUDGEMENT_MODEL)
    try:
        with llm_call_slot(
            model=audit_model,
            world_id=str(getattr(getattr(engine, "context", None), "world_id", "") or ""),
        ):
            response = engine.client.chat.completions.create(
                model=audit_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "只做保守的结构化状态审计，宁可漏记也不虚构。"
                            "唯一的例外是案件时钟：按用户消息中的时钟规则推进时钟"
                            "是你的固定职责，命中即记录，不视为虚构。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                # v4 默认 thinking：推理 token 会吃掉 max_tokens 预算，导致
                # commit_turn 参数被截断而无法解析。审计是结构化任务，关思考。
                max_tokens=4000,
                tools=[COMMIT_TURN_TOOL],
                tool_choice="auto",
                extra_body={"thinking": {"type": "disabled"}},
            )
    except Exception as exc:
        log_error(f"回合审计调用失败: {exc}")
        return {"applied": [], "skipped": [], "error": str(exc)}
    elapsed = time.monotonic() - started_at
    log_model_call(audit_model, "audit", elapsed, None, "stop", 1)

    commit = _extract_commit(response)
    if commit is None:
        log_error("回合审计返回了无法解析的 commit_turn")
        return {"applied": [], "skipped": [], "error": "invalid commit"}
    try:
        result = apply_turn_commit(
            engine,
            commit,
            player_action=player_action,
            narrative=body,
            executed_tools=executed_tools,
        )
    except Exception as exc:
        log_error(f"回合审计应用失败: {exc}")
        return {"applied": [], "skipped": [], "error": str(exc)}
    if result["applied"]:
        log_game("回合状态提交 | " + ", ".join(result["applied"]))
    return result
