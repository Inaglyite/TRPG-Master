"""One committed character state must govern HUD, encounter and pending consent."""

import copy
from unittest.mock import patch

import pytest
from test_structured_combat_transactions import battle as battle

from src.storage.database import WorldState, session_scope
from src.structured.errors import StructuredError


def change(battle, investigator_id="inv-alice", field="hp", delta=-1):
    return battle[3](
        "adjust_stat",
        {
            "investigator_id": investigator_id,
            "field": field,
            "delta": delta,
            "reason": "主持确认伤势",
        },
    )


def cancel_roll(battle):
    _, _, alice, execute, snapshot, _ = battle
    return execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )


def mutate(battle, apply):
    with session_scope(battle[0]) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        apply(state)
        row.state = state


def test_active_stat_change_updates_pc_roster_encounter_and_retires_old_roll(battle):
    _, _, alice, execute, snapshot, draws = battle
    before = snapshot()[0]
    old = {"roll_id": before["combat_pending_roll"]["roll_id"], "response": "roll"}
    result = change(battle)
    state = snapshot()[0]
    hp = before["pc"]["hp"] - 1
    assert state["pc"]["hp"] == state["investigators"]["inv-alice"]["hp"] == hp
    assert (
        next(p for p in state["combat_state"]["participants"] if p["id"] == "inv-alice")["hp"] == hp
    )
    assert not state.get("combat_pending_roll")
    assert state["combat_state"]["phase"] == "awaiting_action"
    assert result["result"]["combat_wait_invalidated"] is True
    assert any(e["type"] == "combat_updated" for e in result["events"])
    after = snapshot()
    with pytest.raises(StructuredError, match="失效"):
        execute("combat_roll", old, alice)
    assert snapshot() == after and not draws
    assert state["item_registry"] == before["item_registry"]


@pytest.mark.parametrize(
    "invalid", [{"hp": 0}, {"conditions": ["unconscious"]}, {"conditions": ["dying"]}]
)
def test_pending_nonhostile_confirmation_rechecks_actual_actor_before_execution(battle, invalid):
    _, _, alice, execute, snapshot, draws = battle
    cancel_roll(battle)
    mutate(battle, lambda s: s["npcs"][0].update(hostile_to_pc=False))
    mutate(battle, lambda s: s["combat_state"]["participants"][-1].update(hostile_to_pc=False))
    execute(
        "combat_action",
        {"actor_id": "inv-alice", "target_id": "guard", "action_type": "firearm", "weapon": "手枪"},
    )
    decision_id = snapshot()[0]["combat_state"]["pending_decision"]["id"]
    mutate(battle, lambda s: s["pc"].update(invalid))
    before = snapshot()
    with pytest.raises(StructuredError, match="无法行动"):
        execute(
            "combat_decide", {"decision_id": decision_id, "option_id": "confirm_violence"}, alice
        )
    assert snapshot() == before and not draws
    execute("combat_decide", {"decision_id": decision_id, "option_id": "cancel_violence"}, alice)
    assert not snapshot()[0]["combat_state"]["pending_decision"]
    assert not draws and snapshot()[0]["item_registry"] == before[0]["item_registry"]


def test_stat_change_events_are_private_but_public_encounter_remains_current(battle):
    result = change(battle)
    events = [e for e in result["events"] if e["type"] == "state_changed"]
    assert {e["audience"]["kind"] for e in events} == {"investigators", "keeper"}
    assert next(e for e in events if e["audience"]["kind"] == "investigators")["audience"][
        "investigator_ids"
    ] == ["inv-alice"]
    assert all(e["payload"]["investigator_id"] == "inv-alice" for e in events)
    combat = next(e for e in result["events"] if e["type"] == "combat_updated")["payload"]
    assert "san" not in combat["participants"][0]


def test_downing_all_pcs_ends_encounter_without_rng_or_ammo_spend(battle):
    _, _, _, _, snapshot, draws = battle
    old = snapshot()[0]
    change(battle, investigator_id="inv-bob", delta=-99)
    change(battle, delta=-99)
    state = snapshot()[0]
    assert state["pc"]["hp"] == state["investigators"]["inv-alice"]["hp"] == 0
    assert not state["combat_state"]["active"]
    assert state["combat_state"]["outcome"] == "defeat"
    assert not state.get("combat_pending_roll")
    assert state["item_registry"] == old["item_registry"] and not draws


def test_downing_current_actor_skips_to_a_still_capable_participant(battle):
    _, _, _, _, snapshot, draws = battle
    change(battle, delta=-99)
    state = snapshot()[0]
    assert state["combat_state"]["active"]
    assert state["combat_state"]["current_actor"] != "inv-alice"
    assert state["combat_state"]["phase"] == "awaiting_action"
    assert not state.get("combat_pending_roll") and not draws


def test_adjusting_other_pc_does_not_overwrite_active_pc_or_publish_private_san(battle):
    _, _, _, _, snapshot, draws = battle
    before = snapshot()[0]
    result = change(battle, investigator_id="inv-bob")
    state = snapshot()[0]
    hp = before["investigators"]["inv-bob"]["hp"] - 1
    assert state["pc"] == before["pc"]
    assert state["investigators"]["inv-bob"]["hp"] == hp
    assert (
        next(p for p in state["combat_state"]["participants"] if p["id"] == "inv-bob")["hp"] == hp
    )
    owner = next(
        e
        for e in result["events"]
        if e["type"] == "state_changed" and e["audience"]["kind"] == "investigators"
    )
    assert owner["audience"]["investigator_ids"] == ["inv-bob"]
    assert not draws


