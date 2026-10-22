import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.action_adjudication import (
    adjudication_context,
    apply_adjudicated_effects,
    validate_proposal,
)
from src.gameplay.action_resolution import ActionPhase, plan_player_action
from tests.model_fixtures import build_chunk
from tests.test_action_adjudication import response, world
from tests.test_rule_authority_regressions import MemoryStore

INPUT = "先去大学吧"
QUESTION = "护卫同行，还是留在大厅？"


def preparing(content=INPUT):
    return {"intent": "prepare_move", "input_quote": content, "approach": "准备去大学，先确定同行安排",
            "destination_scene_id": "university", "destination_reason": "玩家选择去大学",
            "departure_question": QUESTION, "npc_direction": "护卫询问是否同行，等玩家回答。"}


def moving(content="你留在这里，我自己去大学。"):
    return {"intent": "move", "input_quote": content, "approach": "独自去大学",
            "destination_scene_id": "university", "destination_reason": "玩家解决同行安排并决定出发",
            "departure_question": "", "time_minutes": 35}


@pytest.mark.parametrize("mutation", [
    {"intent": "move"}, {"departure_question": ""}, {"time_minutes": 35},
    {"check": {"skill": "persuade", "reason": "不应执行检定"}},
    {"on_success": [{"kind": "npc_reaction", "target": "guard", "value": "cooperative", "evidence_quote": "大学", "reason": "未执行"}]},
    {"destination_scene_id": "hall"}, {"destination_scene_id": "unknown"},
])
def test_pending_decision_cannot_coexist_with_movement_or_effects(mutation):
    state = world()
    with pytest.raises(ValueError):
        validate_proposal(preparing() | mutation, INPUT, state, plan_player_action(INPUT, state))


def test_departure_only_records_non_executable_intention():
    state = world()
    resolution = validate_proposal(preparing(), INPUT, state, plan_player_action(INPUT, state))
    assert resolution.phase == ActionPhase.DEPARTURE
    assert resolution.destination_scene_id is None
    assert resolution.pending_destination_scene_id == "university"
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))
    result = apply_adjudicated_effects(engine, resolution, None)
    held = engine.context.world_store.load()
    assert held["current_scene"] == state["current_scene"]
    assert held.get("world_clock") is None
    assert held["case_clocks"] == state["case_clocks"]
    assert result["status"] == "not_executed" and result["events"] == []
    assert adjudication_context(held, "为什么？", plan_player_action("为什么？", held))["pending_departure"]["question"] == QUESTION


def make_engine(tmp_path, monkeypatch, plans, narratives):
    monkeypatch.setattr("src.app.engine.API_KEY", "configured-test-key")
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    context = RuntimeContext(PROJECT_ROOT, tmp_path, "departure-regression", "mansion_of_madness").ensure_initialized()
    context.world_store.restore(world())
    plans = iter(plans)
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        return response(next(plans))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch("src.structured.engine_gate.OpenAI", return_value=client):
        engine = GameEngine(context)
    engine._retrieve_lore_context = lambda *_: None
    engine._detect_content_skill_hint = lambda *_: None
    engine._maybe_summarize_after_turn = lambda: None
    outputs = iter(narratives)
    engine._stream_llm = lambda *_a, **_k: (next(outputs), [])
    return engine, seen


def test_full_turn_waits_through_followup_then_moves_in_separate_turn(tmp_path, monkeypatch):
    question_input = "你为什么想跟来？"
    question_plan = {"intent": "clarify", "input_quote": question_input, "approach": "询问同行原因",
                     "npc_direction": "护卫只解释原因，不能替玩家选择。"}
    departure = moving()
    engine, seen = make_engine(tmp_path, monkeypatch,
        [preparing(), question_plan, departure],
        ["【npc:guard】我可以同行，也能留守。请您决定。【/npc】",
         "【npc:guard】我熟悉大学附近的街道，但是否同行由您决定。【/npc】",
         "你独自乘车抵达大学。"])
    engine.handle_action(INPUT)
    first_id = engine.turn_journal.latest_completed_id()
    first = engine.turn_journal.read(first_id)
    assert engine.turn_journal.public_history()[0]["narrative_format"] == "dialogue_v1"
    assert engine.turn_journal.recovery_status(first_id)["requested"]["narrative_format"] == "dialogue_v1"
    assert engine.context.world_store.load()["current_scene"]["id"] == "hall"
    assert engine.context.world_store.load()["current_scene"]["npcs_present"] == ["guard"]
    assert "移动尚未执行" in engine.messages[-2]["content"]
    engine.handle_action(question_input)
    state = engine.context.world_store.load()
    assert state["current_scene"]["id"] == "hall"
    assert state["pending_departure"]["question"] == QUESTION
    assert (state.get("world_clock") or {}).get("elapsed_minutes", 0) == 0
    assert len(seen) == 2  # No background continuation while waiting.
    engine.handle_action(departure["input_quote"])
    state = engine.context.world_store.load()
    assert state["current_scene"]["id"] == "university"
    assert state["world_clock"]["elapsed_minutes"] == 35
    assert "pending_departure" not in state
    last_id = engine.turn_journal.latest_completed_id()
    assert first_id != last_id
    assert "抵达" not in first["narrative"]
    assert "抵达大学" in engine.turn_journal.read(last_id)["narrative"]


