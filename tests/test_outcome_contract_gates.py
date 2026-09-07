"""Codex 验收后续三项的机制回归：结果状态契约（A02）、叙事一致性重写通道（A02）、
重复检定闸门（A06）、倒地救援通道（A11 后续）。"""

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
from src.gameplay.action_resolution import plan_player_action
from src.gameplay.check_context import (
    approach_fingerprint,
    record_check_outcome,
    validate_repeat_check,
)
from src.gameplay.narrative_consistency import apply_narrative_consistency, consistency_violations
from tests.test_action_adjudication import INPUT, proposal, response, world
from tests.test_rule_authority_regressions import MemoryStore


def make_engine(state):
    return SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))


# --- A02a：结果状态契约 -----------------------------------------------------


def test_decline_is_not_executed_with_reason():
    state = world()
    raw = {"intent": "decline", "input_quote": "拨动锁芯", "approach": "伤势过重，无法尝试开锁"}
    resolution = validate_proposal(raw, INPUT, state, plan_player_action(INPUT, state))
    result = apply_adjudicated_effects(make_engine(state), resolution, None)
    assert result["status"] == "not_executed"
    assert result["success"] is True  # 兼容旧消费方；语义以 status 为准
    assert result["description"] == "伤势过重，无法尝试开锁"


def test_executed_status_follows_check_result():
    state = world()
    resolution = validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))
    engine = make_engine(state)
    assert (
        apply_adjudicated_effects(engine, resolution, {"success": True})["status"]
        == "executed_success"
    )
    state = world()
    resolution = validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))
    failed = apply_adjudicated_effects(make_engine(state), resolution, {"success": False})
    assert failed["status"] == "executed_failure"
    assert failed["description"] == proposal()["failure_description"]


def test_status_contract_is_announced_to_narrator(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    context = RuntimeContext(
        PROJECT_ROOT, tmp_path, "status-contract", "mansion_of_madness"
    ).ensure_initialized()
    context.world_store.restore(world())
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response(proposal())))
    )
    with patch("src.app.engine.OpenAI", return_value=client):
        engine = GameEngine(context)
    engine._retrieve_lore_context = lambda *_: None
    engine._detect_content_skill_hint = lambda *_: None
    engine._maybe_summarize_after_turn = lambda: None
    engine._stream_llm = lambda *_args, **_kwargs: ("你拨弄锁芯。", [])
    engine.handle_action(INPUT)
    story_input = next(
        m["content"] for m in engine.messages if "骰前裁决已结算" in str(m.get("content"))
    )
    assert "[结果状态] executed_" in story_input
    assert "已结算时间 5 分钟" in story_input


# --- A02b：叙事一致性检查与重写 ----------------------------------------------


def test_consistency_violations_track_settled_minutes():
    assert consistency_violations("你又在咖啡馆里守了几天。", settled_minutes=240)
    assert not consistency_violations("你又在咖啡馆里守了几天。", settled_minutes=1500)
    assert consistency_violations("你等了几个小时。", settled_minutes=10)
    assert not consistency_violations("你等了几个小时。", settled_minutes=100)
    assert not consistency_violations("你守了几天。", settled_minutes=None)
    assert not consistency_violations("你拨弄锁芯。", settled_minutes=0)


def test_span_patterns_cover_chinese_numerals():
    """Codex 探针漏网措辞：中文数字的天/小时跨度。"""
    assert consistency_violations("你等了三天，终于等到他出现。", settled_minutes=240)
    assert consistency_violations("你等了两小时。", settled_minutes=10)
    assert consistency_violations("你盯了一个半小时。", settled_minutes=10)
    assert not consistency_violations("你等了两小时。", settled_minutes=240)
    # 将来打算与回忆不算本回合经过的时间（重写提示也允许保留）
    assert not consistency_violations("明天再走。", settled_minutes=240)
    assert not consistency_violations("他昨天来过这里。", settled_minutes=240)


