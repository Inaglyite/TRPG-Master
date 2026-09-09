import json
from types import SimpleNamespace

import pytest

from src.gameplay.action_adjudication import adjudicate_player_action, validate_proposal
from src.gameplay.action_resolution import ActionPhase, ActionResolution, plan_player_action
from src.gameplay.destination_grounding import DestinationGroundingError, recent_dialogue
from tests.test_action_adjudication import response


@pytest.fixture
def state():
    return {
        "pc": {"hp": 10},
        "current_scene": {"id": "medical", "npcs_present": ["doctor"]},
        "npcs": [
            {
                "id": "doctor",
                "name": "约翰·惠特克罗夫特医生",
                "current_location": "medical",
                "revealed": {"level": 1},
            }
        ],
        "scene_catalog": {
            "medical": {"name": "医学院", "aliases": ["停尸房"]},
            "office": {"name": "莱特的办公室", "aliases": ["莱特教授办公室"]},
            "history": {"name": "历史系自习室", "aliases": ["历史系"]},
        },
    }


def move(content, destination):
    return {
        "intent": "move",
        "input_quote": content,
        "approach": content,
        "destination_scene_id": destination,
        "time_minutes": 10,
    }


def test_doctors_office_is_not_wrights_office(state):
    content = "请他带你去办公室，查看那本记录着背部焦痕的私人笔记。"
    with pytest.raises(DestinationGroundingError):
        validate_proposal(
            move(content, "office"), content, state, plan_player_action(content, state)
        )
    result = validate_proposal(
        move(content, "medical"), content, state, plan_player_action(content, state)
    )
    assert result.phase == ActionPhase.INTERACTION
    assert not result.destination_scene_id


@pytest.mark.parametrize(
    "content",
    [
        "还是先去惠特克罗夫那里看看笔记",
        "从莱特办公室出来，先去惠特克罗夫那里看看笔记",
    ],
)
def test_named_doctor_returns_to_medical_not_history(state, content):
    state["current_scene"] = {"id": "office", "npcs_present": []}
    fallback = plan_player_action(content, state)
    with pytest.raises(DestinationGroundingError):
        validate_proposal(move(content, "history"), content, state, fallback)
    result = validate_proposal(move(content, "medical"), content, state, fallback)
    assert result.destination_scene_id == "medical"


@pytest.mark.parametrize(
    "content,destination",
    [
        ("去莱特的办公室", "office"),
        ("请医生带我去莱特教授办公室", "office"),
        ("去历史系", "history"),
        ("离开莱特办公室，前往历史系", "history"),
        ("前往莱特的办公室，用黄铜钥匙开门进去", "office"),
        ("前往莱特的办公室，用黄铜钥匙开门进去找线索", "office"),
    ],
)
def test_explicit_place_still_allowed(state, content, destination):
    result = validate_proposal(
        move(content, destination), content, state, plan_player_action(content, state)
    )
    assert result.destination_scene_id == destination


def test_following_person_does_not_mean_travel_to_their_current_location(state):
    content = "请惠特克罗夫带我去办公室"
    with pytest.raises(DestinationGroundingError):
        validate_proposal(
            move(content, "office"), content, state, plan_player_action(content, state)
        )


def test_move_to_absent_known_npc_is_not_remote_interaction(state):
    state["current_scene"] = {"id": "office", "npcs_present": []}
    content = "去惠特克罗夫那里看看笔记"
    raw = {**move(content, "medical"), "target_npc_id": "doctor"}
    fallback = plan_player_action(content, state)
    assert validate_proposal(raw, content, state, fallback).destination_scene_id == "medical"
    raw.update(intent="interact", destination_scene_id="")
    with pytest.raises(ValueError, match="远程交互"):
        validate_proposal(raw, content, state, fallback)
    raw["target_npc_id"] = ""
    with pytest.raises(DestinationGroundingError, match="原地交互"):
        validate_proposal(raw, content, state, fallback)


