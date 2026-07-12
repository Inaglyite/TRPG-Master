"""LangGraph 编排层。

这里仅负责 GM 回合的流程控制；世界状态、规则工具、存档和前端事件仍由
GameEngine 及其现有 helper 负责。
"""

from __future__ import annotations

import json
from typing import Any
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from .config import FORCE_PRO, MAX_TOOL_ROUNDS, MODEL_FLASH, MODEL_PRO
from .llm import glm_quick_summary, tension
from .logger import error as log_error, tool as log_tool
from .tools import COMPLEX_FUNCTIONS, dice_summary


class TurnState(TypedDict, total=False):
    engine: Any
    user_content: str | None
    tool_round: int
    narrative: str
    text: str
    tool_calls: list[dict]
    turn_had_check: bool
    tool_outputs: list[tuple[str, str]]
    control_turn: bool


def _tool_category(tool_calls: list[dict]) -> str:
    cat = "dice"
    for tc in tool_calls:
        name = tc["function"]["name"]
        if name.startswith("sanity"):
            return "sanity"
        if name in ("apply_damage", "apply_heal", "combat_start", "combat_action", "combat_end"):
            return "combat"
    return cat


def _prepare_turn(state: TurnState) -> dict:
    engine = state["engine"]
    user_content = state.get("user_content")
    control_turn = user_content is None and engine._has_pending_control_instruction()

    if user_content:
        engine._player_turn_count += 1
        engine._maybe_inject_tier()
        engine.messages.append({"role": "user", "content": f"[玩家行动] {user_content}"})
        engine._detect_content_skill_hint(user_content)

    engine.current_model = MODEL_PRO if FORCE_PRO else MODEL_FLASH
    return {
        "tool_round": 0,
        "narrative": "",
        "text": "",
        "tool_calls": [],
        "turn_had_check": bool(FORCE_PRO),
        "tool_outputs": [],
        "control_turn": control_turn,
    }


def _call_story_agent(state: TurnState) -> dict:
    engine = state["engine"]
    text, tool_calls = engine._stream_llm(
        engine.current_model,
        buffer_if_tools=bool(state.get("control_turn") and state.get("tool_round", 0) == 0),
    )
    return {"text": text, "tool_calls": tool_calls}


def _call_combat_agent(state: TurnState) -> dict:
    engine = state["engine"]
    text, tool_calls = engine._stream_llm(
        engine.current_model,
        system_overlay=engine._combat_system_overlay(),
        buffer_if_tools=bool(state.get("control_turn") and state.get("tool_round", 0) == 0),
    )
    return {"text": text, "tool_calls": tool_calls}


def _route_to_agent(state: TurnState) -> str:
    return "call_combat_agent" if state["engine"]._combat_active() else "call_story_agent"


def _route_after_llm(state: TurnState) -> str:
    text = state.get("text", "")
    tool_calls = state.get("tool_calls", [])

    if not text and not tool_calls:
        return "finalize"
    if not tool_calls:
        return "finalize"
    return "execute_tools"


def _execute_tools(state: TurnState) -> dict:
    engine = state["engine"]
    text = state.get("text", "")
    tool_calls = state.get("tool_calls", [])
    narrative = state.get("narrative", "")
    turn_had_check = state.get("turn_had_check", False)

    complex_hit = any(tc["function"]["name"] in COMPLEX_FUNCTIONS for tc in tool_calls)
    if complex_hit:
        turn_had_check = True
    if complex_hit and state.get("tool_round", 0) == 0:
        engine.cb.on_tension(tension(_tool_category(tool_calls)), _tool_category(tool_calls))

    if text:
        narrative += text + "\n\n"

    assistant_msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    engine.messages.append(assistant_msg)

    tool_outputs: list[tuple[str, str]] = []
    for tc in tool_calls:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError:
            args = {}

        output = engine._execute_tool(name, args)
        engine.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
        log_tool(name, args)

        _handle_tool_side_effects(engine, name, args, output)
        engine._maybe_hint_optional_skill(name)

        if name in ("skill_check", "dice_roll", "dice_roll_advantage", "dice_roll_disadvantage"):
            summary = dice_summary(output)
            if summary:
                try:
                    roll_data = json.loads(output)
                except json.JSONDecodeError:
                    roll_data = None
                engine.cb.on_dice(summary, roll_data)

        if name in {"combat_start", "combat_action"}:
            _emit_combat_dice(engine, output)

        if name in COMPLEX_FUNCTIONS:
            tool_outputs.append((name, output))

        if name == "end_game":
            _handle_end_game(engine, output)

    if tool_outputs:
        quick = glm_quick_summary(tool_outputs, text or narrative)
        if quick:
            engine.cb.on_glm_summary(quick)

    return {
        "narrative": narrative,
        "turn_had_check": turn_had_check,
        "tool_outputs": tool_outputs,
        "tool_round": state.get("tool_round", 0) + 1,
    }