def consistency_engine(outcome, reply="你守了很久。", finish="stop"):
    return SimpleNamespace(
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason=finish, message=SimpleNamespace(content=reply)
                            )
                        ]
                    )
                )
            )
        ),
        context=SimpleNamespace(world_id="w"),
        raise_if_turn_cancelled=lambda: None,
        narrative_model="test-model",
        _adjudicated_outcome=outcome,
        _turn_diagnostics=[],
    )


def test_overreaching_narrative_is_rewritten_before_finalize():
    outcome = {
        "status": "executed_success",
        "events": [{"type": "time_advanced", "before": 0, "after": 240}],
    }
    engine = consistency_engine(outcome)
    rewritten = apply_narrative_consistency(engine, "你又在咖啡馆里守了几天，终于等到接头人。")
    assert rewritten == "你守了很久。"
    assert engine._turn_diagnostics[-1]["role"] == "narrative_consistency"


def test_consistency_gate_is_fail_open():
    outcome = {
        "status": "executed_success",
        "events": [{"type": "time_advanced", "before": 0, "after": 240}],
    }
    original = "你又在咖啡馆里守了几天。"
    engine = consistency_engine(outcome, finish="length")
    assert apply_narrative_consistency(engine, original) == original
    broken = consistency_engine(outcome)
    broken.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("boom"))
            )
        )
    )
    assert apply_narrative_consistency(broken, original) == original
    clean = consistency_engine(outcome)
    clean.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: pytest.fail("无越界不得发起重写"))
        )
    )
    assert apply_narrative_consistency(clean, "你拨弄锁芯。") == "你拨弄锁芯。"


def test_overreaching_streamed_narrative_is_corrected_in_final_history(tmp_path, monkeypatch):
    """A02：流式叙事把 5 分钟演成"几天"时，定稿阶段重写一次，
    消息历史与回合记录采用修正后的文本（前端权威段同理覆盖）。"""
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    context = RuntimeContext(
        PROJECT_ROOT, tmp_path, "consistency-turn", "mansion_of_madness"
    ).ensure_initialized()
    context.world_store.restore(world())

    def create(**kwargs):
        if kwargs.get("tools"):
            return response(proposal())
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="你拨弄锁芯，几分钟里始终留意着走廊。"),
                )
            ]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch("src.app.engine.OpenAI", return_value=client):
        engine = GameEngine(context)
    engine._retrieve_lore_context = lambda *_: None
    engine._detect_content_skill_hint = lambda *_: None
    engine._maybe_summarize_after_turn = lambda: None
    engine._stream_llm = lambda *_args, **_kwargs: ("你守了几天，终于撬开了门。", [])
    engine.handle_action(INPUT)
    final = [m["content"] for m in engine.messages if m.get("role") == "assistant"][-1]
    assert final == "你拨弄锁芯，几分钟里始终留意着走廊。"
    assert any(d.get("role") == "narrative_consistency" for d in engine._turn_diagnostics)


# --- A06：重复检定闸门 --------------------------------------------------------


def record(state, *, success, approach="用发卡拨动锁芯", skill="locksmith", target=""):
    record_check_outcome(
        state,
        skill=skill,
        result={"success": success, "check_id": "r1"},
        push=False,
        push_context_id="",
        approach=approach,
        target_id=target,
        required_success_level="regular",
        push_risk="",
    )


def test_repeat_gate_blocks_same_conditions():
    state = world()
    record(state, success=True)
    with pytest.raises(ValueError, match="不再重复掷骰"):
        validate_repeat_check(state, skill="locksmith", target_id="", approach="用发卡拨动锁芯")
    record(state, success=False)
    state["pc"]["_check_history"].pop(0)  # 只留失败记录
    with pytest.raises(ValueError, match="相同做法的检定已失败"):
        validate_repeat_check(state, skill="locksmith", target_id="", approach="用发卡拨动锁芯")
    # 换做法、换场景、孤注一掷均放行
    validate_repeat_check(state, skill="locksmith", target_id="", approach="改用铁丝探入锁孔")
    state["current_scene"]["id"] = "cellar"
    validate_repeat_check(state, skill="locksmith", target_id="", approach="用发卡拨动锁芯")
    state["current_scene"]["id"] = "hall"
    validate_repeat_check(
        state, skill="locksmith", target_id="", approach="用发卡拨动锁芯", push=True
    )


