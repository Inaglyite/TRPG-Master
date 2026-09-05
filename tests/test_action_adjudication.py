import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.action_adjudication import (
    adjudicate_player_action,
    apply_adjudicated_effects,
    validate_proposal,
)
from src.gameplay.action_checks import infer_action_check
from src.gameplay.action_resolution import ActionPhase, plan_player_action
from src.gameplay.resolution import begin_resolution, resolution_rng, resolution_scope
from src.gameplay.turn_reconciler import apply_turn_commit, reconcile_narrative_entities
from tests.test_rule_authority_regressions import MemoryStore


def world():
    return {
        "pc": {"name": "调查员", "hp": 12, "san": 60, "attributes": {}, "skills": {"locksmith": 60, "persuade": 50}, "inventory": ["发卡"]},
        "current_scene": {"id": "hall", "name": "大厅", "npcs_present": ["guard"]},
        "scene_catalog": {"hall": {"id": "hall", "name": "大厅", "npcs_present": ["guard"]}, "university": {"id": "university", "name": "大学"}},
        "npcs": [{"id": "guard", "name": "守卫", "disposition": "guarded", "revealed": {"level": 1, "entries": []}}],
        "case_clocks": {"pressure": 0},
        "case_clock_definitions": {"pressure": {"max": 3, "advance_when": ["拖延", "暴露"]}},
        "clue_catalog": {}, "clues_found": {}, "flags": {},
    }


INPUT = "我用发卡拨动锁芯，试着把这扇门打开。"


def proposal():
    return {
        "intent": "interact", "input_quote": "用发卡拨动锁芯", "approach": "用发卡拨动锁芯",
        "check": {"skill": "locksmith", "required_success_level": "hard", "reason": "发卡不如专用工具稳定", "push_risk": "发卡折断，门锁卡死，只能寻找另一条路"},
        "time_minutes": 5, "success_description": "门锁打开", "failure_description": "锁芯未松动，守卫的脚步声接近",
        "on_failure": [{"kind": "clock_advance", "target": "pressure", "evidence_quote": "拨动锁芯", "reason": "开锁尝试拖延时间"}],
    }


def response(data):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(tool_calls=[
        SimpleNamespace(function=SimpleNamespace(name="adjudicate_action", arguments=json.dumps(data, ensure_ascii=False)))
    ]))])


def test_freeform_action_is_understood_by_model_before_roll(monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    state = world()
    assert infer_action_check(INPUT, state) is None
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return response(proposal())

    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)),
                             client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
                             raise_if_turn_cancelled=lambda: None)
    resolution = adjudicate_player_action(engine, INPUT, state, plan_player_action(INPUT, state))
    assert resolution.preferred_skill == "locksmith"
    assert json.loads(resolution.adjudication_json)["check"]["required_success_level"] == "hard"
    assert "adjudicate_action" in str(captured[0]["tool_choice"])
    assert engine.context.world_store.load() == state  # proposal alone cannot mutate


def test_conditional_effects_follow_actual_check_result():
    state = world()
    resolution = validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))
    result = apply_adjudicated_effects(engine, resolution, {"success": False})
    assert result["description"] == proposal()["failure_description"]
    assert engine.context.world_store.load()["case_clocks"]["pressure"] == 1
    assert engine.context.world_store.load()["world_clock"]["elapsed_minutes"] == 5


def test_same_scene_relocation_is_interaction_not_arrival():
    """同场景内走位（如地下室→楼上办公室）不是跨场景抵达：不得触发
    "抵达回合只允许旅行" 而禁止发现/效果，否则模型旁白越界且无法落账。"""
    state = world()
    state["clue_catalog"] = {
        "tin_box": {
            "id": "tin_box", "type": "obvious", "source": "hall",
            "discovery_rules": [{"intent": "search", "targets": ["锡盒", "锁箱"], "requires_success": False}],
        }
    }
    content = "我冲上二楼办公室，撬开那只上锁的锡盒，取出里面的东西。"
    raw = {
        "intent": "move", "input_quote": "撬开那只上锁的锡盒", "approach": "上楼撬开锡盒",
        "destination_scene_id": "hall", "discovery_refs": ["tin_box:0"],
        "success_description": "拿到盒中物品", "failure_description": "",
    }
    resolution = validate_proposal(raw, content, state, plan_player_action(content, state))
    assert resolution.phase != ActionPhase.ARRIVAL
    assert [match.clue_id for match in resolution.discovery_matches] == ["tin_box"]