def _handle_tool_side_effects(engine: Any, name: str, args: dict, output: str) -> None:
    if name == "npc_reveal":
        try:
            data = json.loads(output)
            if data.get("ok") and data.get("revealed_level") == 1:
                engine._auto_handout("npc", args.get("npc_id", ""))
        except Exception:
            pass

    if name == "state_set" and args.get("path", "") == "current_scene":
        try:
            val = json.loads(args.get("value", "{}"))
            scene_id = val.get("id", "") if isinstance(val, dict) else ""
        except Exception:
            scene_id = ""
        if scene_id:
            engine._auto_handout("scene", scene_id)

    if name == "state_add_clue":
        try:
            data = json.loads(output)
            asset = data.get("clue", {}).get("asset") or {}
            asset_id = asset.get("id")
            if data.get("ok") and asset_id:
                engine._auto_handout("clue", asset_id)
        except Exception:
            pass


def _handle_end_game(engine: Any, output: str) -> None:
    try:
        end_data = json.loads(output)
        engine.cb.on_game_over(
            end_data.get("ending_type", "neutral"),
            end_data.get("title", "故事结束"),
            end_data.get("summary", ""),
        )
    except json.JSONDecodeError:
        pass


def _route_after_tools(state: TurnState) -> str:
    if state.get("tool_round", 0) <= MAX_TOOL_ROUNDS:
        return _route_to_agent(state)
    return "finalize"


def _emit_combat_dice(engine: Any, output: str) -> None:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return
    if not data.get("ok") or data.get("event") != "action_resolved":
        return

    rolls = []
    for key in ("attack_roll", "defense_roll"):
        roll = data.get(key)
        if isinstance(roll, dict) and isinstance(roll.get("roll"), int):
            rolls.append(roll["roll"])
    damage = data.get("damage")
    wound_check = damage.get("major_wound_check") if isinstance(damage, dict) else None
    if isinstance(wound_check, dict) and isinstance(wound_check.get("roll"), int):
        rolls.append(wound_check["roll"])
    if not rolls:
        return
    engine.cb.on_dice(
        data.get("summary", "战斗对抗已结算"),
        {
            "spec": f"{len(rolls)}d100",
            "sides": 100,
            "count": len(rolls),
            "rolls": rolls,
            "total": sum(rolls),
            "combat": True,
        },
    )


def _finalize_turn(state: TurnState) -> dict:
    engine = state["engine"]
    narrative = state.get("narrative", "")
    text = state.get("text", "")
    tool_calls = state.get("tool_calls", [])

    if not tool_calls and text:
        narrative = text

    if narrative.strip():
        engine.messages.append({"role": "assistant", "content": narrative.strip()})
    else:
        log_error("空回合：模型未生成任何叙述或工具调用")
        engine.cb.on_error("守秘人陷入了沉思……")

    engine.save("slot_000")
    engine._last_turn_high_risk = state.get("turn_had_check", False)
    engine._round_count += 1
    engine.cb.on_done()
    engine._maybe_summarize_after_turn()
    return {"narrative": narrative}


def build_turn_graph():
    graph = StateGraph(TurnState)
    graph.add_node("prepare_turn", _prepare_turn)
    graph.add_node("call_story_agent", _call_story_agent)
    graph.add_node("call_combat_agent", _call_combat_agent)
    graph.add_node("execute_tools", _execute_tools)
    graph.add_node("finalize", _finalize_turn)

    graph.add_edge(START, "prepare_turn")
    graph.add_conditional_edges("prepare_turn", _route_to_agent, {
        "call_story_agent": "call_story_agent",
        "call_combat_agent": "call_combat_agent",
    })
    for agent_node in ("call_story_agent", "call_combat_agent"):
        graph.add_conditional_edges(
            agent_node,
            _route_after_llm,
            {"execute_tools": "execute_tools", "finalize": "finalize"},
        )
    graph.add_conditional_edges(
        "execute_tools",
        _route_after_tools,
        {
            "call_story_agent": "call_story_agent",
            "call_combat_agent": "call_combat_agent",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile()
