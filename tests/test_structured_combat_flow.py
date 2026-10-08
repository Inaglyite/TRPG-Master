"""Preparation is not execution; only a controlled player can roll/respond."""

import copy

import pytest

from src.structured.combat_endings import combat_projection, finish_encounter, start_encounter
from src.structured.combat_flow import (
    prepare_combat_action,
    respond_combat_decision,
    respond_combat_roll,
)
from src.structured.domains import CommandContext
from src.structured.errors import StructuredError
from src.structured.principal import Principal


@pytest.fixture
def state():
    return {
        "pc": {
            "name": "调查员",
            "hp": 12,
            "max_hp": 12,
            "attributes": {"DEX": 90, "CON": 60},
            "skills": {"射击": 90, "斗殴": 90},
            "inventory": ["手枪（3发）"],
            "conditions": [],
        },
        "npcs": [
            {
                "id": "guard",
                "name": "守卫",
                "hp": 12,
                "max_hp": 12,
                "attributes": {"DEX": 20, "CON": 50},
                "hostile_to_pc": True,
                "skills": {"闪避": 20},
                "damage_spec": "1d3",
            }
        ],
        "current_scene": {"id": "hall", "npcs_present": ["guard"]},
        "investigator_controllers": {"alice": "pc"},
    }


def keeper():
    return CommandContext(
        world_id="test",
        principal=Principal(kind="keeper", user_id="gm"),
        rng=lambda _n: pytest.fail("preparation must not roll"),
    )


def player(user_id="alice", kind="player", ids=("pc",)):
    return CommandContext(
        world_id="test",
        principal=Principal(kind=kind, user_id=user_id, investigator_ids=ids),
        rng=lambda n: min(1, n - 1),
    )


def prepare(state):
    ctx = keeper()
    start_encounter(state, {"participants": [{"id": "guard"}]}, ctx)
    return prepare_combat_action(
        state,
        {
            "actor_id": "pc",
            "target_id": "guard",
            "action_type": "firearm",
            "weapon": "手枪",
            "damage_spec": "1d3",
        },
        ctx,
    )


def roll_frame(state, response="roll"):
    return {"roll_id": state["combat_pending_roll"]["roll_id"], "response": response}


def test_preparation_waits_without_consuming_rng_ammo_or_health(state):
    result = prepare(state)
    assert state["pc"]["inventory"] == ["手枪（3发）"]
    assert state["npcs"][0]["hp"] == 12
    assert state["combat_state"]["current_actor"] == "pc"
    assert combat_projection(state)["awaiting_roll"] is True
    assert result.events[-2].audience == {"kind": "investigators", "investigator_ids": ["pc"]}
    assert (
        "conditions" not in result.events[-2].payload and "action" not in result.events[-2].payload
    )
    with pytest.raises(StructuredError):
        prepare_combat_action(state, {"actor_id": "pc", "action_type": "other"}, keeper())


def test_player_roll_consumes_once_and_advances_turn(state):
    prepare(state)
    frame = roll_frame(state)
    result = respond_combat_roll(state, frame, player())
    assert result.result["attack_roll"]["roll"] == 11
    assert state["pc"]["inventory"] == ["手枪（2发）"]
    assert state["npcs"][0]["hp"] < 12
    assert state["combat_state"]["current_actor"] == "guard"
    assert "combat_pending_roll" not in state
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError, match="失效"):
        respond_combat_roll(state, frame, player())
    assert state == before


def test_keeper_cannot_inject_player_defense_into_npc_action(state):
    ctx = keeper()
    start_encounter(state, {"participants": [{"id": "guard"}]}, ctx)
    combat = state["combat_state"]
    combat["current_actor"] = "guard"
    combat["turn_index"] = combat["turn_order"].index("guard")
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError, match="替玩家选择"):
        prepare_combat_action(
            state,
            {
                "actor_id": "guard",
                "target_id": "pc",
                "action_type": "melee",
                "defender_choice": "dodge",
            },
            ctx,
        )
    assert state == before


@pytest.mark.parametrize(
    "principal",
    [
        player("bob"),
        player(ids=()),
        player(kind="agent"),
        player(kind="keeper"),
        player(kind="viewer"),
    ],
)
def test_other_players_agent_keeper_and_viewer_cannot_roll(state, principal):
    prepare(state)
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError, match="控制|只有"):
        respond_combat_roll(state, roll_frame(state), principal)
    assert state == before