def test_completion_flag_set_only_for_declared_flags():
    """flag_set 只能落定模组 completion_flags 声明的完成标记（如 cottage_searched），
    结局关键 flag（monster_defeated 等）仍由确定性阀门独占。"""
    state = world()
    state["completion_flags"] = {"cottage_searched": "完成小屋搜查"}
    content = "我把小屋里外彻底搜查了一遍，确认没有遗漏。"
    raw = {
        "intent": "interact", "input_quote": "彻底搜查了一遍", "approach": "系统性搜查小屋",
        "success_description": "搜查完成",
        "on_success": [{"kind": "flag_set", "target": "cottage_searched", "evidence_quote": "彻底搜查了一遍", "reason": "玩家完成了对小屋的搜查"}],
    }
    resolution = validate_proposal(raw, content, state, plan_player_action(content, state))
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))
    result = apply_adjudicated_effects(engine, resolution, None)
    assert engine.context.world_store.load()["flags"]["cottage_searched"] is True
    assert {"type": "flag_set", "target": "cottage_searched"} in result["events"]

    # 已落定的标记不能重复提交
    with pytest.raises(ValueError):
        validate_proposal(raw, content, engine.context.world_store.load(), plan_player_action(content, state))


def test_completion_flag_set_only_for_declared_flags_rejects_undeclared():
    state = world()
    state["completion_flags"] = {"cottage_searched": "完成小屋搜查"}
    raw = {
        "intent": "interact", "input_quote": "彻底搜查了一遍", "approach": "系统性搜查小屋",
        "success_description": "搜查完成",
        "on_success": [{"kind": "flag_set", "target": "monster_defeated", "evidence_quote": "彻底搜查了一遍", "reason": "试图越权"}],
    }
    content = "我把小屋里外彻底搜查了一遍，确认没有遗漏。"
    with pytest.raises(ValueError):
        validate_proposal(raw, content, state, plan_player_action(content, state))


@pytest.mark.parametrize("mutation", [
    {"check": {"skill": "invented", "reason": "test"}},
    {"target_npc_id": "absent"},
    {"intent": "clarify"},
    {"discovery_refs": ["unknown:0"]},
    {"on_success": [{"kind": "take_item", "target": "机枪", "source_id": "invented", "evidence_quote": "发卡", "reason": "test"}]},
    {"on_success": [{"kind": "flag_set", "target": "monster_defeated", "evidence_quote": "发卡", "reason": "test"}]},
    {"on_success": [{"kind": "clock_advance", "target": "invented", "evidence_quote": "发卡", "reason": "test"}]},
])
def test_invalid_proposals_cannot_cross_authority_boundary(mutation):
    state = world()
    data = {**proposal(), **mutation}
    with pytest.raises(ValueError):
        validate_proposal(data, INPUT, state, plan_player_action(INPUT, state))


@pytest.mark.parametrize("narrative", ["你没有进入大学。", "他警告你：不要进入大学。", "你回想起昨天进入大学的经历。", "你进入大学。"])
def test_prose_cannot_move_player_even_with_positive_arrival_wording(narrative):
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(world())), _execute_tool=lambda *_: pytest.fail("prose attempted state write"))
    reconcile_narrative_entities(engine, narrative)
    result = apply_turn_commit(engine, {"scene_id": "university", "items_add": ["凭空来的物品"], "flags_set": [{"key": "x", "value_json": "true"}]}, player_action="站在原地", narrative=narrative)
    assert result["applied"] == []
    assert engine.context.world_store.load()["current_scene"]["id"] == "hall"


def test_full_turn_executes_model_selected_difficulty_and_persists_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    context = RuntimeContext(PROJECT_ROOT, tmp_path, "adjudication-test", "mansion_of_madness").ensure_initialized()
    context.world_store.restore(world())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response(proposal()))))
    with patch("src.app.engine.OpenAI", return_value=client):
        engine = GameEngine(context)
    engine._retrieve_lore_context = lambda *_: None
    engine._detect_content_skill_hint = lambda *_: None
    engine._maybe_summarize_after_turn = lambda: None
    engine._stream_llm = lambda *_args, **_kwargs: ("你收回发卡，留意着门后的动静。", [])
    dice = []
    engine.cb.on_dice = lambda _summary, result: dice.append(result)
    engine.handle_action(INPUT)
    assert dice and dice[0]["skill"] == "locksmith"
    assert dice[0]["required_success_level"] == "hard"
    assert context.world_store.load()["world_clock"]["elapsed_minutes"] == 5
    turn_id = engine.turn_journal.latest_completed_id()
    record = engine.turn_journal.read(turn_id)
    assert record["diagnostics"]["adjudication"]["resolution_id"]
    assert "seed" not in record["diagnostics"]["adjudication"]