def test_repeat_gate_applies_in_adjudication_validation():
    state = world()
    record(state, success=True)
    with pytest.raises(ValueError, match="不再重复掷骰"):
        validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))
    # 同一句话 + 模型改写做法标签（Codex 复现：以发卡探锁 vs 原文）：逐字锚点不变，仍拦
    relabeled = json.loads(json.dumps(proposal()))
    relabeled["approach"] = "以发卡探锁"
    with pytest.raises(ValueError, match="不再重复掷骰"):
        validate_proposal(relabeled, INPUT, state, plan_player_action(INPUT, state))
    # 玩家真正改变输入与做法：放行
    other_input = "我改用铁丝探入锁孔，试着开锁。"
    other = json.loads(json.dumps(proposal()))
    other["input_quote"] = "改用铁丝探入锁孔"
    other["approach"] = "用铁丝探入锁孔"
    other["on_failure"][0]["evidence_quote"] = "改用铁丝探入锁孔"
    validate_proposal(other, other_input, state, plan_player_action(other_input, state))


def test_repeat_gate_applies_at_execution_layer():
    from src.ai.tools.registry import _roll_check

    state = world()
    context = SimpleNamespace(world_store=MemoryStore(state), project_root=PROJECT_ROOT)
    first = json.loads(_roll_check(context, "locksmith", approach="用发卡拨动锁芯"))
    assert first.get("skill") == "locksmith"
    assert context.world_store.load()["pc"]["_check_history"][-1]["approach"] == "用发卡拨动锁芯"
    second = json.loads(_roll_check(context, "locksmith", approach="用发卡拨动锁芯"))
    assert second == {"ok": False, "error": "repeat_check", "detail": second["detail"]}
    third = json.loads(_roll_check(context, "locksmith", approach="改用铁丝探入锁孔"))
    assert third.get("skill") == "locksmith"


def test_repeat_gate_cannot_bypass_via_fallback_path():
    """Codex 复现的绕过：裁决层两次拒绝后退回确定性流程。兜底路径现在把玩家原文
    作为做法指纹传入执行层——同一句话重复必被拦（裁决的短做法被原文包含）。"""
    from src.ai.tools.registry import _roll_check

    state = world()
    context = SimpleNamespace(world_store=MemoryStore(state), project_root=PROJECT_ROOT)
    first = json.loads(_roll_check(context, "locksmith", approach="用发卡拨动锁芯"))
    assert first.get("skill") == "locksmith"
    # 兜底路径传入玩家原文：短做法被长句包含 → 同一动作，拦截
    bypass = json.loads(_roll_check(context, "locksmith", approach=INPUT))
    assert bypass["ok"] is False and bypass["error"] == "repeat_check"
    # 换了目标/做法的措辞放行
    changed = json.loads(
        _roll_check(context, "locksmith", approach="我改用铁丝拨动右边那扇门的锁芯")
    )
    assert changed.get("skill") == "locksmith"
    # 完全无做法描述的直调路径：双方皆空才算同条件
    state2 = world()
    ctx2 = SimpleNamespace(world_store=MemoryStore(state2), project_root=PROJECT_ROOT)
    assert json.loads(_roll_check(ctx2, "locksmith")).get("skill") == "locksmith"
    again = json.loads(_roll_check(ctx2, "locksmith"))
    assert again["ok"] is False and again["error"] == "repeat_check"
    # "历史做法非空→直调省略做法"不再是放行路径
    omitted = json.loads(_roll_check(context, "locksmith"))
    assert omitted["ok"] is False and omitted["error"] == "repeat_check"
    # 裁决记录带逐字锚点指纹、兜底传玩家原文：措辞无子串关系也拦得住（Codex 原文场景）
    state3 = world()
    ctx3 = SimpleNamespace(world_store=MemoryStore(state3), project_root=PROJECT_ROOT)
    seeded = json.loads(_roll_check(ctx3, "locksmith", approach="以发卡探锁⟦用发卡撬开左边的门锁⟧"))
    assert seeded.get("skill") == "locksmith"
    bypass3 = json.loads(_roll_check(ctx3, "locksmith", approach="我用发卡撬开左边的门锁。"))
    assert bypass3["ok"] is False and bypass3["error"] == "repeat_check"
    # 孤注一掷仍是合法的付费重试通道（此处无 push 上下文，应报孤注一掷错误而非重复）
    pushed = json.loads(_roll_check(ctx2, "locksmith", push=True, push_context_id="missing"))
    assert pushed["ok"] is False and pushed["error"] != "repeat_check"


