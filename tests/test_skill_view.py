"""有效技能视图（skill_view）与裁决纠错反馈的回归。

背景：真实故障回合的角色卡缺 persuade（规则表基础值 10）；旧实现中裁决
校验与掷骰层只认卡面键，合法提案被拒绝后进入保守回退。本文件以“缺
persuade 的旧卡”构造回归（失败提案原文未留存，不断言模型当时提交了
什么）。修复后各层共用同一只读有效视图：规则表基础值补卡上缺项，卡上
显式值优先，虚构技能一律拒绝；被拒绝的提案有结构化错误码与合法候选。
"""

import json
from types import SimpleNamespace

import pytest

from src.ai.tools.registry import _roll_check
from src.gameplay.action_adjudication import (
    ProposalValidationError,
    adjudicate_player_action,
    adjudication_schema,
    validate_proposal,
)
from src.gameplay.action_resolution import ActionPhase, plan_player_action
from src.gameplay.skill_view import effective_skill_value, effective_skills
from tests.test_action_adjudication import response
from tests.test_rule_authority_regressions import MemoryStore


def test_schema_base_fills_card_gap_and_explicit_value_wins():
    pc = {"attributes": {"DEX": 55, "EDU": 70}, "skills": {"spot_hidden": 70}}
    view = effective_skills(pc)
    assert view["persuade"] == 10  # 规则表基础值补卡上缺项
    assert view["dodge"] == 27  # DEX/2 公式
    assert view["language_own"] == 70  # EDU 公式
    assert view["spot_hidden"] == 70  # 卡上显式值优先于基础值
    assert "invented_skill" not in view  # 虚构技能不进视图


def test_formula_base_fails_closed_without_attribute():
    assert effective_skill_value({"attributes": {}, "skills": {}}, "dodge") is None
    assert effective_skill_value({"attributes": {}, "skills": {}}, "persuade") == 10
    # 旧 CLI 的“未知技能默认 50”不得复活
    assert effective_skill_value({"attributes": {}, "skills": {}}, "invented") is None


def test_explicit_zero_kept_and_corrupt_explicit_value_unavailable():
    # 显式 0 是真实卡面数值，必须保留
    assert effective_skill_value({"skills": {"cthulhu_mythos": 0}}, "cthulhu_mythos") == 0
    # 只有“缺失”才能补基础值：显式损坏值让该技能不可用，绝不悄悄回退
    assert effective_skill_value({"skills": {"persuade": "bad"}}, "persuade") is None
    assert effective_skill_value({"skills": {"persuade": True}}, "persuade") is None


def test_attribute_key_wins_over_same_named_skill():
    # 与旧掷骰层一致：attributes 优先于 skills 的同名键
    pc = {"attributes": {"STR": 45}, "skills": {"STR": 99}}
    assert effective_skill_value(pc, "STR") == 45


def test_roll_uses_schema_base_for_missing_card_skill():
    from src.app.config import PROJECT_ROOT

    context = SimpleNamespace(
        project_root=PROJECT_ROOT,
        world_store=MemoryStore({"pc": {"attributes": {}, "skills": {}}}),
    )
    result = json.loads(_roll_check(context, "persuade"))
    assert result["skill_value"] == 10
    assert "d100_roll" in result


def test_roll_rejects_fabricated_skill_with_legal_candidates():
    context = SimpleNamespace(world_store=MemoryStore({"pc": {"skills": {}}}))
    result = json.loads(_roll_check(context, "invented_skill"))
    assert result["ok"] is False and result["error"] == "unknown_skill"
    assert "persuade" in result["available_skills"]


def test_structured_skill_value_uses_the_same_view():
    from src.structured.checks import _skill_value
    from src.structured.errors import StructuredError

    state = {
        "investigators": {
            "inv-a": {"skills": {"custom_card_skill": 33}, "attributes": {"STR": 45}}
        }
    }
    assert _skill_value(state, "inv-a", "persuade") == 10  # 规则表基础值补缺
    assert _skill_value(state, "inv-a", "custom_card_skill") == 33  # 卡上显式技能保留
    with pytest.raises(StructuredError) as raised:
        _skill_value(state, "inv-a", "不存在的技能")
    assert raised.value.code == "invalid_action"
    # 结构化协议只认技能键：属性键不能经由 request_check 放行
    with pytest.raises(StructuredError):
        _skill_value(state, "inv-a", "STR")


def world():
    return {
        "pc": {
            "name": "调查员",
            "hp": 12,
            "san": 60,
            "attributes": {},
            "skills": {"fast_talk": 50},
            "inventory": [],
        },
        "current_scene": {"id": "shop", "name": "铺面", "npcs_present": ["keeper_npc"]},
        "scene_catalog": {"shop": {"id": "shop", "name": "铺面", "npcs_present": ["keeper_npc"]}},
        "npcs": [
            {"id": "keeper_npc", "name": "店主", "disposition": "guarded", "revealed": {"level": 0, "entries": []}}
        ],
        "clue_catalog": {},
        "clues_found": {},
        "flags": {},
    }


CONTENT = "我说服店主说出实情。"