def test_failed_attempt_reuses_private_seed_and_frozen_plan(tmp_path):
    context = RuntimeContext(PROJECT_ROOT, tmp_path, "resolution-test", "mansion_of_madness").ensure_initialized()
    state = context.world_store.load()
    first = begin_resolution(context, state, INPUT)
    first.freeze_plan(proposal())
    with resolution_scope(first):
        values = [resolution_rng("check", "locksmith").randint(1, 100), resolution_rng("sanity", "body").randint(1, 100)]
    second = begin_resolution(context, copy.deepcopy(state), INPUT)
    assert second.id == first.id
    assert second.plan == proposal()
    with resolution_scope(second):
        assert [resolution_rng("check", "locksmith").randint(1, 100), resolution_rng("sanity", "body").randint(1, 100)] == values
    changed = {**state, "last_resolution_id": first.id}
    assert begin_resolution(context, changed, INPUT).id != first.id


def incapacitated_world():
    state = world()
    state["pc"]["hp"] = 0
    state["pc"]["conditions"] = ["dying"]
    return state


def test_incapacitated_pc_can_only_wait_or_talk():
    from src.gameplay.action_resolution import pc_incapacitated

    state = incapacitated_world()
    assert pc_incapacitated(state)
    # 确定性兜底：不产生目的地、不产生发现
    resolution = plan_player_action("我冲上楼撬开锡盒取出徽章", state)
    assert resolution.destination_scene_id is None
    assert not resolution.discovery_matches
    # 裁决校验：身体动作与效果一律拒绝
    with pytest.raises(ValueError, match="失去行动能力"):
        validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))
    # 等待/收场允许
    raw = {"intent": "wait", "input_quote": "撑住", "approach": "原地等待救援", "time_minutes": 30}
    ok = validate_proposal(raw, "我撑住，等救援。", state, plan_player_action("我撑住，等救援。", state))
    assert ok.phase == ActionPhase.INTERACTION


def test_no_take_disclaimer_blocks_acquisition_discovery():
    """A10：玩家明确"只看、不带走"时，附带取得后果的发现不得落账。"""
    from src.gameplay.discovery import disclaims_acquisition, match_discovery_rules

    assert disclaims_acquisition("我当面检查文档，不把文档带走")
    assert disclaims_acquisition("只看看，不拿")
    assert not disclaims_acquisition("我收好文档带走")
    assert not disclaims_acquisition("我检查抽屉")

    state = world()
    state["clue_catalog"] = {
        "docs": {
            "id": "docs", "type": "obvious", "source": "hall", "granted_item": "文档",
            "discovery_rules": [{"intent": "search", "targets": ["文档"], "requires_success": False}],
        }
    }
    assert match_discovery_rules("我当面检查文档，不带走", state) == []
    assert match_discovery_rules("我搜查书桌，找到文档并收好", state)

    # 裁决层同样拒绝
    raw = {
        "intent": "interact", "input_quote": "检查文档", "approach": "当面查看",
        "discovery_refs": ["docs:0"], "success_description": "看到了内容",
    }
    with pytest.raises(ValueError, match="不取得"):
        validate_proposal(raw, "我当面检查文档，不带走", state, plan_player_action("我当面检查文档，不带走", state))


def test_time_settlement_is_announced_to_narrator(tmp_path, monkeypatch):
    """A03：带时间结算的裁决结果必须向叙事模型声明已结算分钟数与跨度约束。"""
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    context = RuntimeContext(PROJECT_ROOT, tmp_path, "adjudication-time", "mansion_of_madness").ensure_initialized()
    context.world_store.restore(world())
    timed = {**proposal(), "time_minutes": 30}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response(timed))))
    with patch("src.app.engine.OpenAI", return_value=client):
        engine = GameEngine(context)
    engine._retrieve_lore_context = lambda *_: None
    engine._detect_content_skill_hint = lambda *_: None
    engine._maybe_summarize_after_turn = lambda: None
    engine._stream_llm = lambda *_args, **_kwargs: ("你花了半小时仔细拨弄锁芯。", [])
    engine.handle_action(INPUT)
    story_input = next(m["content"] for m in engine.messages if "骰前裁决已结算" in str(m.get("content")))
    assert "已结算时间 30 分钟" in story_input
    assert "不得把数小时演成数天" in story_input
    assert context.world_store.load()["world_clock"]["elapsed_minutes"] == 30