def test_repeat_gate_distinguishes_targets():
    """Codex 对偶复核：同场景换目标不得误伤。双方目标已知且不同 → 放行；
    目标未知不当作同一目标（单方未知时由做法是否相同决定）。"""
    state = world()
    record(state, success=False, approach="撬锁", target="左边的门锁")
    # 换目标：放行
    validate_repeat_check(state, skill="locksmith", target_id="右边的门锁", approach="撬锁")
    # 同目标同做法：拦截
    with pytest.raises(ValueError, match="已失败"):
        validate_repeat_check(state, skill="locksmith", target_id="左边的门锁", approach="撬锁")
    # 记录目标未知、新做法目标已知：做法相同仍按重复处理（未知不等于已排除）
    state2 = world()
    record(state2, success=False, approach="撬锁", target="")
    with pytest.raises(ValueError, match="已失败"):
        validate_repeat_check(state2, skill="locksmith", target_id="左边的门锁", approach="撬锁")
    # 裁决层完整链路：check.target 参与闸门
    state3 = world()
    record(state3, success=False, approach="撬锁", target="左边的门锁")
    right = json.loads(json.dumps(proposal()))
    right["check"]["target"] = "右边的门锁"
    right["approach"] = "撬锁"
    validate_proposal(right, INPUT, state3, plan_player_action(INPUT, state3))  # 不拦
    left = json.loads(json.dumps(proposal()))
    left["check"]["target"] = "左边的门锁"
    left["approach"] = "撬锁"
    with pytest.raises(ValueError, match="已失败"):
        validate_proposal(left, INPUT, state3, plan_player_action(INPUT, state3))


def test_approach_fingerprint_shared_anchor():
    assert (
        approach_fingerprint("以发卡探锁", "我用发卡撬开左边的门锁")
        == "以发卡探锁⟦我用发卡撬开左边的门锁⟧"
    )
    assert approach_fingerprint("用发卡拨动锁芯", "用发卡拨动锁芯") == "用发卡拨动锁芯"
    assert approach_fingerprint("撬锁") == "撬锁"


def test_repeat_refusal_never_falls_back_to_rollable_path(monkeypatch):
    """Codex 复现：裁决记录做法"以发卡探锁"，玩家原文"我用发卡撬开左边的门锁"，
    两种表达无子串关系。裁决两次正确拒绝后不得退回可掷骰的确定性流程——
    重复检定拒绝与模型调用失败分流，前者按 not_executed 拒行落账。"""
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    state = world()
    # 上一回合真实执行过的检定，做法指纹带逐字锚点（模型引用了不含"我"的短语）
    record(state, success=False, approach="以发卡探锁⟦用发卡撬开左边的门锁⟧")
    content = "我用发卡撬开左边的门锁。"
    paraphrased = {
        "intent": "interact",
        "input_quote": "用发卡撬开左边的门锁",
        "approach": "以发卡探锁",
        "check": {"skill": "locksmith", "reason": "开锁"},
        "failure_description": "锁未开",
    }
    engine = SimpleNamespace(
        context=SimpleNamespace(world_store=MemoryStore(state)),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: response(paraphrased))
            )
        ),
        raise_if_turn_cancelled=lambda: None,
        _turn_diagnostics=[],
    )
    resolution = adjudicate_player_action(
        engine, content, state, plan_player_action(content, state)
    )
    plan = json.loads(resolution.adjudication_json)
    assert plan["intent"] == "decline"  # 拒行决议，而非确定性兜底
    assert not resolution.discovery_matches
    assert engine._turn_diagnostics[-1]["status"] == "repeat_refused"
    outcome = apply_adjudicated_effects(engine, resolution, None)
    assert outcome["status"] == "not_executed"
    assert "重复检定未执行" in outcome["description"]


