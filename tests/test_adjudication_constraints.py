import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.action_adjudication import (
    adjudicate_player_action,
    adjudication_context,
    adjudication_schema,
    discovery_candidates,
    validate_proposal,
)
from src.gameplay.action_resolution import ActionPhase, plan_player_action
from tests.test_action_adjudication import response, world
from tests.test_departure_turn import INPUT, moving, preparing
from tests.test_rule_authority_regressions import MemoryStore


def test_schema_only_offers_current_legal_effect_targets():
    state = world()
    schema = adjudication_schema(state)
    Draft202012Validator.check_schema(schema)
    branches = schema["$defs"]["EffectProposal"]["anyOf"]
    by_kind = {b["properties"]["kind"]["const"]: b for b in branches}
    assert "flag_set" not in by_kind
    assert "rescue" not in by_kind
    assert by_kind["npc_reaction"]["properties"]["target"]["enum"] == ["guard"]
    assert by_kind["clock_advance"]["properties"]["target"]["enum"] == ["pressure"]
    assert schema["properties"]["discovery_refs"]["maxItems"] == 0
    plan = {"intent": "interact", "input_quote": "询问", "approach": "询问", "departure_question": "",
            "on_success": [{"kind": "flag_set", "target": "imagined", "evidence_quote": "询问", "reason": "非法"}]}
    assert list(Draft202012Validator(schema).iter_errors(plan))


def test_declared_completion_effect_remains_valid_in_projected_schema():
    state = world()
    state["completion_flags"] = {"hall_searched": "大厅搜索完成"}
    plan = {"intent": "interact", "input_quote": "搜索", "approach": "搜索", "departure_question": "",
            "on_success": [{"kind": "flag_set", "target": "hall_searched", "evidence_quote": "搜索", "reason": "搜索完成"}]}
    Draft202012Validator(adjudication_schema(state)).validate(plan)


def test_pending_permission_can_be_discussed_before_destination_flag_is_met():
    state = world()
    state["scene_catalog"]["university"]["required_flags"] = {"permission": True}
    resolution = validate_proposal(preparing(), INPUT, state, plan_player_action(INPUT, state))
    assert resolution.phase == ActionPhase.DEPARTURE
    with pytest.raises(ValueError, match="前置条件"):
        validate_proposal(moving(INPUT), INPUT, state, plan_player_action(INPUT, state))
    state["flags"]["permission"] = True
    assert validate_proposal(moving(INPUT), INPUT, state, plan_player_action(INPUT, state)).is_arrival


def test_invalid_nontravel_proposal_cannot_fall_back_to_an_unpriced_action(monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    state = world()
    content = "我在这里监视几天。"
    raw = {"intent": "interact", "input_quote": content, "approach": "监视", "time_minutes": 4320}
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)),
                             client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response(raw)))),
                             raise_if_turn_cancelled=lambda: None)
    resolution = adjudicate_player_action(engine, content, state, plan_player_action(content, state))
    assert resolution.phase == ActionPhase.INTERACTION
    assert json.loads(resolution.adjudication_json)["intent"] == "clarify"
    assert "尚未执行" in json.loads(resolution.adjudication_json)["approach"]
    assert engine.context.world_store.load() == state


def hunter_world():
    state = json.loads((PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text())
    state["current_scene"] = {"id": "arkham_sanatorium", "npcs_present": ["cecil_hunter"]}
    return state


def test_model_sees_author_defined_access_and_no_roll_contract():
    state = hunter_world()
    content = "请亨特拿出复制件让我检查。"
    payload = adjudication_context(state, content, plan_player_action(content, state))
    candidate = payload["discovery_candidates"]["hunter_copy:0"]
    assert candidate["requires_success"] is False
    assert "枕头底下" in candidate["authored_outcome"]
    schema = adjudication_schema(state)
    assert "hunter_copy:0" in schema["properties"]["discovery_refs"]["items"]["enum"]


def test_authorized_core_discovery_channel_works_after_optional_check_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app.engine.API_KEY", "test-key")
    context = RuntimeContext(PROJECT_ROOT, tmp_path, "hunter-channel-probe", "猩红文档").ensure_initialized()
    state = hunter_world()
    context.world_store.restore(state)
    with patch("src.structured.engine_gate.OpenAI", return_value=SimpleNamespace()):
        engine = GameEngine(context)
    match = discovery_candidates(state)["hunter_copy:0"]
    results = engine._resolve_discoveries([match], {"success": False, "skill": "psychoanalysis"})
    assert results[0]["discovered"] is True
    assert any(c.get("catalog_id") == "hunter_copy" for c in context.world_store.load()["clues_found"]["investigation"])


def test_required_check_still_blocks_without_success():
    state = world()
    state["clue_catalog"] = {"locked": {"id": "locked", "source": "hall", "type": "hidden",
        "discovery_rules": [{"intent": "search", "targets": ["抽屉"], "requires_success": True, "skill": "locksmith"}]}}
    raw = {"intent": "interact", "input_quote": "搜查抽屉", "approach": "搜查抽屉", "discovery_refs": ["locked:0"]}
    with pytest.raises(ValueError, match="必须执行"):
        validate_proposal(raw, "搜查抽屉", state, plan_player_action("搜查抽屉", state))
    gated = copy.deepcopy(state)
    gated["clue_catalog"]["locked"]["discovery_rules"][0]["requires_flags"] = ["not_met"]
    assert not discovery_candidates(gated)
