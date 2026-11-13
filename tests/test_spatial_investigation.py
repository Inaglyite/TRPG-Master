"""Minimal deterministic Spatial v1 slice; no model calls or production access."""

import copy
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_structured_commands import make_structured_world

from src.gameplay.spatial import (
    SpatialError,
    actor_id,
    authorize_transition,
    runtime,
    settle_transition,
    visible_destinations,
)
from src.gameplay.spatial_actions import execute_action, public_points
from src.gameplay.spatial_checks import legacy_proof, record_legacy_check
from src.gameplay.spatial_inventory import held_items
from src.gameplay.spatial_storage import guard_update
from src.modules.module_compiler import compile_payload
from src.modules.module_format import parse_module
from src.storage.database import WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/spatial-investigation"
KEEPER = Principal(kind="keeper", user_id="u-keeper")


def payloads():
    return [json.loads((EXAMPLE / name).read_text()) for name in ("manifest.json", "module.json")]


def world():
    result = compile_payload(*payloads())
    assert result.ok, result.to_dict(include_outputs=False)
    return copy.deepcopy(result.result.world_state)


def move(state, destination):
    before = copy.deepcopy(state)
    return settle_transition(before, state, destination)


def record(state, ident="real-check", *, success=False, target="fireplace:search"):
    record_legacy_check(
        state,
        skill="spot_hidden",
        result={"check_id": ident, "success": success},
        target_id=target,
        required_success_level="regular",
    )
    return legacy_proof(ident)


def grant(state, clue, actor):
    from src.structured.domains import CommandContext, cmd_grant_clue

    cmd_grant_clue(
        state,
        {"clue_id": clue, "recipient_investigator_ids": [actor], "basis": "已结算调查点"},
        CommandContext(world_id="test", principal=KEEPER),
    )


def discover(state, *, success=False):
    return execute_action(
        state, "fireplace", "search", proof=record(state, success=success), grant=grant
    )


def test_example_compilation_parent_cycle_and_missing_capability():
    manifest, module = payloads()
    preview = compile_payload(manifest, module)
    assert preview.ok
    assert preview.result.world_state["current_scene"]["id"] == "foyer"
    assert "spatial_interact" in preview.result.keeper_prompt
    manifest["capabilities"] = []
    assert not compile_payload(manifest, module).ok
    module["spatial"]["nodes"]["house"]["parent_scene_id"] = "house"
    with pytest.raises(ValidationError, match="循环"):
        parse_module(module)


def test_current_room_targets_hidden_rooms_and_old_exit_cannot_bypass():
    state = world()
    assert {p["id"] for p in public_points(state)} == {"cabinet", "lamp"}
    assert "secret" not in visible_destinations(state)
    with pytest.raises(SpatialError, match="当前房间"):
        execute_action(state, "bell_box", "examine", grant=grant)
    move(state, "bedroom")
    state["scene_visits"] = {"secret": {"visits": 1}}
    with pytest.raises(SpatialError) as exc:
        authorize_transition(state, "secret")
    assert exc.value.code == "unknown_destination"
    discover(state)
    assert "secret" in visible_destinations(state)
    with pytest.raises(SpatialError) as exc:
        authorize_transition(state, "secret")
    assert exc.value.code == "passage_locked"


def test_failure_cost_and_knowledge_do_not_create_physical_objects():
    state = world()
    move(state, "bedroom")
    receipt = discover(state)
    assert receipt["success"] is False
    assert state["case_clocks"]["pressure"] == 1
    assert state["flags"]["door_found"] is True
    assert state["pc"]["inventory"] == []
    assert any(
        c.get("catalog_id") == "score" for rows in state["clues_found"].values() for c in rows
    )
    with pytest.raises(SpatialError) as exc:
        discover(state)
    assert exc.value.code == "action_completed"
    assert state["case_clocks"]["pressure"] == 1


def test_failure_effects_rollback_when_cost_cannot_be_paid():
    state = world()
    move(state, "bedroom")
    state["case_clocks"]["pressure"] = 3
    proof = record(state)
    before = copy.deepcopy(state)
    with pytest.raises(SpatialError) as exc:
        execute_action(state, "fireplace", "search", proof=proof, grant=grant)
    assert exc.value.code == "clock_cost_unavailable"
    assert state == before


def test_charged_key_owned_instance_unlocks_once_and_survives_restore():
    state = world()
    execute_action(state, "cabinet", "take")
    move(state, "bedroom")
    discover(state, success=True)
    with pytest.raises(SpatialError):
        authorize_transition(state, "secret")
    move(state, "foyer")
    execute_action(state, "lamp", "charge")
    key = held_items(state, "charged_key", actor_id(state))[0]
    key["holder"] = {"kind": "scene", "id": "foyer"}
    move(state, "bedroom")
    with pytest.raises(SpatialError):
        authorize_transition(state, "secret")
    key = state["item_registry"]["items"][key["item_id"]]
    key["holder"] = {"kind": "investigator", "id": actor_id(state)}
    move(state, "secret")
    assert held_items(state, "charged_key", actor_id(state))[0]["states"]["charge"] == 0
    snapshot = copy.deepcopy(state)
    move(state, "bedroom")
    move(state, "secret")
    assert runtime(state)["connections"] == runtime(snapshot)["connections"]
    with pytest.raises(SpatialError):
        execute_action(state, "cabinet", "take")


