"""CoC pacing: goals survive recovery, clocks need reasons, duplicate SAN is quiet."""

import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from src.app.agent_graph import _emit_sanity_dice
from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.action_adjudication import (
    ProposalValidationError,
    adjudication_schema,
    apply_adjudicated_effects,
    validate_proposal,
)
from src.gameplay.action_resolution import plan_player_action
from src.gameplay.adjudication_policy import (
    check_conditions,
    policy_instruction,
    recent_goal_checks,
)
from tests.test_action_adjudication import world
from tests.test_rule_authority_regressions import MemoryStore

INPUT = "继续问守卫刚才那件事。"


def test_fallback_negation_is_scoped_to_the_action_clause():
    from src.gameplay.action_checks import infer_action_check

    state = world()
    assert infer_action_check("不搜查房间", state) is None
    result = infer_action_check("不搜查房间，先包扎伤口", state)
    assert result and result.skill == "first_aid"


def plan(**updates):
    return {
        "intent": "interact", "input_quote": "问守卫", "approach": "继续问守卫",
        "goal_id": "guard_information", "goal": "让守卫交代值班安排",
        "check": {"skill": "persuade", "reason": "守卫不愿透露", "obstacle": "守卫受保密要求约束"},
        "success_description": "守卫交代值班安排", "failure_description": "守卫拒绝透露",
        **updates,
    }


def prior(state, *, success=True):
    state["pc"]["_check_history"] = [{
        "check_id": "prior", "scene_id": "hall", "target_id": "guard",
        "skill": "persuade", "approach": "先前交谈", "success": success,
        "goal_id": "guard_information", "goal": "让守卫交代值班安排",
        "condition_snapshot": check_conditions(state),
    }]


def validate(raw, state, content=INPUT, dialogue=None):
    return validate_proposal(raw, content, state, plan_player_action(content, state), dialogue)


def test_resolved_goal_cannot_be_rerolled_by_changing_skill_or_wording():
    state = world()
    prior(state)
    raw = plan(check={"skill": "intimidate", "reason": "换个口气", "obstacle": "守卫仍不愿说"})
    with pytest.raises(ProposalValidationError) as error:
        validate(raw, state)
    assert error.value.code == "goal_already_resolved"
    # Continue the same interaction by keeping the established result, without dice.
    continued = validate(plan(check=None), state)
    assert json.loads(continued.adjudication_json)["check"] is None


def test_failed_goal_cannot_be_retried_for_free():
    state = world()
    prior(state, success=False)
    with pytest.raises(ProposalValidationError) as error:
        validate(plan(), state)
    assert error.value.code == "goal_requires_push"


def test_genuinely_new_situation_allows_new_check_but_fabrication_does_not():
    state = world()
    prior(state)
    raw = plan(changed_conditions="新守卫持枪赶到", change_evidence_quote="另一名守卫持枪赶到")
    with pytest.raises(ProposalValidationError, match="局势变化"):
        validate(raw, state)
    state["current_scene"]["npcs_present"].append("new_guard")
    assert validate(raw, state, dialogue=[{"role": "assistant", "content": "另一名守卫持枪赶到。"}])


def test_reusing_the_same_changed_condition_does_not_reset_goal_again():
    state = world()
    prior(state)
    state["pc"]["_check_history"][0]["changed_conditions"] = "新守卫持枪赶到"
    with pytest.raises(ProposalValidationError):
        validate(plan(changed_conditions="新守卫持枪赶到", change_evidence_quote="另一名守卫持枪赶到"),
                 state, dialogue=[{"role": "assistant", "content": "另一名守卫持枪赶到。"}])


def test_new_goal_and_different_scene_are_independent():
    state = world()
    prior(state)
    assert validate(plan(goal_id="guard_second_issue", goal="请求查看通行证"), state)
    state["current_scene"]["id"] = "university"
    assert validate(plan(), state)


def test_empty_obstacle_is_not_a_reason_for_new_contract_check():
    raw = plan()
    raw["check"]["obstacle"] = ""
    with pytest.raises(ProposalValidationError) as error:
        validate(raw, world())
    assert error.value.code == "missing_check_stakes"