def test_cancel_clears_pending_plan_without_moving(tmp_path, monkeypatch):
    cancellation = "算了，先不去。"
    cancel = {"intent": "decline", "input_quote": cancellation, "approach": "取消去大学的计划"}
    engine, _ = make_engine(tmp_path, monkeypatch, [preparing(), cancel], ["护卫等你决定。", "你决定先留在大厅。"])
    engine.handle_action(INPUT)
    engine.handle_action(cancellation)
    state = engine.context.world_store.load()
    assert state["current_scene"]["id"] == "hall"
    assert "pending_departure" not in state
    assert (state.get("world_clock") or {}).get("elapsed_minutes", 0) == 0


def test_move_without_pending_decision_arrives_immediately(tmp_path, monkeypatch):
    engine, _ = make_engine(tmp_path, monkeypatch, [moving(INPUT)], ["你乘车抵达大学。"])
    engine.handle_action(INPUT)
    assert engine.context.world_store.load()["current_scene"]["id"] == "university"


def test_move_receipt_records_origin_destination_presence_and_turn_scope(tmp_path, monkeypatch):
    """移动回执：出发地、目的地、是否执行、实际在场人物、本轮范围、回合标识。"""
    engine, _ = make_engine(tmp_path, monkeypatch, [moving(INPUT)], ["你乘车抵达大学。"])
    engine.handle_action(INPUT)
    turn_id = engine.turn_journal.latest_completed_id()
    mutations = engine.turn_journal.read(turn_id)["diagnostics"]["mutations"]
    receipts = [entry for entry in mutations if entry.get("name") == "scene_transition"]
    assert len(receipts) == 1
    details = receipts[0]["details"]
    state = engine.context.world_store.load()
    assert details["executed"] is True
    assert details["origin_scene_id"] == "hall"
    assert details["destination_scene_id"] == "university"
    assert details["present_npc_ids"] == list(state["current_scene"]["npcs_present"])
    assert details["turn_id"] == turn_id
    assert details["turn_scope"] == "arrival_only"


def test_refused_move_records_not_executed_receipt_with_reason(tmp_path, monkeypatch):
    """结算被拒的移动同样留回执：executed=False 加可诊断的未执行原因，世界不移动。"""
    from src.gameplay.turn_mutations import TurnMutationLedger

    engine, _ = make_engine(tmp_path, monkeypatch, [], [])
    engine._turn_mutations = TurnMutationLedger()
    engine._active_turn_id = "turn-refused"
    assert engine._resolve_scene_transition("去一个不存在的地方。", destination_scene_id="nowhere") is None
    receipts = [entry for entry in engine._turn_mutations.entries if entry["name"] == "scene_transition"]
    assert len(receipts) == 1
    details = receipts[0]["details"]
    assert details["executed"] is False
    assert details["reason"] == "destination_not_in_catalog"
    assert details["origin_scene_id"] == "hall"
    assert details["destination_scene_id"] == "nowhere"
    assert details["turn_id"] == "turn-refused"
    assert engine.context.world_store.load()["current_scene"]["id"] == "hall"


def test_origin_speaker_farewell_allowed_after_movement(tmp_path, monkeypatch):
    """抵达回合放行出发地人物的简短送别台词：移动已在叙事前结算，
    当前场景是目的地，送行角色属于出发场景，台词合法且回合正常完成。"""
    engine, _ = make_engine(tmp_path, monkeypatch, [], [])
    del engine._stream_llm

    def create(**kwargs):
        if not kwargs.get("stream"):
            return response(moving(INPUT))
        raw = {"segments": [{"kind": "speech", "speaker_id": "guard", "text": "路上小心。"}]}
        return iter([build_chunk({"content": json.dumps(raw)}), build_chunk({"finish_reason": "stop"})])

    engine.client.chat.completions.create = create
    engine.handle_action(INPUT)
    state = engine.context.world_store.load()
    assert state["current_scene"]["id"] == "university"
    assert state["world_clock"]["elapsed_minutes"] == 35


