"""Elapsed time is an explicit public projection, not narration interpretation."""

import copy

import pytest
from test_structured_commands import make_structured_world
from test_structured_ws import _event_validator

from src.storage.database import WorldState, session_scope
from src.structured.game_clock import clock_projection
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService


@pytest.mark.parametrize("value", [True, "20", 1.5, -1, 2**53, None])
def test_bad_explicit_clock_is_unknown_and_not_repaired(value):
    state = {"world_clock": {"elapsed_minutes": value, "secret": "NPC schedule"}}
    before = copy.deepcopy(state)
    assert clock_projection(state) is None
    assert state == before


def test_unadvanced_world_starts_zero_but_projection_filters_secrets():
    assert clock_projection({}) == {"elapsed_minutes": 0}
    assert clock_projection({"world_clock": "broken"}) is None
    assert clock_projection(
        {
            "world_clock": {
                "elapsed_minutes": 200,
                "npc_deadline": 500,
                "calendar": "secret",
            }
        }
    ) == {"elapsed_minutes": 200}


def test_committed_move_and_time_match_player_keeper_and_wire_snapshot(tmp_path):
    context = make_structured_world(tmp_path)
    service = StructuredPlayService(context.database_url)
    keeper = Principal(kind="keeper", user_id="u-keeper")
    assert service.session_snapshot(world_id=context.world_id, principal=keeper)["clock"] == {
        "elapsed_minutes": 0,
    }
    for index, (kind, payload) in enumerate(
        [
            ("move_party", {"destination_scene_id": "library", "travel_minutes": 20}),
            ("advance_time", {"minutes": 180, "reason": "主持明确推进，不从对白推断"}),
        ]
    ):
        service.execute_command(
            world_id=context.world_id,
            principal=keeper,
            command_id=f"clock-{index}",
            kind=kind,
            payload=payload,
            expected_revision=None,
        )
    for principal in (
        keeper,
        Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
    ):
        assert service.session_snapshot(world_id=context.world_id, principal=principal)[
            "clock"
        ] == {
            "elapsed_minutes": 200,
        }
    envelope = StructuredGateway(context.database_url).snapshot_envelope(
        world_id=context.world_id,
        user_id="u-keeper",
    )
    _event_validator().validate(envelope)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["world_clock"]["npc_deadline"] = "secret deadline"
        row.state = state
    assert service.session_snapshot(world_id=context.world_id, principal=keeper)["clock"] == {
        "elapsed_minutes": 200,
    }


def test_corrupt_source_projects_null_without_side_effects(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["world_clock"] = {"elapsed_minutes": "invalid"}
        row.state = state
        revision = row.revision
    gateway = StructuredGateway(context.database_url)
    envelope = gateway.snapshot_envelope(world_id=context.world_id, user_id="u-keeper")
    assert envelope["payload"]["clock"] is None
    _event_validator().validate(envelope)
    with session_scope(context.database_url) as session:
        assert (
            session.get(WorldState, context.world_id).state["world_clock"] == state["world_clock"]
        )
        assert session.get(WorldState, context.world_id).revision == revision


def test_unsafe_elapsed_event_is_unknown_not_invalidating_other_status(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["world_clock"] = {"elapsed_minutes": 2**53 - 1}
        row.state = state
    result = StructuredPlayService(context.database_url).execute_command(
        world_id=context.world_id,
        principal=Principal(kind="keeper", user_id="u-keeper"),
        command_id="clock-overflow",
        kind="advance_time",
        payload={"minutes": 1, "reason": "边界"},
        expected_revision=None,
    )
    clock_event = next(e for e in result["events"] if e["type"] == "state_changed")
    assert clock_event["payload"]["clock"] is None
    _event_validator().validate({k: v for k, v in clock_event.items() if k != "audience"})