def test_real_check_proof_required_and_never_reused_for_repeat_action():
    state = world()
    move(state, "bedroom")
    before = copy.deepcopy(state)
    with pytest.raises(SpatialError) as exc:
        execute_action(state, "fireplace", "search", proof=legacy_proof("fake"), grant=grant)
    assert exc.value.code == "invalid_check_proof"
    assert state == before
    state["spatial_definition"]["points"]["fireplace"]["actions"]["search"]["repeat"] = True
    execute_action(
        state, "fireplace", "search", proof=record(state, "one", success=True), grant=grant
    )
    execute_action(
        state, "fireplace", "search", proof=record(state, "two", success=True), grant=grant
    )
    with pytest.raises(SpatialError) as exc:
        execute_action(state, "fireplace", "search", proof=legacy_proof("one"), grant=grant)
    assert exc.value.code == "check_already_used"


def test_classic_state_guard_blocks_forged_unlock_and_discarded_key():
    state = world()
    execute_action(state, "cabinet", "take")
    changed = copy.deepcopy(state)
    runtime(changed)["connections"]["secret_door"] = {"unlocked": True}
    with pytest.raises(SpatialError):
        guard_update(state, changed)
    changed = copy.deepcopy(state)
    changed["pc"]["inventory"] = []
    guard_update(state, changed)
    assert not held_items(changed, "charged_key", actor_id(changed))


@pytest.fixture
def game(tmp_path):
    context = make_structured_world(tmp_path)
    state = world()
    state["investigators"] = {
        i: {**copy.deepcopy(state["pc"]), "id": i} for i in ("inv-alice", "inv-bob")
    }
    state["active_investigator_id"] = "inv-alice"
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        row.state = state
    return context, StructuredPlayService(context.database_url, rng=lambda n: 99 % n)


def saved(game):
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


def command(game, kind, payload, ident=None, principal=KEEPER):
    context, service = game
    return service.execute_command(
        world_id=context.world_id,
        principal=principal,
        kind=kind,
        payload=payload,
        command_id=ident or uuid.uuid4().hex,
        expected_revision=None,
    )


def test_structured_command_atomic_replay_permissions_and_check_receipt(game):
    payload = {"investigator_id": "inv-alice", "point_id": "cabinet", "action_id": "take"}
    first = command(game, "interact_point", payload, "take-key")
    assert command(game, "interact_point", payload, "take-key")["result"] == first["result"]
    assert len(held_items(saved(game)[0], "charged_key", "inv-alice")) == 1
    before = saved(game)
    with pytest.raises(StructuredError):
        command(
            game,
            "interact_point",
            {**payload, "action_id": "fake"},
            principal=Principal(kind="player", user_id="u-alice"),
        )
    assert saved(game) == before
    with patch.object(game[1], "_append_events", side_effect=RuntimeError("outbox failed")):
        with pytest.raises(RuntimeError):
            command(game, "interact_point", {**payload, "point_id": "lamp", "action_id": "charge"})
    assert saved(game) == before
    command(game, "move_party", {"destination_scene_id": "bedroom", "investigator_id": "inv-alice"})
    check = command(
        game,
        "request_check",
        {
            "investigator_id": "inv-alice",
            "skill": "spot_hidden",
            "difficulty": "regular",
            "attempt": "搜索壁炉锁板",
            "target": {"kind": "scene_object", "id": "fireplace:search"},
        },
    )["result"]["check_request_id"]
    game[1].submit_check_response(
        world_id=game[0].world_id,
        principal=Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        request={
            "request_id": "roll-point",
            "world_id": game[0].world_id,
            "check_request_id": check,
            "decision": "roll",
        },
    )
    response = command(
        game,
        "interact_point",
        {
            "investigator_id": "inv-alice",
            "point_id": "fireplace",
            "action_id": "search",
            "check_request_id": check,
        },
    )
    assert response["result"]["success"] is False
    assert saved(game)[0]["case_clocks"]["pressure"] == 1
    assert "spatial" in game[1].scene_notes_for_agent(game[0].world_id)[0]


def test_structured_direct_grant_and_remote_movement_blocked(game):
    before = saved(game)
    with pytest.raises(StructuredError) as exc:
        command(
            game,
            "grant_clue",
            {"clue_id": "score", "recipient_investigator_ids": ["inv-alice"], "basis": "跳过调查"},
        )
    assert exc.value.code == "point_action_required"
    with pytest.raises(StructuredError):
        command(game, "move_party", {"destination_scene_id": "secret"})
    assert saved(game) == before