def persuade_proposal(skill: str = "persuade") -> dict:
    return {
        "intent": "interact",
        "input_quote": "说服店主",
        "approach": "说明来意",
        "check": {"skill": skill, "reason": "当面交涉"},
        "time_minutes": 5,
        "success_description": "店主松口",
        "failure_description": "店主拒绝",
    }


def test_card_missing_schema_skill_is_rollable_via_adjudication():
    state = world()
    resolution = validate_proposal(
        persuade_proposal(), CONTENT, state, plan_player_action(CONTENT, state)
    )
    assert resolution.preferred_skill == "persuade"


def test_fabricated_skill_raises_structured_error_with_candidates():
    state = world()
    with pytest.raises(ProposalValidationError) as raised:
        validate_proposal(
            persuade_proposal("invented"), CONTENT, state, plan_player_action(CONTENT, state)
        )
    error = raised.value
    assert error.code == "unknown_check_skill"
    assert error.field == "check.skill"
    assert error.value == "invented"
    assert {"persuade", "fast_talk"} <= set(error.candidates)
    # 错误正文不回放模型控制的原始字段值
    assert "invented" not in str(error)


def test_error_value_is_charset_sanitized():
    state = world()
    with pytest.raises(ProposalValidationError) as raised:
        validate_proposal(
            persuade_proposal("bad\nsecret-probe"), CONTENT, state, plan_player_action(CONTENT, state)
        )
    assert raised.value.value == "badsecret-probe"


def test_adjudication_schema_constrains_check_skill_to_effective_view():
    skill_schema = adjudication_schema(world())["$defs"]["CheckProposal"]["properties"]["skill"]
    assert "persuade" in skill_schema["enum"]  # 卡上缺项但规则表存在
    assert "fast_talk" in skill_schema["enum"]  # 卡上显式技能保留
    assert "invented" not in skill_schema["enum"]


def make_engine(state, payloads):
    captured = []
    queue = list(payloads)

    def create(**kwargs):
        captured.append(kwargs)
        return response(queue.pop(0))

    engine = SimpleNamespace(
        context=SimpleNamespace(world_store=MemoryStore(state)),
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        raise_if_turn_cancelled=lambda: None,
        messages=[],
        _turn_diagnostics=[],
    )
    return engine, captured


def retry_feedback(captured) -> dict:
    content = captured[-1]["messages"][-1]["content"]
    assert content.startswith("裁决未通过验证：")
    return json.loads(content.split("裁决未通过验证：", 1)[1].rsplit("。请依据", 1)[0])


def test_rejected_proposal_gets_structured_feedback_and_metadata_only_diagnostics(monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    state = world()
    engine, captured = make_engine(state, [persuade_proposal("invented"), persuade_proposal()])
    resolution = adjudicate_player_action(engine, CONTENT, state, plan_player_action(CONTENT, state))
    assert resolution.preferred_skill == "persuade"
    assert len(captured) == 2

    feedback = retry_feedback(captured)
    assert feedback["code"] == "unknown_check_skill"
    assert feedback["field"] == "check.skill"
    assert "persuade" in feedback["candidates"]

    rejected = [entry for entry in engine._turn_diagnostics if entry["status"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["proposal_meta"] == {
        "code": "unknown_check_skill",
        "field": "check.skill",
        "value": "invented",
    }
    # 公开诊断（diagnostic_report 的来源）绝不携带提案正文
    assert '"intent"' not in json.dumps(engine._turn_diagnostics, ensure_ascii=False)


def test_failed_proposal_body_only_recorded_with_explicit_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    bad = persuade_proposal("invented")
    content = CONTENT
    state = world()

    engine, captured = make_engine(state, [dict(bad), dict(bad)])
    resolution = adjudicate_player_action(engine, content, state, plan_player_action(content, state))
    # 两次都失败：保守回退，不把未通过校验的行动当作已执行
    assert resolution.phase == ActionPhase.INTERACTION
    assert json.loads(resolution.adjudication_json)["intent"] == "clarify"
    assert len(captured) == 2
    assert not (tmp_path / "private_diagnostics").exists()  # 默认不录制提案正文

    monkeypatch.setenv("TRPG_ADJUDICATION_CAPTURE", "1")
    engine, _captured = make_engine(state, [dict(bad), dict(bad)])
    engine.context.runtime_root = tmp_path
    adjudicate_player_action(engine, content, state, plan_player_action(content, state))
    capture = tmp_path / "private_diagnostics" / "adjudication-rejections.jsonl"
    assert capture.exists()
    records = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    assert records and json.loads(records[0]["proposal"])["check"]["skill"] == "invented"
    assert capture.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "private_diagnostics").stat().st_mode & 0o777 == 0o700


def test_repeat_check_refusal_is_not_retried_with_a_second_model_call(monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    state = world()
    state["pc"]["_check_history"] = [
        {
            "scene_id": "shop",
            "skill": "persuade",
            "target_id": "",
            "approach": "说明来意⟦说服店主⟧",
            "success": True,
        }
    ]
    engine, captured = make_engine(state, [persuade_proposal()])
    resolution = adjudicate_player_action(engine, CONTENT, state, plan_player_action(CONTENT, state))
    # 不可重试的业务拒绝：直接拒行落账，不消耗第二次裁决调用
    assert len(captured) == 1
    assert json.loads(resolution.adjudication_json)["intent"] == "decline"
