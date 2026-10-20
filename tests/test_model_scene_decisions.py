"""Model decisions own travel; legacy matching must not supply player speech."""

import json
from types import SimpleNamespace

import pytest

from src.app.agent_graph import _prepare_turn
from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.gameplay.action_adjudication import (
    adjudicate_player_action,
    adjudication_context,
    validate_proposal,
)
from src.gameplay.action_resolution import ActionPhase, ActionResolution, plan_player_action
from src.gameplay.discovery import DiscoveryMatch
from tests import test_discovery


@pytest.fixture
def context(tmp_path):
    return RuntimeContext.create(
        "model-scene-decision", "猩红文档",
        project_root=PROJECT_ROOT, runtime_root=tmp_path,
    )


def test_model_gets_location_facts_not_a_preselected_route(context):
    world = context.world_store.load()
    text = "我想看看莱特教授的尸体"
    payload = adjudication_context(world, text, plan_player_action(text, world))
    assert "遗体" in payload["destinations"]["miskatonic_medical"]["description"]
    assert "deterministic_hint" not in payload
    assert "action_advisories" not in payload["current_scene"]
    assert "action_routes" not in payload["current_scene"]


@pytest.mark.parametrize("intent", ["interact", "clarify", "move"])
def test_model_overrides_body_route_without_streaming_canned_speech(context, intent):
    world = context.world_store.load()
    text = "我想看看莱特教授的尸体"
    legacy = plan_player_action(text, world)
    assert legacy.destination_scene_id == "miskatonic_medical"
    raw = {"intent": intent, "input_quote": text, "approach": "结合上下文处理验尸意图"}
    if intent == "move":
        raw.update(
            destination_scene_id="miskatonic_medical",
            destination_reason=world["scene_catalog"]["miskatonic_medical"]["description"],
        )
    resolution = validate_proposal(raw, text, world, legacy)
    events = []
    engine = test_discovery.DiscoveryResolutionTests._preview_engine(context, events)
    engine._preplanned_action_resolution = resolution
    _prepare_turn({"engine": engine, "user_content": text})
    after = context.world_store.load()
    expected = "miskatonic_medical" if intent == "move" else "miskatonic_university"
    assert after["current_scene"]["id"] == expected
    # Neither fixed handoff, travel nor entry prose is emitted before narration.
    assert not [event for event in events if event[0] in {"narrative", "decision"}]
    assert '听你说出' not in engine.messages[-1]["content"]
    assert "拨通医学院的内线" not in engine.messages[-1]["content"]
    if intent == "move":
        assert '"scene_entry_beat":null' in engine.messages[-1]["content"]
        assert "冷柜间门口，惠特克罗夫特医生攥着病历夹" not in engine.messages[-1]["content"]
    assert after.get("clues_found") == world.get("clues_found")


def test_model_can_resolve_conversational_destination_without_matching_a_name(context):
    world = context.world_store.load()
    text = "那就过去吧"
    dialogue = [{"role": "assistant", "content": "法伦指了指医学院：遗体在那里。"}]
    raw = {
        "intent": "move", "input_quote": text, "approach": "承接刚刚指明的遗体位置",
        "destination_scene_id": "miskatonic_medical",
        "destination_reason": "法伦指了指医学院：遗体在那里。",
    }
    fallback = plan_player_action(text, world)
    assert not fallback.destination_scene_id
    result = validate_proposal(raw, text, world, fallback, dialogue)
    assert result.destination_scene_id == "miskatonic_medical"
    assert result.transition_kind == "model_adjudicated"
    with pytest.raises(ValueError, match="目的地不存在"):
        validate_proposal({**raw, "destination_scene_id": "invented"}, text, world, fallback, dialogue)
    world["scene_catalog"]["miskatonic_medical"]["required_flags"] = {"access": True}
    with pytest.raises(ValueError, match="前置条件"):
        validate_proposal(raw, text, world, fallback, dialogue)


def test_timeout_cannot_promote_legacy_body_match_to_travel(context, monkeypatch):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    world = context.world_store.load()
    text = "我想看看莱特教授的尸体"

    def timeout(**kwargs):
        raise TimeoutError("simulated unavailable model")

    engine = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=timeout))),
        context=context, messages=[], raise_if_turn_cancelled=lambda: None,
    )
    result = adjudicate_player_action(engine, text, world, plan_player_action(text, world))
    assert not result.destination_scene_id
    assert not result.discovery_matches
    assert json.loads(result.adjudication_json)["intent"] == "clarify"


@pytest.mark.parametrize("adjudicated", [False, True])
def test_discovery_example_cannot_invent_player_speech_or_precheck_facts(context, adjudicated):
    text = "我是其侄女请来负责调查的，请还是说明白。"
    example = '你把"战国编钟"四个字说出口，金三爷拇指上的扳指停住了。'
    world = context.world_store.load()
    clue = {"id": "trade", "text": "机密交易内容", "source": world["current_scene"]["id"],
            "type": "obvious", "category": "npc"}
    rule = {"intent": "talk", "targets": ["编钟"], "approach_text": example,
            "skill": "persuade", "requires_success": True}
    events = []
    engine = test_discovery.DiscoveryResolutionTests._preview_engine(context, events)
    engine._preplanned_action_resolution = ActionResolution(
        player_input=text, phase=ActionPhase.CONTACT,
        origin_scene_id=world["current_scene"]["id"],
        discovery_matches=(DiscoveryMatch("trade", clue, rule),),
        preferred_skill="persuade",
        adjudication_json=json.dumps({"intent": "interact", "input_quote": text, "approach": text}) if adjudicated else "",
    )
    checks = []
    engine._resolve_action_check = lambda content, skill: checks.append((content, skill)) or {"success": False}
    result = _prepare_turn({"engine": engine, "user_content": text})
    # Semantic selection of a discovery never authorizes its sample wording.
    assert checks == [(text, "persuade")]
    assert not [event for event in events if event[0] == "narrative"]
    assert "战国编钟" not in result["narrative"]
    assert example not in engine.messages[-1]["content"]
    assert context.world_store.load()["clues_found"] == world["clues_found"]