def test_max_hp_adjustment_does_not_invent_healing_or_damage(battle):
    _, _, _, _, snapshot, draws = battle
    hp = snapshot()[0]["pc"]["hp"]
    max_hp = snapshot()[0]["pc"]["max_hp"]
    change(battle, field="max_hp", delta=2)
    state = snapshot()[0]
    participant = next(p for p in state["combat_state"]["participants"] if p["id"] == "inv-alice")
    assert state["pc"]["hp"] == participant["hp"] == hp
    assert state["pc"]["max_hp"] == participant["max_hp"] == max_hp + 2
    assert not draws and not state.get("combat_pending_roll")


def test_hp_increase_keeps_dying_condition_and_does_not_reauthorize_action(battle):
    _, _, _, execute, snapshot, draws = battle
    cancel_roll(battle)
    mutate(battle, lambda s: s["pc"].update(hp=1, conditions=["dying"]))
    change(battle, delta=3)
    state = snapshot()[0]
    assert state["pc"]["hp"] == 4 and state["pc"]["conditions"] == ["dying"]
    assert state["combat_state"]["current_actor"] != "inv-alice"
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("combat_action", {"actor_id": "inv-alice", "action_type": "other"})
    assert snapshot() == before and not draws


def test_pending_confirmation_cannot_attack_target_whose_actual_hp_is_zero(battle):
    _, _, alice, execute, snapshot, draws = battle
    cancel_roll(battle)
    mutate(battle, lambda s: s["npcs"][0].update(hostile_to_pc=False))
    mutate(battle, lambda s: s["combat_state"]["participants"][-1].update(hostile_to_pc=False))
    execute(
        "combat_action", {"actor_id": "inv-alice", "target_id": "guard", "action_type": "melee"}
    )
    decision_id = snapshot()[0]["combat_state"]["pending_decision"]["id"]
    mutate(battle, lambda s: s["npcs"][0].update(hp=0))
    before = snapshot()
    with pytest.raises(StructuredError, match="目标"):
        execute(
            "combat_decide", {"decision_id": decision_id, "option_id": "confirm_violence"}, alice
        )
    assert snapshot() == before and not draws


def test_stat_change_replay_does_not_skip_turn_twice_or_duplicate_invalidation(battle):
    _, _, _, execute, snapshot, draws = battle
    payload = {
        "investigator_id": "inv-alice",
        "field": "hp",
        "delta": -99,
        "reason": "主持确认伤势",
    }
    first = execute("adjust_stat", payload, command_id="downed-once")
    after = snapshot()
    second = execute("adjust_stat", payload, command_id="downed-once")
    assert second["deduplicated"] and second["result"] == first["result"]
    assert not second["events"] and snapshot() == after and not draws


@pytest.mark.parametrize("incapacitated", ["guard", "inv-alice"])
def test_pending_npc_attack_rechecks_attacker_and_defender_and_can_be_retired(
    battle, incapacitated
):
    _, _, alice, execute, snapshot, draws = battle
    cancel_roll(battle)

    def guard_turn(state):
        combat = state["combat_state"]
        combat["current_actor"] = "guard"
        combat["turn_index"] = combat["turn_order"].index("guard")

    mutate(battle, guard_turn)
    execute(
        "combat_action", {"actor_id": "guard", "target_id": "inv-alice", "action_type": "melee"}
    )
    decision = snapshot()[0]["combat_state"]["pending_decision"]
    mutate(
        battle,
        lambda s: (s["pc"] if incapacitated == "inv-alice" else s["npcs"][0]).update(
            conditions=["unconscious"]
        ),
    )
    before = snapshot()
    with pytest.raises(StructuredError, match="无法"):
        execute("combat_decide", {"decision_id": decision["id"], "option_id": "fight_back"}, alice)
    assert snapshot() == before and not draws
    # A zero-delta, documented keeper adjustment reconciles externally changed
    # sheets and retires the stale defence instead of forcing a downed reply.
    change(battle, delta=0)
    assert not snapshot()[0]["combat_state"]["pending_decision"]
    assert not draws and snapshot()[0]["item_registry"] == before[0]["item_registry"]


def test_nested_adjustment_and_illegal_downed_action_roll_back_as_one_batch(battle):
    _, service, _, execute, snapshot, draws = battle
    cancel_roll(battle)
    drafted = service.create_keeper_draft(
        world_id="sp-world",
        summary="不可把倒地当作仍能行动",
        proposed_commands=[
            {
                "kind": "adjust_stat",
                "payload": {
                    "investigator_id": "inv-alice",
                    "field": "hp",
                    "delta": -99,
                    "reason": "坠落",
                },
            },
            {"kind": "combat_action", "payload": {"actor_id": "inv-alice", "action_type": "other"}},
        ],
        narration="",
    )
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("resolve_draft", {"draft_id": drafted["draft_id"], "decision": "approved"})
    assert snapshot() == before and not draws


def test_stat_and_wait_events_roll_back_together_if_outbox_fails(battle):
    _, service, _, _, snapshot, draws = battle
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox unavailable")):
        with pytest.raises(RuntimeError):
            change(battle)
    assert snapshot() == before and not draws