def test_clock_requires_a_declared_trigger_and_regular_dialogue_changes_nothing():
    state = world()
    effect = {"kind": "clock_advance", "target": "pressure", "evidence_quote": "问守卫",
              "reason": "本轮结束", "clock_trigger": "完成一轮"}
    with pytest.raises(ProposalValidationError) as error:
        validate(plan(check=None, on_success=[effect]), state)
    assert error.value.code == "ungrounded_clock_trigger"
    effect.update(clock_trigger="暴露", reason="主动向敌对守卫暴露身份")
    assert validate(plan(check=None, on_success=[effect]), state)
    engine = NS(context=NS(world_store=MemoryStore(state)))
    result = apply_adjudicated_effects(engine, validate(plan(check=None), state), None)
    assert engine.context.world_store.load()["case_clocks"]["pressure"] == 0
    assert not any(e["type"] == "clock_advance" for e in result["events"])


def test_executed_goal_is_persistent_and_available_after_state_recovery():
    state = world()
    prior(state)
    state["pc"]["_check_history"][0].pop("goal_id")
    engine = NS(context=NS(world_store=MemoryStore(state)))
    apply_adjudicated_effects(engine, validate(plan(), state), {"check_id": "prior", "success": True})
    recovered = json.loads(json.dumps(engine.context.world_store.load()))
    assert recent_goal_checks(recovered)[0]["goal_id"] == "guard_information"
    assert recent_goal_checks(recovered)[0]["outcome"] == "守卫交代值班安排"
    with pytest.raises(ProposalValidationError, match="已经成功"):
        validate(plan(), recovered)
    assert "goal_id" not in state["pc"]["_check_history"][0]  # original snapshot intact


def test_skill_is_loaded_from_frozen_world_pin_without_disk_fallback():
    engine = NS(_skill_pins_cache={"keeper.adjudication": NS(content="冻结的裁决规则")})
    assert policy_instruction(engine) == "冻结的裁决规则"
    assert policy_instruction(NS(_skill_pins_cache=False)) == ""


def test_new_schema_requires_explicit_goal_and_obstacle():
    schema = adjudication_schema(world())
    assert {"goal", "goal_id"} <= set(schema["required"])
    assert "obstacle" in schema["$defs"]["CheckProposal"]["required"]
    assert "guard" in schema["properties"]["target_npc_id"]["enum"]
    assert "guest_biao" not in schema["properties"]["target_npc_id"]["enum"]


def test_goal_survives_real_save_load(tmp_path, monkeypatch):
    from src.storage.persistence import load_game_artifacts

    monkeypatch.setattr("src.app.engine.API_KEY", "test-key")
    ctx = RuntimeContext.create("goal-recovery", "猩红文档", project_root=PROJECT_ROOT, runtime_root=tmp_path)
    state = world()
    prior(state)
    state["pc"]["_check_history"][0]["outcome"] = "守卫交代值班安排"
    ctx.world_store.restore(state)
    with patch("src.structured.engine_gate.OpenAI", return_value=NS()):
        engine = GameEngine(ctx)
    engine.prepare_session()
    engine.messages.append({"role": "assistant", "content": "守卫已经交代值班安排。"})
    saved = engine.save("slot_001")
    def forget_checks(state):
        state["pc"].pop("_check_history")

    ctx.world_store.update(forget_checks)
    assert engine.load("slot_001") is not None
    restored = ctx.world_store.load()
    assert recent_goal_checks(restored)[0]["goal_id"] == "guard_information"
    # The persisted snapshot, not a generated summary, owns goal continuity.
    _messages, snapshot, _metadata = load_game_artifacts("slot_001", context=ctx)
    assert snapshot["pc"]["_check_history"][0]["goal_id"] == "guard_information"
    assert saved


