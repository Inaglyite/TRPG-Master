"""Acceptance boundaries found by real-model play, including alternate tools."""
import json
from types import SimpleNamespace

import pytest

from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.gameplay.action_adjudication import validate_proposal
from src.gameplay.action_resolution import plan_player_action
from src.gameplay.discovery import match_discovery_rules
from tests.test_action_adjudication import world
from tests.test_rule_authority_regressions import MemoryStore


def test_no_take_cannot_be_bypassed_by_direct_item_effect():
    state = world()
    state["current_scene"]["items"] = ["文档"]
    content = "我只检查文档，不拿走"
    raw = {"intent": "interact", "input_quote": content, "approach": "当面检查",
           "on_success": [{"kind": "take_item", "target": "文档", "source_id": "scene:hall:文档",
                           "evidence_quote": "检查文档", "reason": "尝试绕过发现门"}]}
    with pytest.raises(ValueError, match="不取得"):
        validate_proposal(raw, content, state, plan_player_action(content, state))


def test_no_take_cannot_be_bypassed_by_fallback_narrator():
    engine = object.__new__(GameEngine)
    engine.context = SimpleNamespace(world_store=MemoryStore(world()))
    engine._execute_tool = lambda *_: pytest.fail("unauthorized inventory write")
    result = json.loads(engine._execute_model_tool("state_add_item", {"item": "文档"}, player_action="只检查文档，不拿走"))
    assert result["ok"] is False


def test_reading_original_documents_does_not_acquire_them():
    state = json.loads((PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text())
    state["current_scene"] = {"id": "trivial_pursuits", "npcs_present": ["abner_wick"]}
    state["flags"]["deep_basement_found"] = True
    matches = match_discovery_rules("当面阅读女巫审判文档，只看，不带走", state)
    assert any(match.clue_id == "witch_trial_documents_read" for match in matches)
    assert all(not match.clue.get("granted_item") and not match.clue.get("flag_effects") for match in matches)
    taking = match_discovery_rules("取走女巫审判文档", state)
    assert any(match.clue_id == "witch_trial_documents" for match in taking)


@pytest.mark.parametrize("condition", ["dying", "dead", "unconscious"])
@pytest.mark.parametrize("tool", ["skill_check", "state_add_clue", "state_add_item", "use_item"])
def test_condition_blocks_model_actions_even_with_positive_hp(condition, tool):
    state = world()
    state["pc"]["conditions"] = [condition]
    engine = object.__new__(GameEngine)
    engine.context = SimpleNamespace(world_store=MemoryStore(state))
    engine._execute_tool = lambda *_: pytest.fail("incapacitated action escaped guard")
    result = json.loads(engine._execute_model_tool(tool, {}, player_action="检查文档并带走"))
    assert result["error"] == "pc_incapacitated"


def test_no_take_blocks_known_clue_item_regrant():
    state = world()
    state["clue_catalog"] = {"docs": {"granted_item": "文档"}}
    state["clues_found"] = {"investigation": [{"id": "docs"}]}
    engine = object.__new__(GameEngine)
    engine.context = SimpleNamespace(world_store=MemoryStore(state))
    engine._execute_tool = lambda *_: pytest.fail("known clue must not regrant an item")
    result = json.loads(engine._execute_model_tool("state_add_clue", {"clue_id": "docs"}, player_action="只看看，不拿"))
    assert result["error"] == "acquisition_declined"


def test_read_and_take_share_one_sanity_exposure():
    from src.gameplay.sanity_sources import exposure_id_for_clue

    state = json.loads((PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text())
    assert exposure_id_for_clue(state, "witch_trial_documents_read") == exposure_id_for_clue(state, "witch_trial_documents")
