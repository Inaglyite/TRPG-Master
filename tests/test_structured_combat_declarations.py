"""A typed combat declaration is durable intent, not dice or execution consent."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import PlayerRequest, WorldInvestigator, WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.validation import validate_event, validate_frame


def ready(battle):
    url, service, alice, execute, snapshot, draws = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    return url, service, alice, execute, snapshot, draws


def request(snapshot, **changes):
    return {
        "type": "action_request",
        "protocol_version": 1,
        "world_id": "sp-world",
        "request_id": "declare-1",
        "expected_revision": snapshot()[1],
        "investigator_id": "inv-alice",
        "action": {
            "kind": "combat",
            "encounter_id": snapshot()[0]["combat_state"]["encounter_id"],
            "action_type": "firearm",
            "target_id": "guard",
            **changes,
        },
    }


def submit(service, alice, frame):
    validate_frame("action_request", frame)
    return service.submit_action_request(world_id="sp-world", principal=alice, request=frame)


def test_declaration_persists_without_rng_health_ammo_or_revision_then_replays(battle):
    url, service, alice, _, snapshot, draws = ready(battle)
    frame = request(snapshot, approach="掩护同伴")
    before = snapshot()
    result = submit(service, alice, frame)
    assert result["status"] == "queued" and not draws
    assert snapshot()[:3] == before[:3]
    keeper_snapshot = service.session_snapshot(
        world_id="sp-world", principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert keeper_snapshot["server_capabilities"]["combat_action_request"]
    validate_event(
        {
            "protocol_version": 1,
            "event_id": 0,
            "world_id": "sp-world",
            "sequence": 0,
            "revision": snapshot()[1],
            "type": "session_snapshot",
            "cause_request_id": None,
            "payload": keeper_snapshot,
        }
    )
    own = service.session_snapshot(world_id="sp-world", principal=alice)
    assert own["requests"][0]["action"] == frame["action"]
    bob = service.session_snapshot(
        world_id="sp-world",
        principal=Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",)),
    )
    assert not any(r["request_id"] == "declare-1" for r in bob["requests"])
    with session_scope(url) as session:
        row = session.scalar(select(PlayerRequest).where(PlayerRequest.request_id == "declare-1"))
        assert row.payload["action"] == frame["action"] and row.status == "queued"
    after = snapshot()
    assert submit(service, alice, frame)["deduplicated"]
    assert snapshot() == after and not draws


@pytest.mark.parametrize(
    "changes",
    [
        {"damage_spec": "100d100"},
        {"actor_id": "guard"},
        {"bonus_dice": 2},
        {"defender_choice": "fight_back"},
        {"target_id": None},
        {"action_type": "flee"},
    ],
)
def test_payload_injection_or_missing_attack_target_refused_even_programmatic(battle, changes):
    _, service, alice, _, snapshot, draws = ready(battle)
    before = snapshot()
    with pytest.raises(StructuredError, match="协议"):
        service.submit_action_request(
            world_id="sp-world", principal=alice, request=request(snapshot, **changes)
        )
    assert snapshot() == before and not draws


@pytest.mark.parametrize(
    "changes",
    [{"target_id": "inv-alice"}, {"target_id": "not-present"}, {"encounter_id": "old-encounter"}],
)
def test_stale_or_self_target_refused_without_request_or_state_mutation(battle, changes):
    _, service, alice, _, snapshot, draws = ready(battle)
    before = snapshot()
    with pytest.raises(StructuredError):
        submit(service, alice, request(snapshot, **changes))
    assert snapshot() == before and not draws


def test_waiting_roll_cannot_receive_another_declaration(battle):
    _, service, alice, _, snapshot, draws = battle
    before = snapshot()
    with pytest.raises(StructuredError, match="当前决定或掷骰"):
        submit(service, alice, request(snapshot))
    assert snapshot() == before and not draws


def test_down_roster_refused_even_if_encounter_actor_still_alive(battle):
    url, service, alice, _, snapshot, draws = ready(battle)
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["investigators"]["inv-alice"]["hp"] = 0
        row.state = state
    before = snapshot()
    with pytest.raises(StructuredError, match="无法行动"):
        submit(service, alice, request(snapshot))
    assert snapshot() == before and not draws


def test_revoked_control_cannot_submit_or_replay_private_declaration(battle):
    url, service, alice, _, snapshot, draws = ready(battle)
    frame = request(snapshot)
    submit(service, alice, frame)
    with session_scope(url) as session:
        row = session.get(WorldInvestigator, "wi-alice")
        row.controller_user_id = None
        row.status = "available"
    before = snapshot()
    for request_id in ("declare-1", "declare-2"):
        with pytest.raises(StructuredError, match="控制权"):
            submit(service, alice, {**frame, "request_id": request_id})
    assert snapshot() == before and not draws


def test_outbox_failure_rolls_back_declaration(battle):
    url, service, alice, _, snapshot, draws = ready(battle)
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox failure")):
        with pytest.raises(RuntimeError):
            submit(service, alice, request(snapshot))
    assert snapshot() == before and not draws
    with session_scope(url) as session:
        assert (
            session.scalar(select(PlayerRequest).where(PlayerRequest.request_id == "declare-1"))
            is None
        )


@pytest.mark.parametrize("wrong", [{"target_id": "inv-bob"}, {"action_type": "melee"}])
def test_approval_must_preserve_typed_target_and_action(battle, wrong):
    _, service, alice, _, snapshot, draws = ready(battle)
    submit(service, alice, request(snapshot))
    before = snapshot()
    with pytest.raises(StructuredError, match="原战斗申报不一致"):
        service.execute_command(
            world_id="sp-world",
            principal=Principal(kind="keeper", user_id="u-keeper"),
            kind="combat_action",
            payload={
                "actor_id": "inv-alice",
                "action_type": "firearm",
                "target_id": "guard",
                **wrong,
            },
            command_id="wrong-approval",
            expected_revision=None,
            cause_id="declare-1",
        )
    assert snapshot() == before and not draws


def test_matching_approval_waits_then_actual_player_roll_and_explicit_host_resolution(battle):
    url, service, alice, execute, snapshot, draws = ready(battle)
    submit(service, alice, request(snapshot))
    service.execute_command(
        world_id="sp-world",
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="combat_action",
        payload={
            "actor_id": "inv-alice",
            "action_type": "firearm",
            "target_id": "guard",
            "weapon": "手枪",
            "damage_spec": "1d3",
        },
        command_id="matching-approval",
        expected_revision=None,
        cause_id="declare-1",
    )
    assert not draws and snapshot()[0]["pc"]["inventory"] == ["手枪（3发）"]
    assert snapshot()[0]["combat_pending_roll"]["source_request_id"] == "declare-1"
    result = execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    assert result["result"]["source_request_id"] == "declare-1"
    assert draws and snapshot()[0]["pc"]["inventory"] == ["手枪（2发）"]
    execute(
        "resolve_intent",
        {"request_id": "declare-1", "resolution": "completed", "outcome": "success"},
    )
    with session_scope(url) as session:
        row = session.scalar(select(PlayerRequest).where(PlayerRequest.request_id == "declare-1"))
        assert row.status == "completed"