@pytest.mark.parametrize("emitter", [_emit_sanity_dice, GameEngine._emit_sanity_result])
def test_duplicate_sanity_result_is_not_a_second_visible_roll(emitter):
    seen = []
    host = NS(cb=NS(on_dice=lambda *args: seen.append(args)))
    result = {"san_roll": 84, "san_before": 70, "actual_loss": 4, "san_check_success": False}
    emitter(host, json.dumps(result))
    emitter(host, json.dumps({**result, "san_before": 66, "actual_loss": 0, "duplicate": True}))
    assert len(seen) == 1
    # A real successful roll with zero loss must still be shown.
    emitter(host, json.dumps({**result, "san_roll": 20, "actual_loss": 0, "san_check_success": True}))
    assert len(seen) == 2


# ------------------------------------------------------------ 后备链与模型裁决的同一份裁决


def stub_engine(state, adjudication=None):
    engine = NS(
        context=NS(world_store=MemoryStore(state)),
        _action_resolution=NS(adjudication_json=adjudication),
        _turn_diagnostics=[],
        cb=NS(on_dice=lambda *args: None, on_decision=lambda decision: decision["default_option"]),
        tool_calls=[],
    )

    def execute(name, args):
        engine.tool_calls.append((name, args))
        return json.dumps({"ok": True, "check_id": "stub-roll", "success": True, "skill": args.get("skill")})

    engine._execute_tool = execute
    return engine


def rolls(engine):
    return [args for name, args in engine.tool_calls if name == "skill_check"]


@pytest.mark.parametrize("content,goal_id,goal", [
    ("我压低声音喝问：门外是谁？", "ask_identity", "问清门外人的身份"),
    ("我再看一遍手里那封已经拿到的信。", "reread_letter", "重读已取得的信"),
    ("我把桌上那页纸收好放进内袋。", "stow_page", "收起已拿到的纸页"),
])
def test_everyday_authorized_actions_complete_without_a_check(content, goal_id, goal):
    """日常可直接完成：问身份、看已拿到的信、收纸页，不因场景紧张自动检定。"""
    raw = plan(check=None, goal_id=goal_id, goal=goal, input_quote=content[:6], approach=content)
    resolution = validate(raw, world(), content=content)
    assert json.loads(resolution.adjudication_json)["check"] is None


def test_adjudicated_no_check_is_not_hijacked_by_skill_keywords():
    """模型已明确裁决不检定时，"搜查"等关键词不得再补骰；差异记入诊断。"""
    from src.gameplay.action_checks import resolve_action_check

    adjudication = json.dumps(plan(check=None), ensure_ascii=False)
    engine = stub_engine(world(), adjudication)
    assert resolve_action_check(engine, "我仔细搜查书桌的每一个抽屉。") is None
    assert rolls(engine) == []
    assert engine._turn_diagnostics[-1]["status"] == "adjudicated_no_check"
    assert engine._turn_diagnostics[-1]["deterministic_skill"] == "spot_hidden"


def test_module_required_check_survives_an_adjudicated_no_check():
    """规则明确要求检定时仍执行：模组 requires_success 不被 check=null 取消。"""
    from src.gameplay.action_checks import resolve_action_check

    adjudication = json.dumps(plan(check=None), ensure_ascii=False)
    engine = stub_engine(world(), adjudication)
    result = resolve_action_check(engine, "在追兵撞门时寻找暗格。", preferred_skill="spot_hidden")
    assert result["check_id"] == "stub-roll"
    assert len(rolls(engine)) == 1
    assert engine._turn_diagnostics[-1]["status"] == "module_rule_requires_check"
    assert engine._turn_diagnostics[-1]["preferred_skill"] == "spot_hidden"


def test_deterministic_fallback_covers_an_adjudication_gap():
    """模型漏判（无冻结方案）时后备按既有规则补骰，且只补一次。"""
    from src.gameplay.action_checks import resolve_action_check

    engine = stub_engine(world())
    result = resolve_action_check(engine, "我仔细搜查书桌的每一个抽屉。")
    assert result["check_id"] == "stub-roll"
    assert len(rolls(engine)) == 1
    assert engine._turn_diagnostics[-1]["status"] == "deterministic_fallback"
    assert engine._turn_diagnostics[-1]["deterministic_skill"] == "spot_hidden"