def test_unique_generic_shop_name_allowed_but_wrong_destination_blocked(state):
    state["scene_catalog"]["shop"] = {"name": "轻率琐事古董店"}
    content = "我在古董店对面监视进出的人"
    fallback = plan_player_action(content, state)
    assert (
        validate_proposal(move(content, "shop"), content, state, fallback).destination_scene_id
        == "shop"
    )
    with pytest.raises(DestinationGroundingError):
        validate_proposal(move(content, "office"), content, state, fallback)
    state["scene_catalog"]["other_shop"] = {"name": "另一家古董店"}
    with pytest.raises(DestinationGroundingError):
        validate_proposal(move(content, "shop"), content, state, fallback)


@pytest.mark.parametrize("kind", ["authored_route", "discovery_target"])
def test_structured_module_route_preserved(state, kind):
    content = "去约好的地方"
    fallback = ActionResolution(content, ActionPhase.ARRIVAL, "medical", "history", kind)
    assert (
        validate_proposal(move(content, "history"), content, state, fallback).destination_scene_id
        == "history"
    )


@pytest.mark.parametrize("second_error", [False, True])
def test_bad_move_then_retry_or_timeout_never_falls_back_to_wrong_scene(
    state, monkeypatch, second_error
):
    monkeypatch.setenv("TRPG_ACTION_ADJUDICATION", "1")
    content = "请他带你去办公室，查看私人笔记"
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if second_error and len(calls) == 2:
            raise TimeoutError("test timeout")
        return response(move(content, "office"))

    engine = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        context=SimpleNamespace(world_id="destination-test"),
        raise_if_turn_cancelled=lambda: None,
        messages=[{"role": "assistant", "content": "惠特克罗夫特医生说：笔记在我办公室抽屉里。"}],
    )
    fallback = ActionResolution(content, ActionPhase.ARRIVAL, "medical", "office", "explicit_move")
    result = adjudicate_player_action(engine, content, state, fallback)
    assert not result.destination_scene_id
    assert not result.discovery_matches
    assert json.loads(result.adjudication_json)["intent"] == "clarify"
    payload = json.loads(calls[0]["messages"][1]["content"])
    assert "笔记" in payload["recent_dialogue"][0]["content"]
    assert payload["known_people_locations"][0]["current_location"] == "medical"
    assert state["current_scene"]["id"] == "medical"


def test_two_character_name_component_grounds_travel_to_person(state):
    """两字姓名（法伦/洛奇/亨特）也要能解析为"去已知人物的所在地"。"""
    state["scene_catalog"]["university"] = {"name": "密斯卡托尼克大学"}
    state["npcs"].append(
        {"id": "fallon", "name": "布莱斯·法伦", "current_location": "university", "revealed": {"level": 1}}
    )
    content = "文档已经找回、怪物已被消灭。我去找法伦，完成封印仪式，了结这个案子"
    fallback = plan_player_action(content, state)
    assert (
        validate_proposal(move(content, "university"), content, state, fallback).destination_scene_id
        == "university"
    )


def test_role_word_alone_does_not_ground_travel(state):
    """只说"医生"不等于提到某个人：不能据此把目的地解析成他的位置。"""
    content = "请医生带我去办公室"
    with pytest.raises(DestinationGroundingError):
        validate_proposal(move(content, "history"), content, state, plan_player_action(content, state))


def test_recent_dialogue_bounded_and_excludes_tools_and_images():
    engine = SimpleNamespace(
        messages=[
            {"role": "system", "content": "private system"},
            {"role": "tool", "content": "private tool"},
            {"role": "user", "content": "[引擎控制指令｜非玩家发言]\nprivate reminder"},
            {"role": "assistant", "content": [{"type": "image_url", "image_url": "secret"}]},
            {"role": "assistant", "content": "可见对白" * 3000},
            {
                "role": "user",
                "content": "去他的办公室\n\n[引擎权威状态｜仅供守秘人，不得复述]\nprivate state",
            },
        ]
    )
    result = recent_dialogue(engine)
    assert sum(len(item["content"]) for item in result) <= 6000
    assert "private" not in str(result) and "secret" not in str(result)
