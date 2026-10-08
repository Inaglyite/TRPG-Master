"""Committed scene visits enable return routes without revealing unvisited maps."""

import copy

import pytest
from test_structured_commands import make_structured_world

from src.storage.database import WorldState, session_scope
from src.storage.persistence import save_game
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService

KEEPER = Principal(kind="keeper", user_id="u-keeper")
PLAYER = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))


@pytest.fixture
def routes(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["scene_catalog"]["library"]["exits"].append("gallery")
        state["scene_catalog"]["gallery"] = {"id": "gallery", "name": "画廊", "exits": ["library"]}
        state["scene_catalog"]["secret"] = {"id": "secret", "name": "未知密室", "exits": []}
        state["encounter_history"] = {"study": {"npc-roll": {"present": True, "check_result": 42}}}
        row.state = state
    service = StructuredPlayService(context.database_url)
    return context, service, tmp_path


def move(context, service, destination, command_id=None):
    return service.execute_command(
        world_id=context.world_id,
        principal=KEEPER,
        kind="move_party",
        payload={"destination_scene_id": destination},
        command_id=command_id or destination,
        expected_revision=None,
    )


def state_of(context):
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


def test_committed_visits_allow_direct_return_preserve_encounters_and_hide_unvisited(routes):
    context, service, _ = routes
    original = state_of(context)[0]["encounter_history"]["study"]
    move(context, service, "library")
    result = move(context, service, "gallery")
    for principal in (KEEPER, PLAYER):
        destinations = service.session_snapshot(world_id=context.world_id, principal=principal)[
            "destinations"
        ]
        assert {entry["id"] for entry in destinations} == {"study", "library", "gallery"}
    scene_event = next(e for e in result["events"] if e["type"] == "scene_changed")
    assert "study" in {entry["id"] for entry in scene_event["payload"]["destinations"]}
    assert state_of(context)[0]["encounter_history"]["study"] == original
    assert state_of(context)[0]["encounter_history"]["gallery"] == {}
    move(context, service, "study", "return-direct")
    before = state_of(context)
    assert move(context, service, "gallery", "gallery")["deduplicated"] is True
    assert state_of(context) == before


def test_refused_and_rolled_back_movement_do_not_record_visits(routes, monkeypatch):
    context, service, _ = routes
    before = state_of(context)
    with pytest.raises(StructuredError) as error:
        move(context, service, "secret")
    assert error.value.code == "unknown_target"
    assert state_of(context) == before

    def fail(*args, **kwargs):
        raise RuntimeError("outbox failed")

    monkeypatch.setattr(service, "_append_events", fail)
    with pytest.raises(RuntimeError, match="outbox failed"):
        move(context, service, "library")
    assert state_of(context) == before


def test_restore_and_branch_use_their_own_committed_visit_history(routes):
    context, service, root = routes
    move(context, service, "library")
    save_game([], "slot_008", context=context)
    move(context, service, "gallery")
    branch = create_structured_branch(context, project_root=root, runtime_root=root)
    restore_structured_save(context, "slot_008")
    assert "gallery" not in state_of(context)[0]["encounter_history"]
    assert "gallery" in state_of(branch.context)[0]["encounter_history"]
    move(branch.context, service, "study", "branch-return")
    assert state_of(context)[0]["current_scene"]["id"] == "library"


def test_malformed_history_is_refused_not_silently_overwritten(routes):
    context, service, _ = routes
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["encounter_history"] = ["custom invalid history"]
        row.state = state
    before = state_of(context)
    with pytest.raises(StructuredError):
        move(context, service, "library")
    assert state_of(context) == before