def test_model_failure_still_falls_back(monkeypatch):
    """模型调用失败（非闸门拒绝）仍退回确定性流程——两条失败路径不混淆。"""
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    state = world()
    content = "我用发卡拨动锁芯，试着把这扇门打开。"
    engine = SimpleNamespace(
        context=SimpleNamespace(world_store=MemoryStore(state)),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: (_ for _ in ()).throw(RuntimeError("api down"))
                )
            )
        ),
        raise_if_turn_cancelled=lambda: None,
        _turn_diagnostics=[],
    )
    fallback = plan_player_action(content, state)
    resolution = adjudicate_player_action(engine, content, state, fallback)
    assert resolution is fallback
    assert engine._turn_diagnostics[-1]["status"] == "fallback"


def test_repeat_blocked_check_is_not_executed_not_success():
    """被重复闸门拦下的检定必须按 not_executed 落账：不得按"无检定即成功"
    发放成功分支效果（Codex 复现：软拒绝返回 None 被判为成功，门锁照开）。"""
    state = world()
    resolution = validate_proposal(proposal(), INPUT, state, plan_player_action(INPUT, state))
    engine = make_engine(state)
    outcome = apply_adjudicated_effects(
        engine, resolution, {"repeat_blocked": True, "detail": "相同做法的检定已失败"}
    )
    assert outcome["status"] == "not_executed"
    assert outcome["success"] is False
    assert "检定未执行" in outcome["description"]
    assert "相同做法的检定已失败" in outcome["description"]
    # Codex 对偶复核：不得夹带尚未发生的失败后果
    assert proposal()["failure_description"] not in outcome["description"]
    saved = engine.context.world_store.load()
    assert saved["case_clocks"]["pressure"] == 0  # 成败分支效果均未落账
    assert saved["world_clock"]["elapsed_minutes"] == 5  # 时间代价仍正常结算


# --- 倒地救援通道 --------------------------------------------------------------


def downed_world():
    state = world()
    state["pc"]["hp"] = 0
    state["pc"]["conditions"] = ["dying"]
    return state


def rescue_proposal(**overrides):
    raw = {
        "intent": "wait",
        "input_quote": "呼救",
        "approach": "撑住并向守卫呼救",
        "time_minutes": 30,
        "on_success": [
            {
                "kind": "rescue",
                "target": "guard",
                "evidence_quote": "呼救",
                "reason": "守卫听到呼救赶来",
            }
        ],
    }
    raw.update(overrides)
    return raw


def test_rescue_channel_recovers_downed_pc_with_cost():
    state = downed_world()
    content = "我撑住，向守卫呼救。"
    resolution = validate_proposal(
        rescue_proposal(), content, state, plan_player_action(content, state)
    )
    engine = make_engine(state)
    result = apply_adjudicated_effects(engine, resolution, None)
    saved = engine.context.world_store.load()
    assert saved["pc"]["hp"] == 1
    assert saved["pc"]["conditions"] == []
    assert saved["world_clock"]["elapsed_minutes"] == 30
    assert {"type": "rescue", "rescuer": "guard", "hp_after": 1} in result["events"]


def test_rescue_requires_incapacitated_pc():
    state = world()
    content = "我撑住，向守卫呼救。"
    with pytest.raises(ValueError, match="仅适用于失去行动能力"):
        validate_proposal(rescue_proposal(), content, state, plan_player_action(content, state))


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda s: s["current_scene"].update(npcs_present=[]), "在场的 NPC"),
        (lambda s: s["npcs"][0].update(disposition="hostile"), "不会施救"),
        (lambda s: s["pc"].update(conditions=["dead"]), "不可逆转"),
    ],
)
def test_rescue_eligibility_is_engine_validated(mutate, match):
    state = downed_world()
    mutate(state)
    content = "我撑住，向守卫呼救。"
    with pytest.raises(ValueError, match=match):
        validate_proposal(rescue_proposal(), content, state, plan_player_action(content, state))


