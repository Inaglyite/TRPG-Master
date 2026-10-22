"""Departure -> failed search -> subsequent turn must agree with persisted facts."""

import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from src.ai.model.dialogue_output import decode_dialogue_output, parse_dialogue_output
from src.ai.model.model_stream_helpers import _guest_context
from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.action_adjudication import apply_adjudicated_effects, validate_proposal
from src.gameplay.action_resolution import plan_player_action
from src.gameplay.adjudication_policy import check_conditions
from src.gameplay.discovery import DiscoveryMatch, _known_clue_ids
from src.gameplay.guest_speakers import guest_allowed
from src.gameplay.scene_speaker_presence import departed_speakers
from tests.test_action_adjudication import world


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app.engine.API_KEY", "test-key")
    ctx = RuntimeContext.create("scene-continuity", "猩红文档", project_root=PROJECT_ROOT, runtime_root=tmp_path)
    state = world()
    state["guest_speakers"] = {"guest_man": {"display_name": "门外男人", "scene_id": "hall"}}
    state["pc"]["skills"]["spot_hidden"] = 70
    state["clues_found"] = {"investigation": []}
    state["case_clocks"]["pressure"] = 1
    state["clue_catalog"] = {"hidden_bell": {
        "id": "hidden_bell", "source": "hall", "type": "hidden", "text": "壁炉后藏着铜钟。",
        "granted_item": "铜钟", "flag_effects": {"bell_found": True},
        "discovery_rules": [{"intent": "search", "targets": ["壁炉"], "skill": "spot_hidden",
                             "requires_success": True,
                             "fallback": {"mode": "grant_clue", "cost_clock": "pressure", "cost_amount": 1,
                                          "narrative": "搜查声暴露了你的行踪，但你终于找到了入口。"}}],
    }}
    ctx.world_store.restore(state)
    with patch("src.structured.engine_gate.OpenAI", return_value=NS()):
        result = GameEngine(ctx)
    result.prepare_session()
    return result


def match(engine):
    clue = engine.context.world_store.load()["clue_catalog"]["hidden_bell"]
    return DiscoveryMatch("hidden_bell", clue, clue["discovery_rules"][0])


FAILED = {"skill": "spot_hidden", "success": False, "check_id": "failed-search"}


def proposal():
    departure = {"kind": "speaker_departure", "target": "guest_man", "evidence_quote": "赶出去",
                 "reason": "闯入者已经服软，接受离开，后续搜查成败不影响其离场"}
    return {"intent": "interact", "input_quote": "赶出去", "approach": "赶走来人后搜查壁炉",
            "goal_id": "fireplace_search", "goal": "赶人并找到壁炉暗格",
            "discovery_refs": ["hidden_bell:0"],
            "check": {"skill": "spot_hidden", "target": "壁炉", "reason": "暗格隐蔽", "obstacle": "机关藏在砖缝"},
            "success_description": "发现暗格", "failure_description": "未找到暗格",
            "on_success": [departure], "on_failure": [departure]}


INPUT = "把他们赶出去，锁门后搜查壁炉。"


def resolution(engine):
    state = engine.context.world_store.load()
    return validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))


def test_failed_check_can_grant_authored_discovery_only_with_real_paid_cost(engine):
    discoveries = engine._resolve_discoveries([match(engine)], FAILED)
    state = engine.context.world_store.load()
    assert "hidden_bell" in _known_clue_ids(state)
    assert "铜钟" in state["pc"]["inventory"]
    assert state["case_clocks"]["pressure"] == 2
    assert discoveries[0]["fallback"]["paid_cost"] == {"clock_id": "pressure", "before": 1, "after": 2, "amount": 1}


def test_full_pressure_cannot_pay_for_free_bell_or_overflow_clock(engine):
    engine.context.world_store.update(lambda w: w["case_clocks"].update(pressure=3))
    discoveries = engine._resolve_discoveries([match(engine)], FAILED)
    state = engine.context.world_store.load()
    assert discoveries[0]["reason"] == "fallback_cost_unavailable"
    assert "hidden_bell" not in _known_clue_ids(state)
    assert "铜钟" not in state["pc"]["inventory"]
    assert not state["flags"].get("bell_found")
    assert state["case_clocks"]["pressure"] == 3


@pytest.mark.parametrize("receipt", [None, {**FAILED, "repeat_blocked": True}, {**FAILED, "skill": "listen"}])
def test_no_roll_blocked_roll_or_wrong_skill_cannot_trigger_failure_fallback(engine, receipt):
    discoveries = engine._resolve_discoveries([match(engine)], receipt)
    assert discoveries[0]["discovered"] is False
    assert engine.context.world_store.load()["case_clocks"]["pressure"] == 1
    assert "hidden_bell" not in _known_clue_ids(engine.context.world_store.load())


def test_already_discovered_clue_does_not_charge_or_grant_twice(engine):
    item = match(engine)
    engine._resolve_discoveries([item], FAILED)
    assert engine._resolve_discoveries([item], FAILED) == []
    state = engine.context.world_store.load()
    assert state["pc"]["inventory"].count("铜钟") == 1
    assert state["case_clocks"]["pressure"] == 2


def test_paid_discovery_and_departure_form_one_authoritative_outcome(engine):
    action = resolution(engine)
    def add_check(state):
        state["pc"]["_check_history"] = [{**FAILED, "scene_id": "hall", "target_id": "壁炉"}]
    engine.context.world_store.update(add_check)
    discoveries = engine._resolve_discoveries([match(engine)], FAILED)
    outcome = apply_adjudicated_effects(engine, action, FAILED, discoveries)
    assert outcome["status"] == "executed_with_cost"
    assert outcome["goal_achieved"] is True
    assert "铜钟" in outcome["description"] and "未找到" not in outcome["description"]
    state = engine.context.world_store.load()
    assert "guest_man" in departed_speakers(state)
    assert state["pc"]["_check_history"][-1]["goal_achieved"] is True