def test_unrelated_speaker_after_movement_rolls_back_uncommitted_scene(tmp_path, monkeypatch):
    """与出发地、目的地都无关的人物台词仍被拒，未提交的场景移动照常回滚。"""
    from src.ai.model.model_streamer import ModelResponseError

    engine, _ = make_engine(tmp_path, monkeypatch, [], [])
    del engine._stream_llm

    def create(**kwargs):
        if not kwargs.get("stream"):
            return response(moving(INPUT))
        raw = {"segments": [{"kind": "speech", "speaker_id": "librarian", "text": "既不在大厅也不在大学的第三者不能说话。"}]}
        return iter([build_chunk({"content": json.dumps(raw)}), build_chunk({"finish_reason": "stop"})])

    engine.client.chat.completions.create = create
    before = engine.context.world_store.load()
    with pytest.raises(ModelResponseError):
        engine.handle_action(INPUT)
    after = engine.context.world_store.load()
    assert after["current_scene"] == before["current_scene"]
    assert (after.get("world_clock") or {}).get("elapsed_minutes", 0) == 0


def dismissal_plan():
    return {"intent": "interact", "input_quote": "让守卫回去吧", "approach": "遣散守卫",
            "on_success": [{"kind": "speaker_departure", "target": "guard",
                            "evidence_quote": "让守卫回去吧", "reason": "玩家遣散了守卫"}]}


def test_departed_this_turn_farewell_commits_departure_record(tmp_path, monkeypatch):
    """正向对照：本回合离场者的告别台词合法，回合提交后离场记录落账。
    与回滚测试共用同一计划，证明回滚断言不是空转（计划确实会落账离场）。"""
    engine, _ = make_engine(tmp_path, monkeypatch, [], [])
    del engine._stream_llm

    def create(**kwargs):
        if not kwargs.get("stream"):
            return response(dismissal_plan())
        raw = {"segments": [{"kind": "speech", "speaker_id": "guard", "text": "“那我先回去了。”"}]}
        return iter([build_chunk({"content": json.dumps(raw)}), build_chunk({"finish_reason": "stop"})])

    engine.client.chat.completions.create = create
    engine.handle_action("让守卫回去吧")
    state = engine.context.world_store.load()
    assert state["scene_speaker_presence"]["hall"]["guard"]["present"] is False
    assert state["current_scene"]["npcs_present"] == []


def test_failed_narrative_rolls_back_departure_record(tmp_path, monkeypatch):
    """叙述未通过校验时，本回合已落账的离场记录随回合一起回滚（scene 移动
    回滚之外的离场记录域断言）。"""
    from src.ai.model.model_streamer import ModelResponseError

    engine, _ = make_engine(tmp_path, monkeypatch, [], [])
    del engine._stream_llm

    def create(**kwargs):
        if not kwargs.get("stream"):
            return response(dismissal_plan())
        raw = {"segments": [{"kind": "speech", "speaker_id": "librarian", "text": "不在场的人不能说话。"}]}
        return iter([build_chunk({"content": json.dumps(raw)}), build_chunk({"finish_reason": "stop"})])

    engine.client.chat.completions.create = create
    with pytest.raises(ModelResponseError):
        engine.handle_action("让守卫回去吧")
    state = engine.context.world_store.load()
    assert "scene_speaker_presence" not in state
    assert state["current_scene"]["npcs_present"] == ["guard"]


@pytest.mark.parametrize("tool", ["state_set", "state_add_clue", "npc_reveal", "combat_start", "end_game"])
def test_narrator_cannot_bypass_departure_wait_with_game_tool(tool):
    engine = GameEngine.__new__(GameEngine)
    state = world()
    engine.context = SimpleNamespace(world_store=MemoryStore(state))
    engine._action_resolution = validate_proposal(preparing(), INPUT, state, plan_player_action(INPUT, state))
    engine._execute_tool = lambda *_: pytest.fail("pending departure attempted game effect")
    result = json.loads(engine._execute_model_tool(tool, {}))
    assert result["ok"] is False