def test_rescue_needs_time_cost_and_wait_intent():
    state = downed_world()
    content = "我撑住，向守卫呼救。"
    with pytest.raises(ValueError, match="至少 20"):
        validate_proposal(
            rescue_proposal(time_minutes=10), content, state, plan_player_action(content, state)
        )
    with pytest.raises(ValueError, match="救援应表达为 wait"):
        validate_proposal(
            rescue_proposal(intent="clarify"), content, state, plan_player_action(content, state)
        )
    # 倒地时仍不得携带检定/发现/取物
    state = downed_world()
    state["clue_catalog"] = {
        "tin": {
            "id": "tin",
            "type": "obvious",
            "source": "hall",
            "discovery_rules": [
                {"intent": "search", "targets": ["锡盒"], "requires_success": False}
            ],
        }
    }
    with pytest.raises(ValueError, match="不能产生检定或发现"):
        validate_proposal(
            rescue_proposal(discovery_refs=["tin:0"]),
            content,
            state,
            plan_player_action(content, state),
        )


def test_downed_pc_in_combat_may_wait_for_rescue():
    state = downed_world()
    state["combat_state"] = {"active": True}
    content = "我撑住，向守卫呼救。"
    resolution = validate_proposal(
        rescue_proposal(), content, state, plan_player_action(content, state)
    )
    assert resolution.adjudication_json
    body = dict(
        rescue_proposal(), intent="interact", check=None, on_success=rescue_proposal()["on_success"]
    )
    with pytest.raises(ValueError, match="失去行动能力"):
        validate_proposal(
            body,
            content,
            downed_world() | {"combat_state": {"active": True}},
            plan_player_action(content, state),
        )


def test_rescue_rejects_hostile_to_pc_despite_polite_disposition():
    """Codex 探针：社交面具 guarded 但 hostile_to_pc 已标记的 NPC 不得施救——
    与"伏击 NPC 敌意不能只看 disposition"的既有教训同一口径。"""
    state = downed_world()
    state["npcs"][0]["hostile_to_pc"] = True
    content = "我撑住，向守卫呼救。"
    with pytest.raises(ValueError, match="不会施救"):
        validate_proposal(rescue_proposal(), content, state, plan_player_action(content, state))


def test_rescue_rejects_combat_participant_hostility():
    state = downed_world()
    state["combat_state"] = {
        "active": True,
        "participants": [{"id": "guard", "hostile_to_pc": True}],
    }
    content = "我撑住，向守卫呼救。"
    with pytest.raises(ValueError, match="不会施救"):
        validate_proposal(rescue_proposal(), content, state, plan_player_action(content, state))


def test_rescue_settlement_rechecks_hostility():
    """validate 与 apply 之间敌意标记可能变化：结算层必须复核。"""
    state = downed_world()
    content = "我撑住，向守卫呼救。"
    resolution = validate_proposal(
        rescue_proposal(), content, state, plan_player_action(content, state)
    )
    hostile = downed_world()
    hostile["npcs"][0]["hostile_to_pc"] = True
    with pytest.raises(ValueError, match="敌对"):
        apply_adjudicated_effects(make_engine(hostile), resolution, None)


# --- 裁决上下文信息对称（Codex #4） --------------------------------------------


def test_adjudication_context_exposes_flags_and_eligible_endings():
    """裁决器此前看不到一般 flags/eligible_endings，会把已完成的封印/结案当成
    "不了解条件"而 clarify 拖延结局；两层现在共享同一权威视图。"""
    from src.gameplay.action_adjudication import adjudication_context

    state = world()
    state["flags"] = {"monster_defeated": True, "documents_recovered": True}
    state["endings"] = [
        {
            "id": "truth_and_seal",
            "title": "真相大白，怪物被制伏",
            "ending_type": "good",
            "required_flags": {"monster_defeated": True, "documents_recovered": True},
        }
    ]
    payload = adjudication_context(state, INPUT, plan_player_action(INPUT, state))
    assert payload["flags"]["monster_defeated"] is True
    assert payload["eligible_endings"][0]["id"] == "truth_and_seal"
