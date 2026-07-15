"""LangGraph 编排层。

这里仅负责 GM 回合的流程控制；世界状态、规则工具、存档和前端事件仍由
GameEngine 及其现有 helper 负责。
"""

from __future__ import annotations

import json
from typing import Any
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from .config import (
    ENABLE_TURN_AUDIT,
    JUDGEMENT_MODEL,
    MAX_TOOL_ROUNDS,
    NARRATIVE_MODEL,
)
from .discovery import preferred_luck_difficulty
from .llm import glm_quick_summary, tension
from .logger import error as log_error, tool as log_tool
from .tools import COMPLEX_FUNCTIONS, dice_summary
from .choices import extract_action_choices


class TurnState(TypedDict, total=False):
    engine: Any
    user_content: str | None
    tool_round: int
    narrative: str
    text: str
    tool_calls: list[dict]
    turn_had_check: bool
    tool_outputs: list[tuple[str, str]]
    executed_tools: list[dict]
    control_turn: bool
    opening_turn: bool
    lore_active: bool
    lore_entry_ids: list[str]


def _tool_category(tool_calls: list[dict]) -> str:
    cat = "dice"
    for tc in tool_calls:
        name = tc["function"]["name"]
        if name.startswith("sanity"):
            return "sanity"
        if name in ("apply_damage", "apply_heal", "combat_start", "combat_action", "combat_end"):
            return "combat"
    return cat


def _emit_phase(engine: Any, phase: str, label: str) -> None:
    callback = getattr(getattr(engine, "cb", None), "on_phase", None)
    if callback:
        callback(phase, label)


def _check_cancelled(engine: Any) -> None:
    callback = getattr(engine, "raise_if_turn_cancelled", None)
    if callback:
        callback()


def _prepare_turn(state: TurnState) -> dict:
    engine = state["engine"]
    # The resolution is turn-local authority.  Control turns must never inherit
    # an arrival boundary from the preceding player action.
    engine._action_resolution = None
    engine._encounter_resolution = None
    _check_cancelled(engine)
    user_content = state.get("user_content")
    control_turn = user_content is None and engine._has_pending_control_instruction()
    opening_turn = bool(
        control_turn and engine._has_pending_new_game_opening()
    )
    resolved_discoveries: list[dict] = []
    lore_selection = None
    prelude = ""

    _emit_phase(
        engine,
        "preparing",
        "正在准备开场……" if opening_turn else "正在整理当前场景……",
    )

    if user_content:
        engine._player_turn_count += 1
        engine._maybe_inject_tier()
        action_resolution = engine._plan_player_action(user_content)
        engine._action_resolution = action_resolution
        transition_id = action_resolution.destination_scene_id
        discovery_matches = list(action_resolution.discovery_matches)
        discovery_skill = action_resolution.preferred_skill
        prelude = engine._turn_prelude(transition_id, discovery_matches)
        if prelude:
            engine.cb.on_narrative(f"{prelude}\n\n")
        if transition_id:
            engine._resolve_scene_transition(user_content)
            encounter_text = str(
                getattr(engine, "_encounter_resolution", None).narrative_text
                if getattr(engine, "_encounter_resolution", None)
                else ""
            ).strip()
            if encounter_text:
                prelude = f"{prelude}\n\n{encounter_text}" if prelude else encounter_text
                engine.cb.on_narrative(f"{encounter_text}\n\n")

        # An authored unconditional discovery is itself the authority for this
        # action.  Do not let the generic language matcher invent an extra roll.
        needs_discovery_check = any(
            bool(match.rule.get("requires_success"))
            for match in discovery_matches
        )
        _emit_phase(engine, "resolving", "正在结算本轮行动……")
        luck_difficulty = preferred_luck_difficulty(discovery_matches)
        check_result = (
            engine._resolve_luck_check(luck_difficulty)
            if not transition_id and luck_difficulty
            else (
                engine._resolve_action_check(user_content, discovery_skill)
                if not transition_id and (not discovery_matches or needs_discovery_check)
                else None
            )
        )
        resolved_discoveries = engine._resolve_discoveries(
            discovery_matches,
            check_result,
        )
        authority = engine._authoritative_turn_context(
            check_result,
            resolved_discoveries,
        )
        retrieve_lore = getattr(engine, "_retrieve_lore_context", None)
        lore_selection = retrieve_lore(user_content) if retrieve_lore else None
        content = f"[玩家行动] {user_content}"
        if prelude:
            content += (
                "\n\n[本轮已向玩家展示的前置叙事]\n"
                f"{prelude}\n"
                "从此处之后继续叙述，不要重复赶路、抵达或揭示动作。"
            )
        if authority:
            content += f"\n\n{authority}"
        if lore_selection and lore_selection.context:
            content += f"\n\n{lore_selection.context}"
        engine.messages.append({"role": "user", "content": content})
        engine._detect_content_skill_hint(user_content)
    else:
        check_result = None
        if control_turn:
            authority = engine._authoritative_turn_context()
            if authority and "[引擎权威状态｜仅供守秘人，不得复述]" not in engine.messages[-1].get("content", ""):
                engine.messages[-1]["content"] += f"\n\n{authority}"
            retrieve_lore = getattr(engine, "_retrieve_lore_context", None)
            lore_selection = retrieve_lore() if retrieve_lore else None
            if (
                lore_selection
                and lore_selection.context
                and "[本轮 Lorebook 检索素材｜仅供守秘人，不得复述标签]"
                not in engine.messages[-1].get("content", "")
            ):
                engine.messages[-1]["content"] += f"\n\n{lore_selection.context}"

    engine.current_model = getattr(engine, "narrative_model", NARRATIVE_MODEL)
    return {
        "tool_round": 0,
        "narrative": prelude,
        "text": "",
        "tool_calls": [],
        "turn_had_check": bool(check_result or resolved_discoveries),
        "tool_outputs": [],
        "executed_tools": [],
        "control_turn": control_turn,
        "opening_turn": opening_turn,
        "lore_active": lore_selection is not None,
        "lore_entry_ids": list(lore_selection.entry_ids) if lore_selection else [],
    }


