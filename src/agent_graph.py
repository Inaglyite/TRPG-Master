"""LangGraph 编排层。

这里仅负责 GM 回合的流程控制；世界状态、规则工具、存档和前端事件仍由
GameEngine 及其现有 helper 负责。
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from .action_preflight import match_action_preview
from .choices import extract_action_choices
from .config import (
    ENABLE_TURN_AUDIT,
    JUDGEMENT_MODEL,
    MAX_TOOL_ROUNDS,
    NARRATIVE_MODEL,
    tool_execution_timeout_ms,
    tool_pipeline_shadow_enabled,
    tool_pipeline_v2_enabled,
)
from .crisis import maybe_fire_crisis
from .discovery import preferred_luck_difficulty
from .llm import glm_quick_summary, tension
from .logger import error as log_error
from .logger import tool as log_tool
from .npc_speaker_aliases import current_scene_npc_ids
from .skill_activation import note_load_skill_result
from .speaker_parser import Segment
from .speaker_parser import parse_segments as parse_speaker_segments
from .tool_pipeline import ToolPipeline, record_engine_tool_shadow
from .tool_policy import (
    REQUEST_METADATA_KEY,
    ToolPolicyError,
    ToolRequestSnapshot,
    authorize_model_tool_call,
    denied_tool_result,
    public_tool_call,
    schemas_for_catalog,
)
from .tool_request_authority import execution_snapshot, issued_model_request
from .tools import (
    COMPLEX_FUNCTIONS,
    MODEL_TOOL_NAMES,
    dice_summary,
)
from .transition_prelude import build_scene_entry_beat, build_transition_prelude

_NON_REPEATABLE_CHECKS = {
    "skill_check",
    "attribute_check",
    "luck_check",
    "sanity_check",
    "sanity_loss",
    "sanity_event",
    "psychoanalysis",
    "reality_check",
}


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
    skip_agent: bool
    skip_model_audit: bool
    player_followups: list[dict]
    authored_prefix: str
    authored_clean_prefix: str
    authored_segments: list[dict]


def _tool_category(tool_calls: list[dict]) -> str:
    cat = "dice"
    for tc in tool_calls:
        function = tc.get("function") if isinstance(tc, dict) else {}
        name = str(function.get("name") or "") if isinstance(function, dict) else ""
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


def _emit_authored_narrative(engine: Any, text: str) -> None:
    """Emit a trusted tagged beat through the normal chat bubble callbacks."""
    if not text.strip():
        return
    segments, _clean = parse_speaker_segments(
        text,
        is_valid_npc=getattr(engine, "is_valid_npc_id", None) or (lambda _npc_id: False),
        on_unknown_npc=getattr(engine, "log_unknown_npc_speaker", None),
    )
    for segment in segments:
        if segment.kind == "speech" and segment.npc_id:
            engine.cb.on_speaker_segment(segment.npc_id)
            engine.cb.on_narrative(f"{segment.text}\n\n", segment.npc_id)
        else:
            engine.cb.on_narrative(f"{segment.text}\n\n")


def _parse_authored_parts(engine: Any, parts: list[str]) -> tuple[list[Segment], str]:
    """Parse trusted engine prose beat-by-beat without scene-based speaker guessing."""
    segments: list[Segment] = []
    clean_parts: list[str] = []
    for part in parts:
        parsed, clean = parse_speaker_segments(
            part,
            is_valid_npc=getattr(engine, "is_valid_npc_id", None) or (lambda _npc_id: False),
            on_unknown_npc=getattr(engine, "log_unknown_npc_speaker", None),
        )
        segments.extend(parsed)
        if clean.strip():
            clean_parts.append(clean.strip())
    return segments, "\n\n".join(clean_parts)


def _performance_span(engine: Any, name: str):
    factory = getattr(engine, "performance_span", None)
    return factory(name) if factory else nullcontext()


def _prepare_turn(state: TurnState) -> dict:
    engine = state["engine"]
    # The resolution is turn-local authority.  Control turns must never inherit
    # an arrival boundary from the preceding player action.
    engine._action_resolution = None
    engine._encounter_resolution = None
    _check_cancelled(engine)
    user_content = state.get("user_content")
    control_turn = user_content is None and engine._has_pending_control_instruction()
    opening_turn = bool(control_turn and engine._has_pending_new_game_opening())
    _emit_phase(
        engine,
        "preparing",
        "正在准备开场……" if opening_turn else "正在整理当前场景……",
    )

    with _performance_span(engine, "prepare"):
        result = _prepare_turn_inner(state, engine, user_content, control_turn, opening_turn)
    return result


def _prepare_turn_inner(
    state: TurnState,
    engine: Any,
    user_content: str | None,
    control_turn: bool,
    opening_turn: bool,
) -> dict:
    resolved_discoveries: list[dict] = []
    lore_selection = None
    prelude = ""
    prelude_parts: list[str] = []
    skip_agent = False
    skip_model_audit = False
    player_followups = list(getattr(engine, "_preflight_player_followups", None) or [])
    # 记录回合前的消息数：容量熔断（未发起任何 provider 请求）时 finalize
    # 用它回滚本回合追加，避免重试叠加毒化历史。
    state["pre_turn_message_len"] = len(getattr(engine, "messages", None) or [])
    engine.__dict__["_capacity_rejected_turn"] = False
    # 供流式发言归属（model_stream_helpers）做玩家台词守卫。
    engine.__dict__["_turn_user_content"] = user_content
    if user_content:
        original_user_content = user_content
        engine._player_turn_count += 1
        engine._maybe_inject_tier()
        # 模组声明的危机触发（伏击/显形）：条件按上一回合末状态判定，
        # 由引擎确定性落地；文本进 prelude，战斗状态本回合即被战斗 agent 接管。
        crisis_text = "" if opening_turn else maybe_fire_crisis(engine)
        preplanned = getattr(engine, "_preplanned_action_resolution", None)
        action_resolution = (
            preplanned
            if preplanned is not None and preplanned.player_input == user_content
            else engine._plan_player_action(user_content)
        )
        engine._action_resolution = action_resolution
        prelude_parts = [str(getattr(engine, "_preflight_narrative", "") or "").strip()]
        prelude_parts = [part for part in prelude_parts if part]
        if crisis_text:
            prelude_parts.append(crisis_text)
            engine.cb.on_narrative(f"{crisis_text}\n\n")

        try:
            preview_world = engine.context.world_store.load()
        except Exception:
            preview_world = {}
        action_preview = match_action_preview(action_resolution, preview_world)
        selected_preview_option = None
        if action_preview is not None:
            prelude_parts.append(action_preview.narrative)
            _emit_authored_narrative(engine, action_preview.narrative)
            _emit_phase(engine, "awaiting_decision", "等待你决定是否继续……")
            selected_id = engine.cb.on_decision(action_preview.decision_payload())
            selected_preview_option = action_preview.option(selected_id)
            preview_segments, _ = _parse_authored_parts(engine, prelude_parts)
            player_followups.append(
                {
                    "text": selected_preview_option.label,
                    "after_narrative_segment": len(preview_segments),
                }
            )
            engine.record_turn_event(
                {"type": "player_reply", "text": selected_preview_option.label}
            )
            if selected_preview_option.outcome == "cancel":
                skip_agent = True
                skip_model_audit = True
            elif selected_preview_option.outcome == "replace":
                user_content = selected_preview_option.action_text
                engine.__dict__["_turn_user_content"] = user_content
                # The replacement is authored by the same preview and is
                # frozen immediately.  The decision reply label is never fed
                # back through the natural-language router.
                action_resolution = engine._plan_player_action(user_content)
                engine._action_resolution = action_resolution

        transition_id = None if skip_agent else action_resolution.destination_scene_id
        discovery_matches = [] if skip_agent else list(action_resolution.discovery_matches)
        discovery_skill = None if skip_agent else action_resolution.preferred_skill
        transition_prelude = (
            ""
            if skip_agent
            else build_transition_prelude(
                preview_world,
                action_resolution,
                transition_id,
                discovery_matches,
            )
        )
        if transition_prelude:
            prelude_parts.append(transition_prelude)
            engine.cb.on_narrative(f"{transition_prelude}\n\n")
        if transition_id:
            engine._resolve_scene_transition(
                user_content,
                destination_scene_id=transition_id,
            )
            ledger = getattr(engine, "_turn_mutations", None)
            if ledger is not None:
                ledger.record_domain("scene_transition", {"scene_id": transition_id})
            encounter_text = str(
                getattr(engine, "_encounter_resolution", None).narrative_text
                if getattr(engine, "_encounter_resolution", None)
                else ""
            ).strip()
            if encounter_text:
                prelude_parts.append(encounter_text)
                engine.cb.on_narrative(f"{encounter_text}\n\n")
            try:
                entry_world = engine.context.world_store.load()
            except Exception:
                entry_world = {}
            entry_text = build_scene_entry_beat(entry_world, transition_id)
            if entry_text:
                prelude_parts.append(entry_text)
                engine.cb.on_narrative(f"{entry_text}\n\n")

        prelude = "\n\n".join(part for part in prelude_parts if part)

        # An authored unconditional discovery is itself the authority for this
        # action.  Do not let the generic language matcher invent an extra roll.
        needs_discovery_check = any(
            bool(match.rule.get("requires_success")) for match in discovery_matches
        )
        _emit_phase(engine, "resolving", "正在结算本轮行动……")
        luck_difficulty = preferred_luck_difficulty(discovery_matches)
        check_result = (
            engine._resolve_luck_check(luck_difficulty)
            if not skip_agent and not transition_id and luck_difficulty
            else (
                engine._resolve_action_check(user_content, discovery_skill)
                if not skip_agent
                and not transition_id
                and (not discovery_matches or needs_discovery_check)
                else None
            )
        )
        resolved_discoveries = (
            engine._resolve_discoveries(discovery_matches, check_result) if not skip_agent else []
        )
        ledger = getattr(engine, "_turn_mutations", None)
        if ledger is not None and resolved_discoveries:
            ledger.record_domain(
                "resolved_discoveries",
                {"count": len(resolved_discoveries)},
            )
        authority = (
            engine._authoritative_turn_context(check_result, resolved_discoveries)
            if not skip_agent
            else ""
        )
        retrieve_lore = getattr(engine, "_retrieve_lore_context", None)
        lore_selection = retrieve_lore(user_content) if retrieve_lore and not skip_agent else None
        content = f"[玩家行动] {original_user_content}"
        if selected_preview_option is not None:
            content += f"\n[行动预演后的选择] {selected_preview_option.label}"
            if selected_preview_option.outcome == "replace":
                content += f"\n[本轮实际行动] {user_content}"
            elif selected_preview_option.outcome == "cancel":
                content += "\n[权威结果] 玩家暂不执行原行动；场景保持不变。"
        if prelude:
            content += (
                "\n\n[本轮已向玩家展示的前置叙事]\n"
                f"{prelude}\n"
                "从此处之后继续叙述，不要重复赶路、抵达或揭示动作。"
            )
        if authority:
            content += f"\n\n{authority}"
        content += (
            "\n\n[输出格式] NPC 直接引语的台词必须用 【npc:<npc_public_state 中的 id>】…"
            "【/npc】 包裹（只包台词；提及、转述、动作神态不加）。"
        )
        if lore_selection and lore_selection.context:
            content += f"\n\n{lore_selection.context}"
        engine.messages.append({"role": "user", "content": content})
        if not skip_agent:
            engine._detect_content_skill_hint(user_content)
    else:
        check_result = None
        if control_turn:
            authority = engine._authoritative_turn_context()
            if authority and "[引擎权威状态｜仅供守秘人，不得复述]" not in engine.messages[-1].get(
                "content", ""
            ):
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

    authored_segments, authored_clean_prefix = _parse_authored_parts(engine, prelude_parts)
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
        "user_content": user_content,
        "skip_agent": skip_agent,
        "skip_model_audit": skip_model_audit,
        "player_followups": player_followups,
        "authored_prefix": prelude,
        "authored_clean_prefix": authored_clean_prefix,
        "authored_segments": [segment.to_dict() for segment in authored_segments],
    }


def _call_story_agent(state: TurnState) -> dict:
    engine = state["engine"]
    opening_turn = state.get("opening_turn", False)
    _emit_phase(
        engine,
        "narrating",
        "守秘人正在展开开场……" if opening_turn else "守秘人正在续写场景……",
    )
    with _performance_span(engine, "story_model"):
        text, tool_calls = engine._stream_llm(
            engine.current_model,
            system_prompt_override=(engine._opening_system_prompt() if opening_turn else None),
            # A resolved check is an immutable fact for this player action. The
            # follow-up request may narrate it, but must not roll again or enter a
            # second tool-planning loop.
            enable_tools=not opening_turn and not state.get("turn_had_check", False),
            prompt_profile="opening" if opening_turn else None,
            temperature=0.65 if opening_turn else 0.8,
            buffer_if_tools=True,
        )
    return {"text": text, "tool_calls": tool_calls}


def _call_combat_agent(state: TurnState) -> dict:
    engine = state["engine"]
    sync_skills = getattr(engine, "_detect_content_skill_hint", None)
    if sync_skills:
        # 战斗状态是权威能力：由 resolver 的 combat_active 谓词确定性激活
        # keeper.combat（不依赖关键词，也不要求模型先尝试不安全的文件读取）。
        sync_skills("")
    _emit_phase(engine, "narrating", "守秘人正在结算战局……")
    with _performance_span(engine, "combat_model"):
        text, tool_calls = engine._stream_llm(
            getattr(engine, "judgement_model", JUDGEMENT_MODEL),
            system_overlay=engine._combat_system_overlay(),
            buffer_if_tools=True,
        )
    return {"text": text, "tool_calls": tool_calls}


def _route_to_agent(state: TurnState) -> str:
    return "call_combat_agent" if state["engine"]._combat_active() else "call_story_agent"


def _route_after_prepare(state: TurnState) -> str:
    if state.get("skip_agent"):
        return "finalize"
    return _route_to_agent(state)


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

    complex_hit = any(
        str((tc.get("function") or {}).get("name") or "") in COMPLEX_FUNCTIONS
        for tc in tool_calls
        if isinstance(tc, dict)
    )
    if complex_hit:
        turn_had_check = True
        engine.current_model = getattr(engine, "judgement_model", JUDGEMENT_MODEL)
    if complex_hit and state.get("tool_round", 0) == 0:
        engine.cb.on_tension(tension(_tool_category(tool_calls)), _tool_category(tool_calls))

    if text:
        narrative += text + "\n\n"

    assistant_msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        # Request authorization metadata is server-only evidence.  Persisting it
        # in provider history would leak an internal protocol and some OpenAI
        # compatible APIs reject unknown tool-call fields.
        assistant_msg["tool_calls"] = [public_tool_call(tc) for tc in tool_calls]
    engine.messages.append(assistant_msg)

    tool_outputs: list[tuple[str, str]] = []
    executed_tools = list(state.get("executed_tools", []))
    executed_call_names: list[str] = []
    use_pipeline_v2 = tool_pipeline_v2_enabled()
    shadow_pipeline = (
        ToolPipeline(engine, timeout_ms=tool_execution_timeout_ms())
        if (use_pipeline_v2 or tool_pipeline_shadow_enabled())
        else None
    )
    prior_checks = {
        json.dumps(
            {"name": entry.get("name"), "args": entry.get("args", {})},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ): str(entry.get("output") or "")
        for entry in executed_tools
        if isinstance(entry, dict) and entry.get("name") in _NON_REPEATABLE_CHECKS
    }
    for tc in tool_calls:
        _check_cancelled(engine)
        function = tc.get("function") if isinstance(tc, dict) else {}
        function = function if isinstance(function, dict) else {}
        raw_name = str(function.get("name") or "unknown")
        call_id = str(tc.get("id") or "") if isinstance(tc, dict) else ""
        if use_pipeline_v2:
            assert shadow_pipeline is not None
            with _performance_span(engine, "tool_execution"):
                outcome = shadow_pipeline.execute(
                    tc,
                    player_action=state.get("user_content") or "",
                )
            name = outcome.name
            args = dict(outcome.args)
            output = outcome.output
            reused = outcome.reused
            executed_tools.append(outcome.executed_tool_dict())
            if outcome.status == "denied":
                log_error(f"工具策略拒绝: name={raw_name} reason={outcome.error_code or 'unknown'}")
            elif outcome.status not in {"ok", "reused"}:
                log_error(f"工具管线拒绝或失败: name={name} status={outcome.status}")
        else:
            if shadow_pipeline is not None:
                record_engine_tool_shadow(engine, shadow_pipeline.shadow(tc))
            try:
                snapshot = ToolRequestSnapshot.from_dict(
                    tc.get(REQUEST_METADATA_KEY) if isinstance(tc, dict) else None
                )
                issued = issued_model_request(engine, snapshot)
                ordered_catalog = issued.catalog_copy()
                snapshot, name, args = authorize_model_tool_call(
                    tc,
                    tool_schemas=schemas_for_catalog(ordered_catalog),
                    ordered_catalog=ordered_catalog,
                    model_allowed_tool_names={
                        str(tool.get("function", {}).get("name") or "")
                        for tool in ordered_catalog
                        if str(tool.get("function", {}).get("name") or "") in MODEL_TOOL_NAMES
                    },
                )
            except ToolPolicyError as exc:
                # A rejected call still gets one paired tool result so the next
                # provider request sees a valid assistant/tool batch.  Do not log
                # model-controlled arguments: they may themselves contain secrets.
                from .turn_performance import increment_counter

                increment_counter(engine, "model_tool_rejected_count")
                output = denied_tool_result(exc)
                executed_tools.append(
                    {
                        "name": raw_name,
                        "args": {},
                        "output": output,
                        "policy_denied": exc.code,
                    }
                )
                engine.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    }
                )
                log_error(f"工具策略拒绝: name={raw_name} reason={exc.code}")
                continue

            fingerprint = json.dumps(
                {"name": name, "args": args},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            reused = name in _NON_REPEATABLE_CHECKS and fingerprint in prior_checks
            if reused:
                output = prior_checks[fingerprint]
            else:
                try:
                    with (
                        _performance_span(engine, "tool_execution"),
                        execution_snapshot(engine, snapshot),
                    ):
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
                    log_error(f"工具 {name} 执行异常: {type(exc).__name__}")
                    output = "[错误] 工具执行失败，请检查参数后重试"
                if name in _NON_REPEATABLE_CHECKS:
                    prior_checks[fingerprint] = output
            executed_tools.append({"name": name, "args": args, "output": output})
        ledger = getattr(engine, "_turn_mutations", None)
        if ledger is not None and not reused:
            ledger.record_tool(name, args, output)
        # 存入历史前剥离 base64 图片投递载荷：WS 投递在工具执行期间已完成，
        # 模型读不了图片，data URI 留在历史里只是每轮重复发送的死重。
        from .tool_aux_handlers import strip_asset_payloads

        engine.messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": strip_asset_payloads(output)}
        )
        if name == "load_skill":
            # load_skill 的工具结果即 Skill 内容注入点，登记 skill 溯源。
            note_load_skill_result(engine, engine.messages[-1], output)
        log_tool(name, args)
        executed_call_names.append(name)

        if not reused and name in (
            "skill_check",
            "dice_roll",
            "dice_roll_advantage",
            "dice_roll_disadvantage",
        ):
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
    for name in dict.fromkeys(executed_call_names):
        engine._maybe_hint_optional_skill(name)

    # 工具结果之后会发起一次新的模型调用。此时最靠近模型的是 tool 消息，
    # 首轮玩家消息末尾的发言格式契约容易被长工具输出稀释，因此在合法的
    # tool-response 批次结束后重新锚定协议。它是引擎控制指令，不是玩家台词。
    engine.messages.append(
        {
            "role": "user",
            "content": (
                "[引擎控制指令｜非玩家发言] 基于以上工具结果继续完成本轮叙述；"
                "不要复述工具调用过程。所有 NPC 直接引语必须逐段使用 "
                "【npc:<npc_public_state 中的 id>】…【/npc】 包裹，短句、寒暄和"
                "同一人物再次开口也不得省略标签；旁白和动作不加标签。"
            ),
        }
    )

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
    if state.get("tool_round", 0) < MAX_TOOL_ROUNDS:
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


def _parse_final_narrative(
    engine: Any, state: TurnState, narrative: str
) -> tuple[list[Segment], str]:
    """Keep trusted prelude ownership frozen; infer speakers only in model prose."""
    prefix = state.get("authored_prefix", "")
    frozen = state.get("authored_segments", [])
    if prefix and narrative.startswith(prefix) and isinstance(frozen, list):
        prefix_segments = [
            Segment(
                kind=str(item.get("kind") or "narration"),
                text=str(item.get("text") or ""),
                npc_id=str(item.get("npc_id") or "") or None,
            )
            for item in frozen
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        suffix_segments, clean_suffix = parse_speaker_segments(
            narrative[len(prefix) :],
            is_valid_npc=getattr(engine, "is_valid_npc_id", None) or (lambda _npc_id: False),
            on_unknown_npc=getattr(engine, "log_unknown_npc_speaker", None),
            speaker_aliases=(getattr(engine, "npc_speaker_aliases", lambda: {})()),
            player_text=state.get("user_content"),
            present_npc_ids=current_scene_npc_ids(engine),
        )
        return (
            prefix_segments + suffix_segments,
            state.get("authored_clean_prefix", "") + clean_suffix,
        )
    return parse_speaker_segments(
        narrative,
        is_valid_npc=getattr(engine, "is_valid_npc_id", None) or (lambda _npc_id: False),
        on_unknown_npc=getattr(engine, "log_unknown_npc_speaker", None),
        speaker_aliases=(getattr(engine, "npc_speaker_aliases", lambda: {})()),
        player_text=state.get("user_content"),
        present_npc_ids=current_scene_npc_ids(engine),
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

    # 【npc:id⟧ 发言标签权威解析：干净文本入消息历史与记录，
    # 段结构（含发言者）持久化并推送给前端做发言单元渲染。
    narrative_segments, narrative = _parse_final_narrative(engine, state, narrative)
    segment_dicts = [s.to_dict() for s in narrative_segments]
    if state.get("opening_turn") and not narrative.strip():
        log_error("开场失败：模型未生成任何叙述")
        raise RuntimeError("开场模型未生成任何叙述")
    commit_memory = getattr(engine, "_commit_npc_conversations", None)
    if commit_memory and segment_dicts:
        commit_memory(segment_dicts)

    if narrative.strip():
        engine.messages.append({"role": "assistant", "content": narrative.strip()})
    else:
        log_error("空回合：模型未生成任何叙述或工具调用")
        engine.cb.on_error("守秘人陷入了沉思……")
        if getattr(engine, "_capacity_rejected_turn", False):
            pre_len = state.get("pre_turn_message_len")
            if isinstance(pre_len, int) and 0 <= pre_len < len(engine.messages):
                del engine.messages[pre_len:]

    if narrative.strip():
        with _performance_span(engine, "entity_reconcile"):
            engine._reconcile_narrative_entities(narrative)
        if (
            ENABLE_TURN_AUDIT
            and state.get("user_content")
            and not state.get("skip_model_audit")
            and engine._turn_needs_model_audit(
                state.get("executed_tools", []),
                player_action=state.get("user_content") or "",
                narrative=narrative,
            )
        ):
            with _performance_span(engine, "model_audit"):
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
            narrative_segments=segment_dicts,
            player_followups=list(state.get("player_followups", [])),
        )
    if segment_dicts and getattr(engine.cb, "on_narrative_segments", None):
        engine.cb.on_narrative_segments(segment_dicts)
    engine._last_turn_high_risk = state.get("turn_had_check", False)
    engine._round_count += 1
    engine.__dict__.pop("_turn_user_content", None)
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
    graph.add_conditional_edges(
        "prepare_turn",
        _route_after_prepare,
        {
            "call_story_agent": "call_story_agent",
            "call_combat_agent": "call_combat_agent",
            "finalize": "finalize",
        },
    )
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