def test_fallback_cost_is_not_charged_twice_and_push_consequence_keeps_discovery(engine):
    raw = proposal()
    raw["on_failure"].append({"kind": "clock_advance", "target": "pressure", "evidence_quote": "搜查",
                              "clock_trigger": "暴露", "reason": "搜查声音暴露行踪"})
    state = engine.context.world_store.load()
    action = validate_proposal(raw, INPUT, state, plan_player_action(INPUT, state))
    discoveries = engine._resolve_discoveries([match(engine)], FAILED)
    result = apply_adjudicated_effects(engine, action, {**FAILED, "push_consequence": "工具在重试中折断了。"}, discoveries)
    assert engine.context.world_store.load()["case_clocks"]["pressure"] == 2
    assert "工具" in result["description"] and "铜钟" in result["description"]
    assert sum(e["type"] == "clock_advance" for e in result["events"]) == 1


def test_departure_survives_failed_search_and_farewell_is_only_allowed_this_turn(engine):
    outcome = apply_adjudicated_effects(engine, resolution(engine), FAILED)
    state = engine.context.world_store.load()
    assert guest_allowed(state, "guest_man", "hall") is False
    assert engine.is_valid_npc_id("guest_man")  # history can still display its identity
    raw = json.dumps({"segments": [{"kind": "speech", "speaker_id": "guest_man", "text": "“我们走。”"}]})
    parsed = parse_dialogue_output(raw)
    engine._adjudicated_outcome = outcome
    ids, _, _ = _guest_context(engine, parsed)
    assert "guest_man" in ids
    decode_dialogue_output(raw, present_npc_ids=set(), guest_speaker_ids=ids)
    engine._adjudicated_outcome = {}
    ids, _, _ = _guest_context(engine, parsed)
    with pytest.raises(ValueError):
        decode_dialogue_output(raw, present_npc_ids=set(), guest_speaker_ids=ids)
    parsed.guest_speakers = [NS(speaker_id="guest_man", display_name="门外男人")]
    with pytest.raises(ValueError, match="已离场"):
        _guest_context(engine, parsed)


def test_new_method_is_not_a_new_external_condition_even_with_matching_quote(engine):
    state = engine.context.world_store.load()
    state["pc"]["_check_history"] = [{**FAILED, "scene_id": "hall", "goal_id": "fireplace_search",
                                        "condition_snapshot": check_conditions(state)}]
    raw = proposal()
    raw.update(approach="改摸内壁", changed_conditions="改摸炉膛内壁", change_evidence_quote="改摸炉膛内壁")
    with pytest.raises(ValueError, match="换摸法"):
        validate_proposal(raw, "赶出去后改摸炉膛内壁", state, plan_player_action(INPUT, state))
    assert copy.deepcopy(state) == state


def test_forced_resurrection_without_departure_record_is_rejected(engine):
    """强行复活被拒：speaker_return 必须销掉一条真实的本场景离场记录，
    没有离场事实的"回归"在裁决验证阶段就被拒绝。"""
    raw = proposal()
    raw["on_success"] = [{"kind": "speaker_return", "target": "guard",
                          "evidence_quote": "赶出去", "reason": "伪造的回归"}]
    state = engine.context.world_store.load()
    with pytest.raises(ValueError, match="离场记录|伪造"):
        validate_proposal(raw, INPUT, state, plan_player_action(INPUT, state))


def test_evidenced_return_closes_departure_record_and_restores_speech(engine):
    """合理回归事件有证据：玩家给出真实事由后 speaker_return 落账，离场记录
    转为在场（保留事由、回合与玩家原话证据），该人物恢复现场发言资格；
    再次提交同一回归因无离场记录而被拒（幂等，不重复效果）。"""
    apply_adjudicated_effects(engine, resolution(engine), FAILED)
    state = engine.context.world_store.load()
    assert "guest_man" in departed_speakers(state)
    assert guest_allowed(state, "guest_man", "hall") is False
    content = "算了，让他回来吧，我还有话要问。"
    raw = {"intent": "interact", "input_quote": "让他回来吧", "approach": "叫回刚离开的来人",
           "goal_id": "fireplace_search", "goal": "继续处理壁炉搜查的后续",
           "on_success": [{"kind": "speaker_return", "target": "guest_man",
                           "evidence_quote": "让他回来吧", "reason": "玩家改变主意叫回了来人"}]}
    action = validate_proposal(raw, content, state, plan_player_action(content, state))
    engine._active_turn_id = "turn-return"
    outcome = apply_adjudicated_effects(engine, action, None)
    state = engine.context.world_store.load()
    assert "guest_man" not in departed_speakers(state)
    record = state["scene_speaker_presence"]["hall"]["guest_man"]
    assert record["present"] is True
    assert record["evidence"] == "让他回来吧"
    assert record["turn_id"] == "turn-return"
    assert record["reason"] == "玩家改变主意叫回了来人"
    assert guest_allowed(state, "guest_man", "hall") is True
    assert "guest_man" in state["current_scene"]["npcs_present"]
    assert {"type": "speaker_return", "target": "guest_man", "scene_id": "hall",
            "reason": "玩家改变主意叫回了来人"} in outcome["events"]
    with pytest.raises(ValueError, match="离场记录|伪造"):
        validate_proposal(raw, content, state, plan_player_action(content, state))