def test_failed_goal_retry_with_another_skill_is_still_the_same_goal():
    """失败换技能仍属同一目标：skill 字段不改变 goal_id 判重。"""
    state = world()
    prior(state, success=False)
    raw = plan(check={"skill": "intimidate", "reason": "改威胁", "obstacle": "守卫仍不愿说"})
    with pytest.raises(ProposalValidationError) as error:
        validate(raw, state)
    assert error.value.code == "goal_requires_push"


def test_explicit_push_is_the_only_paid_retry_channel():
    """明确孤注一掷：引用已有 push_context、风险已公布、玩家明确请求才放行。"""
    state = world()
    prior(state, success=False)
    state["pc"]["_push_contexts"] = {"retry-1": {
        "skill": "persuade", "target_id": "guard", "scene_id": "hall",
        "approach": "先前交谈", "push_risk": "守卫叫来增援", "used": False,
    }}
    pushed = plan(
        approach="换一套说辞，暗示知道他的把柄",
        input_quote="换套说辞",
        check={"skill": "persuade", "reason": "守卫仍不愿说", "obstacle": "保密约束",
               "push_context_id": "retry-1"},
        target_npc_id="guard",
    )
    assert validate(pushed, state, content="我换套说辞再试一次。")
    # 玩家没有明确请求时，同样的方案不得当成孤注一掷执行。
    with pytest.raises(ValueError, match="孤注一掷需要玩家明确请求"):
        validate(pushed, state, content="我换套说辞，再跟守卫谈谈。")
    # 风险已被消耗的上下文不能二次孤注一掷。
    reused = world()
    prior(reused, success=False)
    reused["pc"]["_push_contexts"] = {"retry-1": {
        **state["pc"]["_push_contexts"]["retry-1"], "used": True,
    }}
    with pytest.raises(ValueError, match="已使用孤注一掷"):
        validate(pushed, reused, content="我换套说辞再试一次。")


def test_push_is_never_available_for_sanity_luck_or_combat_rolls():
    """SAN、幸运与战斗检定不能孤注一掷；战斗状态下一切推骰关闭。"""
    from src.gameplay.check_context import can_push_skill, validate_push

    for skill in ("sanity", "san", "luck", "dodge", "cthulhu_mythos", "fighting_brawl", "firearms_handgun"):
        assert not can_push_skill(skill)
    assert can_push_skill("persuade")
    state = world()
    state["combat_state"] = {"active": True}
    state["pc"]["_push_contexts"] = {"retry-1": {
        "skill": "persuade", "target_id": "", "scene_id": "hall",
        "approach": "先前交谈", "push_risk": "暴露", "used": False,
    }}
    with pytest.raises(ValueError, match="不能孤注一掷"):
        validate_push(state, "persuade", "retry-1", "全新做法", "")


def test_compound_action_rolls_once_at_the_earliest_blocking_point():
    """潜行→开锁→搜查复合链只结算最早真正阻塞后续的风险点，不拆成多个骰子。"""
    from src.gameplay.action_checks import infer_action_check

    state = world()
    assert infer_action_check("我先潜行靠近书房门，撬开门锁后彻底搜查书桌。", state).skill == "stealth"
    assert infer_action_check("我撬开门锁，然后彻底搜查书桌。", state).skill == "locksmith"
    # 前面的无风险小动作（收纸页、走位）直接完成，不产生额外骰子。
    assert infer_action_check("我收好纸页，走到书桌前，仔细搜查抽屉。", state).skill == "spot_hidden"


def test_combat_intent_bypasses_goal_continuity_but_carries_no_check():
    """战斗攻击按战斗规则逐次结算：不受调查目标判重阻挡，也不得自带检定。"""
    state = world()
    prior(state, success=False)
    state["combat_state"] = {"active": True, "participants": []}
    raw = plan(intent="combat", check=None, goal="击退守卫", approach="举枪瞄准守卫")
    assert validate(raw, state, content="我问守卫同伙在哪，同时举枪。")
    armed = plan(intent="combat", goal_id="fight_back", goal="击退守卫", approach="举枪瞄准")
    with pytest.raises(ValueError, match="战斗"):
        validate(armed, state, content="我问守卫同伙在哪，同时举枪。")
