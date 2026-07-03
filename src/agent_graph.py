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
from .tools import COMPLEX_FUNCTIONS, dice_summary, needs_pro_model


class TurnState(TypedDict, total=False):
    engine: Any
    user_content: str | None
    tool_round: int
    narrative: str
    text: str
    tool_calls: list[dict]
    turn_had_check: bool
    tool_outputs: list[tuple[str, str]]


def _tool_category(tool_calls: list[dict]) -> str:
    cat = "dice"
    for tc in tool_calls:
        name = tc["function"]["name"]
        if name.startswith("sanity"):
            return "sanity"
        if name in ("apply_damage", "apply_heal"):
            return "combat"
    return cat


def _prepare_turn(state: TurnState) -> dict:
    engine = state["engine"]
    user_content = state.get("user_content")

    if user_content:
        engine._maybe_inject_tier()
        engine.messages.append({"role": "user", "content": f"[玩家行动] {user_content}"})
        engine._detect_content_skill_hint(user_content)

    if engine._should_summarize():
        engine._summarize_history()

    engine.current_model = MODEL_PRO if FORCE_PRO else MODEL_FLASH
    return {
        "tool_round": 0,
        "narrative": "",
        "text": "",
        "tool_calls": [],
        "turn_had_check": bool(FORCE_PRO),
        "tool_outputs": [],
    }


def _call_llm(state: TurnState) -> dict:
    engine = state["engine"]
    text, tool_calls = engine._stream_llm(engine.current_model)
    return {"text": text, "tool_calls": tool_calls}


def _route_after_llm(state: TurnState) -> str:
    text = state.get("text", "")
    tool_calls = state.get("tool_calls", [])

    if not text and not tool_calls:
        return "finalize"
    if not tool_calls:
        return "finalize"
    if state["engine"].current_model == MODEL_FLASH and needs_pro_model(tool_calls):
        return "switch_to_pro"
    return "execute_tools"


def _switch_to_pro(state: TurnState) -> dict:
    engine = state["engine"]
    tool_calls = state.get("tool_calls", [])
    engine.current_model = MODEL_PRO
    engine.cb.on_tension(tension(_tool_category(tool_calls)), _tool_category(tool_calls))
    if engine.messages and engine.messages[-1]["role"] == "assistant":
        engine.messages.pop()
    return {}


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
                engine.cb.on_dice(summary)

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
        return "call_llm"
    return "finalize"


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
    return {"narrative": narrative}


def build_turn_graph():
    graph = StateGraph(TurnState)
    graph.add_node("prepare_turn", _prepare_turn)
    graph.add_node("call_llm", _call_llm)
    graph.add_node("switch_to_pro", _switch_to_pro)
    graph.add_node("execute_tools", _execute_tools)
    graph.add_node("finalize", _finalize_turn)

    graph.add_edge(START, "prepare_turn")
    graph.add_edge("prepare_turn", "call_llm")
    graph.add_conditional_edges(
        "call_llm",
        _route_after_llm,
        {
            "switch_to_pro": "switch_to_pro",
            "execute_tools": "execute_tools",
            "finalize": "finalize",
        },
    )
    graph.add_edge("switch_to_pro", "call_llm")
    graph.add_conditional_edges(
        "execute_tools",
        _route_after_tools,
        {
            "call_llm": "call_llm",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile()
