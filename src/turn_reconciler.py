"""Turn-end transaction audit for narrative/state consistency."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .config import JUDGEMENT_MODEL
from .logger import error as log_error
from .logger import game_event as log_game
from .logger import model_call as log_model_call

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
    r"|你(?:受伤|中弹|流血|昏迷|死亡|理智崩溃)"
    r"|(?:案件|调查|故事).{0,12}(?:结束|告终)"
    r")"
)


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
                clues.append({
                    "id": clue.get("catalog_id") or clue.get("id"),
                    "category": category,
                    "text": _clip(clue.get("text"), 240),
                })

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
        npcs.append({
            "id": npc.get("id"),
            "name": npc.get("name"),
            "location": npc.get("current_location"),
            "revealed_level": revealed.get("level", 0),
            "revealed_entries": [
                _clip(entry.get("text"), 180)
                for entry in revealed.get("entries", [])[-8:]
                if isinstance(entry, dict)
            ],
        })

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
        prefix = text[max(0, match.start() - 28):match.start()]
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
            position = _scene_transition_position(
                str(scene.get("name") or ""), body
            )
            if position >= 0:
                candidates.append((
                    position,
                    len(str(scene.get("name") or "")),
                    str(scene_id),
                ))

    applied: list[str] = []
    if candidates:
        scene_id = max(candidates)[2]
        current_scene = state.get("current_scene", {})
        target_scene = scenes[scene_id]
        if (
            current_scene.get("id") != scene_id
            or current_scene.get("name") != target_scene.get("name")
        ):
            engine._execute_tool("state_set", {
                "path": "current_scene.id",
                "value": json.dumps(scene_id, ensure_ascii=False),
            })
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
        engine._execute_tool("npc_reveal", {
            "npc_id": npc["id"],
            "tier": 1,
            "entry_text": entry,
        })
        applied.append(f"npc:{npc['id']}")
    if applied:
        log_game("确定性叙事同步 | " + ", ".join(applied))
    return applied


def turn_needs_model_audit(
    executed_tools: list[dict] | None,
    *,
    player_action: str = "",
    narrative: str | None = None,
) -> bool:
    """Audit only stateful prose that reached no authoritative transaction."""
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
    return bool(_STATEFUL_NARRATIVE_PATTERN.search(narrative_body(narrative)))


def _parse_json_scalar(raw: str) -> Any:
    value = json.loads(raw)
    if isinstance(value, (dict, list)):
        raise ValueError("flags only accept scalar values")
    return value


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
            engine._execute_tool("state_set", {
                "path": "current_scene",
                "value": json.dumps(scene_value, ensure_ascii=False),
            })
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
        if (
            npc
            and entry
            and 1 <= tier <= 3
            and _name_mentioned(str(npc.get("name", "")), body)
        ):
            output = engine._execute_tool("npc_reveal", {
                "npc_id": npc_id,
                "tier": tier,
                "entry_text": entry,
            })
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
            output = engine._execute_tool("end_game", {
                "ending_id": ending_id,
                "ending_type": ending.get("ending_type", "neutral"),
                "title": ending.get("title", "故事结束"),
                "summary": ending.get("description", ""),
            })
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
        "没有变化时所有数组与 ID 均为空。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    started_at = time.monotonic()
    audit_model = getattr(engine, "judgement_model", JUDGEMENT_MODEL)
    try:
        response = engine.client.chat.completions.create(
            model=audit_model,
            messages=[
                {
                    "role": "system",
                    "content": "只做保守的结构化状态审计，宁可漏记也不虚构。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=1400,
            tools=[COMMIT_TURN_TOOL],
            tool_choice="auto",
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
