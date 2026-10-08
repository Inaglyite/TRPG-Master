"""Human clinical records are explicit, private, causal and irreversible for death."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import GameCommand, WorldMember, WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal


def payload(**over):
    return {
        "investigator_id": "inv-alice",
        "condition": "prone",
        "operation": "add",
        "expected_present": False,
        "basis": "主持确认调查员倒地，未增加或扣减生命。",
        **over,
    }


def mutate(battle, apply):
    with session_scope(battle[0]) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        apply(state)
        row.state = state


def set_conditions(battle, conditions, hp=None, investigator_id="inv-alice"):
    def apply(state):
        sheet = (
            state["pc"]
            if investigator_id == "inv-alice"
            else state["investigators"][investigator_id]
        )
        sheet["conditions"] = list(conditions)
        if hp is not None:
            sheet["hp"] = hp
        state["investigators"][investigator_id] = copy.deepcopy(sheet)
        for p in state["combat_state"]["participants"]:
            if p["id"] == investigator_id:
                p.update(conditions=list(conditions), hp=sheet["hp"])

    mutate(battle, apply)


def test_explicit_record_synchronizes_actor_and_private_card_and_retires_old_roll(battle):
    _, service, alice, execute, snapshot, draws = battle
    before = snapshot()[0]
    old_roll = {"roll_id": before["combat_pending_roll"]["roll_id"], "response": "roll"}
    result = execute("record_condition", payload(), command_id="condition-once")
    state = snapshot()[0]
    assert (
        state["pc"]["conditions"] == state["investigators"]["inv-alice"]["conditions"] == ["prone"]
    )
    assert next(p for p in state["combat_state"]["participants"] if p["id"] == "inv-alice")[
        "conditions"
    ] == ["prone"]
    assert not state.get("combat_pending_roll") and not draws
    assert (
        state["pc"]["hp"] == before["pc"]["hp"]
        and state["item_registry"] == before["item_registry"]
    )
    changed = [e for e in result["events"] if e["type"] == "state_changed"]
    assert len(changed) == 2 and {e["audience"]["kind"] for e in changed} == {
        "keeper",
        "investigators",
    }
    assert result["result"]["record"]["before"] is False
    assert result["result"]["record"]["after"] is True
    assert result["result"]["record"]["basis"] == payload()["basis"]
    after = snapshot()
    replay = execute("record_condition", payload(), command_id="condition-once")
    assert replay["deduplicated"] and replay["result"] == result["result"] and snapshot() == after
    with pytest.raises(StructuredError, match="失效"):
        execute("combat_roll", old_roll, alice)
    assert snapshot() == after and not draws


@pytest.mark.parametrize("kind", ["agent", "player", "viewer"])
def test_non_human_cannot_record_or_read_a_replayed_private_record(battle, kind):
    _, _, _, execute, snapshot, draws = battle
    execute("record_condition", payload(), command_id="human-clinical")
    before = snapshot()
    with pytest.raises(StructuredError, match="人类主持"):
        execute(
            "record_condition",
            payload(),
            Principal(kind=kind, user_id="u-keeper"),
            "human-clinical",
        )
    assert snapshot() == before and not draws


@pytest.mark.parametrize(
    "over",
    [
        {"expected_present": True},
        {"expected_present": 0},
        {"expected_present": "false"},
        {"condition": "healthy"},
        {"operation": "heal"},
        {"basis": "  "},
        {"basis": "x" * 1001},
        {"investigator_id": "unknown"},
        {"hp": 99},
    ],
)
def test_bad_or_stale_records_change_nothing(battle, over):
    _, _, _, execute, snapshot, draws = battle
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("record_condition", payload(**over))
    assert snapshot() == before and not draws


@pytest.mark.parametrize("condition", ["unconscious", "dying"])
def test_waking_requires_positive_hp_but_does_not_heal_or_clear_other_marks(battle, condition):
    _, _, _, execute, snapshot, draws = battle
    set_conditions(battle, [condition, "major_wound", "custom-author-mark"], hp=0)
    before = snapshot()
    removal = payload(condition=condition, operation="remove", expected_present=True)
    with pytest.raises(StructuredError, match="HP 仍为 0"):
        execute("record_condition", removal)
    assert snapshot() == before and not draws
    execute(
        "adjust_stat",
        {"investigator_id": "inv-alice", "field": "hp", "delta": 1, "reason": "急救已结算"},
    )
    result = execute("record_condition", removal)
    state = snapshot()[0]
    assert state["pc"]["hp"] == 1
    assert state["pc"]["conditions"] == ["major_wound", "custom-author-mark"]
    assert result["result"]["record"]["before"] and not result["result"]["record"]["after"]
    assert not draws


def test_death_cannot_be_removed_or_repaired_by_removing_other_marks(battle):
    _, _, _, execute, snapshot, draws = battle
    set_conditions(battle, ["dead", "unconscious"], hp=1)
    for condition in ["dead", "unconscious"]:
        before = snapshot()
        with pytest.raises(StructuredError, match="死亡"):
            execute(
                "record_condition",
                payload(condition=condition, operation="remove", expected_present=True),
            )
        assert snapshot() == before and not draws


def test_recording_death_requires_explicit_hp_settlement_and_never_changes_hp(battle):
    _, _, _, execute, snapshot, draws = battle
    before = snapshot()
    with pytest.raises(StructuredError, match="HP 归零"):
        execute("record_condition", payload(condition="dead"))
    assert snapshot() == before
    execute(
        "adjust_stat",
        {"investigator_id": "inv-alice", "field": "hp", "delta": -99, "reason": "主持确认致命伤"},
    )
    execute("record_condition", payload(condition="dead"))
    state = snapshot()[0]
    assert state["pc"]["hp"] == 0 and state["pc"]["conditions"] == ["dead"]
    assert not draws


def test_noop_does_not_invalidate_or_advance_existing_consent(battle):
    _, _, _, execute, snapshot, draws = battle
    before = snapshot()
    result = execute("record_condition", payload(operation="remove"))
    after = snapshot()
    assert after[0] == before[0] and after[1] == before[1]
    assert result["result"]["status"] == "not_executed"
    assert not any(e["type"] in {"combat_updated", "state_changed"} for e in result["events"])
    assert not draws


def test_noop_does_not_sync_an_unrelated_stale_encounter_without_revision(battle):
    _, _, _, execute, snapshot, draws = battle
    mutate(battle, lambda s: s["combat_state"]["participants"][0].update(hp=1))
    before = snapshot()
    result = execute("record_condition", payload(operation="remove"))
    assert result["result"]["status"] == "not_executed"
    assert snapshot()[0:2] == before[0:2]
    # A new command still has an acknowledgement/audit receipt, not a new state.
    assert not any(e["type"] in {"combat_updated", "state_changed"} for e in result["events"])
    assert not draws


@pytest.mark.parametrize("malformed", [{"dead": True}, [0], [["dead"]]])
def test_malformed_condition_copy_is_refused_without_losing_old_records(battle, malformed):
    _, _, _, execute, snapshot, draws = battle
    mutate(battle, lambda s: s["investigators"]["inv-alice"].update(conditions=malformed))
    before = snapshot()
    with pytest.raises(StructuredError, match="格式不兼容"):
        execute("record_condition", payload())
    assert snapshot() == before and not draws


def test_revoked_keeper_cannot_replay_their_prior_private_receipt(battle):
    url, _, _, execute, snapshot, _ = battle
    execute("record_condition", payload(), command_id="revoke-clinical")
    with session_scope(url) as session:
        member = session.scalar(select(WorldMember).where(WorldMember.user_id == "u-keeper"))
        member.can_keeper = False
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("record_condition", payload(), command_id="revoke-clinical")
    assert snapshot() == before


def test_outbox_failure_rolls_back_conditions_wait_and_private_audit(battle):
    url, service, _, execute, snapshot, draws = battle
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox failed")):
        with pytest.raises(RuntimeError):
            execute("record_condition", payload(), command_id="clinical-failed")
    assert snapshot() == before and not draws
    with session_scope(url) as session:
        assert (
            session.scalar(select(GameCommand).where(GameCommand.command_id == "clinical-failed"))
            is None
        )


def test_contradictory_dead_roster_or_encounter_is_not_silently_revived(battle):
    _, _, _, execute, snapshot, draws = battle
    mutate(battle, lambda s: s["investigators"]["inv-alice"].update(conditions=["dead"]))
    before = snapshot()
    with pytest.raises(StructuredError, match="副本不一致"):
        execute("record_condition", payload())
    assert snapshot() == before

    # Restore the roster, but leave a contradictory death in the encounter.
    def stale_encounter(state):
        state["investigators"]["inv-alice"]["conditions"] = []
        state["combat_state"]["participants"][0]["conditions"] = ["dead"]

    mutate(battle, stale_encounter)
    before = snapshot()
    with pytest.raises(StructuredError, match="死亡记录"):
        execute("record_condition", payload())
    assert snapshot() == before and not draws


def test_non_active_investigator_record_does_not_overwrite_active_character(battle):
    _, _, _, execute, snapshot, draws = battle
    active = copy.deepcopy(snapshot()[0]["pc"])
    result = execute("record_condition", payload(investigator_id="inv-bob"))
    assert snapshot()[0]["pc"] == active
    assert snapshot()[0]["investigators"]["inv-bob"]["conditions"] == ["prone"]
    own = next(
        e
        for e in result["events"]
        if e["type"] == "state_changed" and e["audience"]["kind"] == "investigators"
    )
    assert own["audience"]["investigator_ids"] == ["inv-bob"] and not draws


def test_closed_case_cannot_rewrite_settled_conditions(battle):
    _, _, _, execute, snapshot, draws = battle
    mutate(battle, lambda s: s.update(game_over={"ending_id": "closed"}))
    before = snapshot()
    with pytest.raises(StructuredError, match="结束"):
        execute("record_condition", payload())
    assert snapshot() == before and not draws