def test_cancel_keeps_health_ammo_and_action(state):
    prepare(state)
    ctx = player()
    ctx.rng = lambda _n: pytest.fail("cancel must not roll")
    respond_combat_roll(state, roll_frame(state, "cancel"), ctx)
    assert state["pc"]["inventory"] == ["手枪（3发）"]
    assert state["npcs"][0]["hp"] == 12
    assert state["combat_state"]["current_actor"] == "pc"
    assert state["combat_state"]["phase"] == "awaiting_action"


def test_changed_conditions_refuse_roll_but_allow_cancel(state):
    prepare(state)
    state["pc"]["inventory"] = ["手枪（1发）"]
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError, match="条件已变化"):
        respond_combat_roll(state, roll_frame(state), player())
    assert state == before
    respond_combat_roll(state, roll_frame(state, "cancel"), player())
    assert "combat_pending_roll" not in state
    assert state["pc"]["inventory"] == ["手枪（1发）"]


def test_nonhostile_confirmation_does_not_replace_player_roll(state):
    state["npcs"][0]["hostile_to_pc"] = False
    prepare(state)
    assert "combat_pending_roll" not in state
    decision = state["combat_state"]["pending_decision"]
    frame = {"decision_id": decision["id"], "option_id": "confirm_violence"}
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError):
        respond_combat_decision(state, frame, player(kind="agent"))
    assert state == before
    respond_combat_decision(state, frame, player())
    assert state["pc"]["inventory"] == ["手枪（3发）"]
    assert state["npcs"][0]["hp"] == 12
    respond_combat_roll(state, roll_frame(state), player())
    assert state["pc"]["inventory"] == ["手枪（2发）"]
    assert state["combat_state"]["pending_decision"] is None


def test_unusable_weapon_cannot_create_waiting_card(state):
    state["pc"]["inventory"] = ["手枪（0发）"]
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError, match="弹药不足"):
        prepare(state)
    # Starting the encounter was valid; the invalid attack was not queued.
    assert "combat_pending_roll" not in state
    assert state["pc"] == before["pc"] and state["npcs"] == before["npcs"]


def test_finish_clears_waiting_roll_and_old_button_becomes_invalid(state):
    prepare(state)
    frame = roll_frame(state)
    finish_encounter(state, {"reason": "谈判停战"}, keeper())
    assert "combat_pending_roll" not in state
    with pytest.raises(StructuredError):
        respond_combat_roll(state, frame, player())


def test_npc_attack_waits_for_actual_player_defence_then_player_roll(state):
    ctx = keeper()
    start_encounter(state, {"participants": [{"id": "guard"}]}, ctx)
    prepare_combat_action(state, {"actor_id": "pc", "action_type": "other"}, ctx)
    assert state["combat_state"]["current_actor"] == "guard"
    prepare_combat_action(
        state,
        {
            "actor_id": "guard",
            "target_id": "pc",
            "action_type": "melee",
        },
        ctx,
    )
    assert state["pc"]["hp"] == 12
    decision = state["combat_state"]["pending_decision"]
    assert decision["responding_investigator_id"] == "pc"
    respond_combat_decision(
        state,
        {
            "decision_id": decision["id"],
            "option_id": "dodge",
        },
        player(),
    )
    assert state["pc"]["hp"] == 12
    assert state["combat_pending_roll"]["investigator_id"] == "pc"
    respond_combat_roll(state, roll_frame(state), player())
    assert "combat_pending_roll" not in state
    assert state["combat_state"]["pending_decision"] is None


def test_exception_during_roll_leaves_actual_working_state_unchanged(state):
    prepare(state)
    before = copy.deepcopy(state)
    calls = []
    ctx = player()

    def fail_mid_roll(n):
        calls.append(n)
        if len(calls) == 2:
            raise RuntimeError("injected RNG failure")
        return 1

    ctx.rng = fail_mid_roll
    with pytest.raises(RuntimeError, match="injected"):
        respond_combat_roll(state, roll_frame(state), ctx)
    assert len(calls) == 2
    assert state == before