def test_classic_tools_real_roll_and_store_restore(game):
    from src.ai.tools.registry import execute_function

    context, _ = game

    def invoke(name, args):
        return json.loads(execute_function(name, args, context=context))

    take = invoke("spatial_interact", {"point_id": "cabinet", "action_id": "take"})
    assert take["ok"]
    assert invoke("spatial_interact", {"point_id": "lamp", "action_id": "charge"})["ok"]
    origin = context.world_store.snapshot()
    context.world_store.update(lambda state: state.update(current_scene={"id": "bedroom"}))
    roll = invoke(
        "skill_check",
        {"skill": "spot_hidden", "approach": "仔细搜索壁炉的边缘", "target_id": "fireplace:search"},
    )
    assert roll["check_id"] in runtime(context.world_store.load())["checks"]
    result = invoke(
        "spatial_interact",
        {"point_id": "fireplace", "action_id": "search", "check_id": roll["check_id"]},
    )
    assert result["ok"]
    context.world_store.update(lambda state: state.update(current_scene={"id": "secret"}))
    assert context.world_store.load()["current_scene"]["id"] == "secret"
    context.world_store.restore(origin)
    restored = context.world_store.load()
    assert restored["current_scene"]["id"] == "foyer"
    assert runtime(restored)["connections"] == {}
    assert held_items(restored, "charged_key", "inv-alice")[0]["states"]["charge"] == 1
    before = context.world_store.load()
    with pytest.raises(SpatialError):
        with context.world_store.transaction() as state:
            state["current_scene"] = {"id": "secret"}
    assert context.world_store.load() == before


def test_essential_point_fallback_and_container_edge_validation():
    manifest, module = payloads()
    module["spatial"]["points"]["fireplace"]["actions"]["search"]["failure"] = []
    assert not compile_payload(manifest, module).ok
    manifest, module = payloads()
    module["entry_scene_id"] = "foyer"
    module["scenes"]["foyer"]["exits"] = []
    module["spatial"]["connections"]["hallway"] = {
        "source_scene_id": "house",
        "target_scene_id": "bedroom",
        "bidirectional": True,
    }
    preview = compile_payload(manifest, module)
    assert preview.ok, preview.to_dict(include_outputs=False)
    assert "bedroom" in visible_destinations(preview.result.world_state)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state["spatial_state"]["checks"].update({"fake": {"success": True}}),
        lambda state: state["flags"].update(door_found=True),
        lambda state: state["case_clocks"].update(pressure=2),
        lambda state: state["clues_found"]["investigation"].append(
            {"catalog_id": "score", "text": "伪造发现"}
        ),
    ],
)
def test_classic_raw_state_cannot_forge_authored_facts(mutation):
    before = world()
    after = copy.deepcopy(before)
    mutation(after)
    with pytest.raises(SpatialError):
        guard_update(before, after)


def test_legacy_world_keeps_old_tools_and_projection_behavior():
    from types import SimpleNamespace

    from src.ai.tools.spatial_tools import SPATIAL_TOOLS, filter_spatial_tools
    from src.gameplay.scene_projection import player_scene_view

    legacy = {"current_scene": {"id": "study", "name": "书房"}, "pc": {"hp": 10}}
    altered = copy.deepcopy(legacy)
    altered["current_scene"] = {"id": "elsewhere"}
    guard_update(legacy, altered)
    assert player_scene_view(legacy) == {"name": "书房"}
    assert (
        filter_spatial_tools(
            SPATIAL_TOOLS, SimpleNamespace(world_store=SimpleNamespace(load=lambda: legacy))
        )
        == []
    )
    assert (
        filter_spatial_tools(
            SPATIAL_TOOLS, SimpleNamespace(world_store=SimpleNamespace(load=world))
        )
        == SPATIAL_TOOLS
    )


def test_explicit_unlock_effect_opens_non_remembering_connection():
    manifest, module = payloads()
    module["spatial"]["connections"]["secret_door"]["remember_unlock"] = False
    module["spatial"]["points"]["fireplace"]["actions"]["open"] = {
        "intent": "use",
        "requires": {"op": "flag", "flag_id": "door_found", "value": True},
        "success": [{"kind": "unlock_connection", "connection_id": "secret_door"}],
    }
    preview = compile_payload(manifest, module)
    assert preview.ok
    state = preview.result.world_state
    move(state, "bedroom")
    discover(state, success=True)
    execute_action(state, "fireplace", "open")
    move(state, "secret")
    move(state, "bedroom")
    move(state, "secret")
    assert state["pc"]["inventory"] == []


def test_legacy_discovery_rule_cannot_bypass_protected_point():
    from src.structured.discoveries import verify_discovery
    from src.structured.domains import CommandContext

    state = world()
    move(state, "bedroom")
    state["clue_catalog"]["score"]["discovery_rules"] = [
        {"intent": "use", "requires_success": False}
    ]
    with pytest.raises(StructuredError) as exc:
        verify_discovery(
            state,
            "score",
            0,
            actor_id(state),
            "",
            CommandContext(world_id="test", principal=KEEPER),
            use=True,
        )
    assert exc.value.code == "point_action_required"