def _call_story_agent(state: TurnState) -> dict:
    engine = state["engine"]
    opening_turn = state.get("opening_turn", False)
    _emit_phase(
        engine,
        "narrating",
        "守秘人正在展开开场……" if opening_turn else "守秘人正在续写场景……",
    )
    text, tool_calls = engine._stream_llm(
        engine.current_model,
        system_prompt_override=(
            engine._opening_system_prompt() if opening_turn else None
        ),
        enable_tools=not opening_turn,
        prompt_profile="opening" if opening_turn else None,
        temperature=0.65 if opening_turn else 0.8,
        buffer_if_tools=False,
    )
    return {"text": text, "tool_calls": tool_calls}


def _call_combat_agent(state: TurnState) -> dict:
    engine = state["engine"]
    _emit_phase(engine, "narrating", "守秘人正在结算战局……")
    text, tool_calls = engine._stream_llm(
        getattr(engine, "judgement_model", JUDGEMENT_MODEL),
        system_overlay=engine._combat_system_overlay(),
        buffer_if_tools=False,
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
    _check_cancelled(engine)
    _emit_phase(engine, "updating", "正在整理行动结果……")
    text = state.get("text", "")
    tool_calls = state.get("tool_calls", [])
    narrative = state.get("narrative", "")
    turn_had_check = state.get("turn_had_check", False)

    complex_hit = any(tc["function"]["name"] in COMPLEX_FUNCTIONS for tc in tool_calls)
    if complex_hit:
        turn_had_check = True
        engine.current_model = getattr(
            engine, "judgement_model", JUDGEMENT_MODEL
        )
    if complex_hit and state.get("tool_round", 0) == 0:
        engine.cb.on_tension(tension(_tool_category(tool_calls)), _tool_category(tool_calls))

    if text:
        narrative += text + "\n\n"

    assistant_msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    engine.messages.append(assistant_msg)

    tool_outputs: list[tuple[str, str]] = []
    executed_tools = list(state.get("executed_tools", []))
    for tc in tool_calls:
        _check_cancelled(engine)
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            args = {}

        try:
            execute_model_tool = getattr(engine, "_execute_model_tool", None)
            if execute_model_tool:
                output = execute_model_tool(
                    name,
                    args,
                    player_action=state.get("user_content") or "",
                )
            else:
                output = engine._execute_tool(name, args)
        except Exception as exc:
            log_error(f"工具 {name} 执行异常: {type(exc).__name__}: {exc}")
            output = "[错误] 工具执行失败，请检查参数后重试"
        executed_tools.append({"name": name, "args": args, "output": output})
        engine.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
        log_tool(name, args)

        if name in ("skill_check", "dice_roll", "dice_roll_advantage", "dice_roll_disadvantage"):
            summary = dice_summary(output)
            if summary:
                try:
                    roll_data = json.loads(output)
                except json.JSONDecodeError:
                    roll_data = None
                engine.cb.on_dice(summary, roll_data)

        if name in {"sanity_event", "sanity_loss"}:
            _emit_sanity_dice(engine, output)

        if name in {"combat_start", "combat_action"}:
            _emit_combat_dice(engine, output)

        if name in COMPLEX_FUNCTIONS:
            tool_outputs.append((name, output))

        if name == "end_game":
            _handle_end_game(engine, output)

    # A tool-calling assistant message must be followed immediately by every
    # matching tool response. Optional skill instructions are user messages, so
    # they can only be appended after the whole batch has been answered.
    for name in dict.fromkeys(
        tc["function"]["name"] for tc in tool_calls
    ):
        engine._maybe_hint_optional_skill(name)

    if tool_outputs:
        quick = glm_quick_summary(tool_outputs, text or narrative)
        if quick:
            engine.cb.on_glm_summary(quick)

    return {
        "narrative": narrative,
        "turn_had_check": turn_had_check,
        "tool_outputs": tool_outputs,
        "executed_tools": executed_tools,
        "tool_round": state.get("tool_round", 0) + 1,
    }


def _handle_end_game(engine: Any, output: str) -> None:
    try:
        end_data = json.loads(output)
        if not end_data.get("game_over"):
            return
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


def _emit_sanity_dice(engine: Any, output: str) -> None:
    try:
        data = json.loads(output)
        roll = int(data["san_roll"])
        before = int(data["san_before"])
        loss = int(data["actual_loss"])
        success = bool(data["san_check_success"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return
    engine.cb.on_dice(
        f"理智检定 {roll} vs {before}，{'成功' if success else '失败'}，SAN -{loss}",
        {
            "spec": "d100",
            "sides": 100,
            "count": 1,
            "rolls": [roll],
            "total": roll,
            "sanity": True,
            "success": success,
            "loss": loss,
        },
    )


def _finalize_turn(state: TurnState) -> dict:
    engine = state["engine"]
    _check_cancelled(engine)
    narrative = state.get("narrative", "")
    text = state.get("text", "")
    tool_calls = state.get("tool_calls", [])

    if not tool_calls and text:
        if narrative and not narrative.endswith(("\n", " ")):
            narrative += "\n\n"
        narrative += text

    if narrative.strip():
        engine.messages.append({"role": "assistant", "content": narrative.strip()})
    else:
        log_error("空回合：模型未生成任何叙述或工具调用")
        engine.cb.on_error("守秘人陷入了沉思……")

    if narrative.strip():
        engine._reconcile_narrative_entities(narrative)
        if (
            ENABLE_TURN_AUDIT
            and
            state.get("user_content")
            and engine._turn_needs_model_audit(
                state.get("executed_tools", []),
                player_action=state.get("user_content") or "",
                narrative=narrative,
            )
        ):
            engine._reconcile_turn(
                state.get("user_content") or "",
                narrative,
                state.get("executed_tools", []),
            )
        _check_cancelled(engine)
        engine._dispatch_narrative_handouts(narrative)
        if state.get("lore_active"):
            engine._record_lore_usage(tuple(state.get("lore_entry_ids", [])))

    _check_cancelled(engine)
    engine.save("slot_000")
    choices = extract_action_choices(narrative)
    choices_callback = getattr(engine.cb, "on_choices", None)
    if choices_callback and choices:
        choices_callback(choices)
    complete_turn = getattr(engine, "_complete_turn_record", None)
    if complete_turn:
        complete_turn(
            narrative=narrative,
            choices=choices,
            executed_tools=list(state.get("executed_tools", [])),
            lore_entry_ids=list(state.get("lore_entry_ids", [])),
        )
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
