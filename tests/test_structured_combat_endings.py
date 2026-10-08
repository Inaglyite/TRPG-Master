"""Domain foundation tests; not a claim of protocol/UI/real-model acceptance."""

import copy

import pytest

from src.structured.combat_endings import (
    combat_projection,
    execute_combat_action,
    finish_encounter,
    finish_game,
    resolve_combat_decision,
    start_encounter,
)
from src.structured.domains import CommandContext
from src.structured.errors import StructuredError


@pytest.fixture
def state():
    return {
        "pc": {
            "name": "调查员",
            "hp": 12,
            "max_hp": 12,
            "conditions": [],
            "attributes": {"DEX": 90, "CON": 60},
            "skills": {"斗殴": 90},
            "beliefs": "不能告诉旁人的背景",
        },
        "npcs": [
            {
                "id": "guard",
                "name": "守卫",
                "hp": 12,
                "max_hp": 12,
                "attributes": {"DEX": 20, "CON": 50},
                "skills": {"闪避": 20},
                "hostile_to_pc": True,
                "secret": "守卫的秘密",
                "damage_spec": "1d3",
            }
        ],
        "current_scene": {"id": "hall", "npcs_present": ["guard"]},
        "flags": {"sealed": False},
        "endings": [
            {
                "id": "seal",
                "title": "封印完成",
                "ending_type": "good",
                "description": "案件已经结束。",
                "required_flags": {"sealed": True},
            }
        ],
    }


@pytest.fixture
def ctx():
    return CommandContext(world_id="isolated-test", principal=None, rng=lambda n: min(1, n - 1))


def start(state, ctx):
    return start_encounter(state, {"participants": [{"id": "guard"}], "reason": "伏击"}, ctx)


def test_start_reuses_initiative_and_redacts_public_roster(state, ctx):
    result = start(state, ctx)
    public = result.events[0].payload
    assert public["current_actor"] == "pc"
    assert public["turn_order"] == ["pc", "guard"]
    assert state["combat_state"]["participants"][0]["skills"]["fighting_brawl"] == 90
    assert all("skills" not in actor and "path" not in actor for actor in public["participants"])
    assert "秘密" not in str(public)
    assert "beliefs" not in str(public)


@pytest.mark.parametrize(
    "spec",
    [
        {"id": "off-scene"},
        {"id": "guard", "dex": 999},
        {"id": "guard", "ready_firearm": "false"},
    ],
)
def test_start_invalid_entities_and_overrides_do_not_mutate(state, ctx, spec):
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError):
        start_encounter(state, {"participants": [spec]}, ctx)
    assert state == before


def test_duplicates_and_active_encounter_are_rejected(state, ctx):
    with pytest.raises(StructuredError):
        start_encounter(state, {"participants": [{"id": "guard"}, {"id": "guard"}]}, ctx)
    start(state, ctx)
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError):
        start(state, ctx)
    assert state == before


def test_melee_roll_damage_and_turn_are_legacy_rule_results(state, ctx):
    start(state, ctx)
    result = execute_combat_action(
        state,
        {
            "actor_id": "pc",
            "target_id": "guard",
            "action_type": "melee",
            "damage_spec": "1d3",
            "defender_choice": "dodge",
        },
        ctx,
    )
    assert result.result["attack_roll"]["roll"] == 11
    assert state["npcs"][0]["hp"] < 12
    assert state["combat_state"]["current_actor"] == "guard"
    assert combat_projection(state)["participants"][1]["hp"] == state["npcs"][0]["hp"]


def test_wrong_actor_and_zero_hp_cannot_act(state, ctx):
    start(state, ctx)
    with pytest.raises(StructuredError):
        execute_combat_action(state, {"actor_id": "guard", "action_type": "other"}, ctx)
    state["combat_state"]["participants"][0]["hp"] = 0
    with pytest.raises(StructuredError):
        execute_combat_action(state, {"actor_id": "pc", "action_type": "other"}, ctx)


def test_nonhostile_confirmation_is_private_and_cancel_does_not_roll(state, ctx):
    state["npcs"][0]["hostile_to_pc"] = False
    ctx.rng = lambda _n: pytest.fail("confirmation/cancel must not roll")
    start(state, ctx)
    result = execute_combat_action(
        state,
        {
            "actor_id": "pc",
            "target_id": "guard",
            "action_type": "melee",
        },
        ctx,
    )
    assert result.events[0].payload["awaiting_decision"] is True
    assert "description" not in result.events[0].payload
    private = result.events[1]
    assert private.audience == {"kind": "investigators", "investigator_ids": ["pc"]}
    assert "roleplay_context" not in private.payload and "action" not in private.payload
    assert result.events[2].audience == {"kind": "keeper"}
    assert "combat" not in result.result and "decision" not in result.result
    assert "不能告诉旁人的背景" not in str(result.result)
    decision = state["combat_state"]["pending_decision"]
    resolve_combat_decision(
        state,
        {
            "decision_id": decision["id"],
            "option_id": "cancel_violence",
        },
        ctx,
    )
    assert state["npcs"][0]["hp"] == 12
    assert state["combat_state"]["current_actor"] == "pc"
    assert state["combat_state"]["pending_decision"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("damage_spec", "999999d999999"),
        ("damage_spec", "0d6"),
        ("bonus_dice", True),
        ("penalty_dice", 3),
        ("surprise", True),
    ],
)
def test_bounded_action_input(state, ctx, field, value):
    start(state, ctx)
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError):
        execute_combat_action(state, {"actor_id": "pc", "action_type": "other", field: value}, ctx)
    assert state == before


def test_ending_requires_authored_flags_and_no_active_combat(state, ctx):
    with pytest.raises(StructuredError, match="前置条件"):
        finish_game(state, {"ending_id": "seal"}, ctx)
    assert "game_over" not in state
    state["flags"]["sealed"] = True
    start(state, ctx)
    with pytest.raises(StructuredError, match="当前战斗"):
        finish_game(state, {"ending_id": "seal"}, ctx)
    finish_encounter(state, {"reason": "双方谈判停战"}, ctx)
    result = finish_game(state, {"ending_id": "seal"}, ctx)
    assert state["game_over"]["id"] == "seal"
    assert result.events[0].type == "game_ended"
    assert result.events[0].payload["title"] == "封印完成"
    with pytest.raises(StructuredError, match="已结算"):
        finish_game(state, {"ending_id": "seal"}, ctx)
    with pytest.raises(StructuredError, match="已结算"):
        start(state, ctx)


def test_finish_requires_explicit_reason_and_real_encounter(state, ctx):
    with pytest.raises(StructuredError):
        finish_encounter(state, {"reason": ""}, ctx)
    with pytest.raises(StructuredError):
        finish_encounter(state, {"reason": "撤离"}, ctx)
