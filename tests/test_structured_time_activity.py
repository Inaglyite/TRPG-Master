"""Host time uses a typed activity, never parses the prose reason."""

import copy
import json

import pytest
from test_case_clock_time import clock_world
from test_structured_commands import make_structured_world

from src.app.config import PROJECT_ROOT
from src.storage.database import WorldState, session_scope
from src.structured.domains import TIME_ACTIVITIES, CommandContext, cmd_advance_time
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService
from src.structured.validation import SCHEMA_DIR, validate_command


def test_activity_definition_matches_the_protocol():
    schema = json.loads((SCHEMA_DIR / "command_request.json").read_text(encoding="utf-8"))
    field = schema["$defs"]["advance_time"]["properties"]["payload"]["properties"]["activity"]
    assert field["enum"] == list(TIME_ACTIVITIES)
    assert field["default"] == "wait"


def test_actual_scarlet_time_rule_works_without_changing_the_module():
    state = json.loads(
        (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json").read_text(encoding="utf-8")
    )
    ctx = CommandContext(world_id="isolated", principal=Principal(kind="keeper", user_id="keeper"))
    result = cmd_advance_time(state, {"minutes": 2880, "reason": "主持确认这次等待已完成。"}, ctx)
    assert state["case_clocks"]["monster_manifestation"] == 1
    assert state["case_clocks"]["human_pressure"] == 0
    assert state["case_clocks"]["clue_clarity"] == 0
    assert result.result["clock_events"][0]["target"] == "monster_manifestation"


@pytest.fixture
def timed_world(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        rules = clock_world()
        for key in ("case_clocks", "case_clock_definitions"):
            state[key] = rules[key]
        row.state = state
    return context


def execute(context, command_id, payload):
    return StructuredPlayService(context.database_url).execute_command(
        world_id=context.world_id,
        principal=Principal(kind="keeper", user_id="u-keeper"),
        command_id=command_id,
        kind="advance_time",
        payload=payload,
        expected_revision=None,
    )


def read_state(context):
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


@pytest.mark.parametrize("activity", [None, "wait"])
def test_wait_reaches_threshold_with_freeform_reason_and_replay_is_idempotent(
    timed_world,
    activity,
):
    payload = {"minutes": 1440, "reason": "记录这段跨日时间，不解析我写的说明。"}
    if activity is not None:
        payload["activity"] = activity
    validate_command("advance_time", payload)
    first = execute(timed_world, "day-one", payload)
    state, _ = read_state(timed_world)
    assert state["case_clocks"]["doom"] == 0
    assert state["case_clock_time"]["doom"]["carry"] == 1440
    assert first["result"]["activity"] == "wait"
    second = execute(timed_world, "day-two", payload)
    state, revision = read_state(timed_world)
    assert state["world_clock"]["elapsed_minutes"] == 2880
    assert state["case_clocks"] == {"doom": 1, "pressure": 0}
    assert second["result"]["reason"] == payload["reason"]
    assert second["result"]["clock_events"][0]["source"] == "time"
    # Private clock identities now have an explicit keeper-only live projection,
    # but never enter a player/public event or the player's replay stream.
    assert all(
        "doom" not in str(e["payload"])
        for e in second["events"]
        if e["audience"]["kind"] != "keeper"
    )
    private = next(e for e in second["events"] if e["type"] == "keeper_progress_updated")
    assert private["audience"] == {"kind": "keeper"}
    assert next(c for c in private["payload"]["clocks"] if c["id"] == "doom")["value"] == 1
    player = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
    replay = StructuredPlayService(timed_world.database_url).replay_events(
        world_id=timed_world.world_id, after_sequence=0, principal=player
    )
    assert all("doom" not in str(e["payload"]) for e in replay)
    assert execute(timed_world, "day-two", payload)["deduplicated"] is True
    assert read_state(timed_world) == (state, revision)


@pytest.mark.parametrize("activity", ["travel", "check", "interact", "combat", "other"])
def test_nonwait_does_not_count_as_wait_even_if_reason_mentions_wait(timed_world, activity):
    payload = {"minutes": 2880, "activity": activity, "reason": "wait 等待两天（只是说明）"}
    validate_command("advance_time", payload)
    result = execute(timed_world, "nonwait", payload)
    state, _ = read_state(timed_world)
    assert result["result"]["activity"] == activity
    assert "clock_events" not in result["result"]
    assert state["world_clock"]["elapsed_minutes"] == 2880
    assert state["case_clocks"]["doom"] == 0
    assert state["current_scene"]["id"] == "study"
    assert state["combat_state"]["active"] is False
    execute(timed_world, "later-wait", {"minutes": 1440, "reason": "稍后明确等待。"})
    state, _ = read_state(timed_world)
    assert state["case_clocks"]["doom"] == 0
    assert state["case_clock_time"]["doom"]["carry"] == 1440


def test_module_can_explicitly_count_travel_without_moving_the_party(timed_world):
    with session_scope(timed_world.database_url) as session:
        row = session.get(WorldState, timed_world.world_id)
        state = copy.deepcopy(row.state)
        state["case_clock_definitions"]["doom"]["time_advance"]["activity"] = ["travel"]
        row.state = state
    result = execute(
        timed_world,
        "travel-rule",
        {
            "minutes": 2880,
            "activity": "travel",
            "reason": "等待（说明不影响规则类型）",
        },
    )
    state, _ = read_state(timed_world)
    assert state["case_clocks"]["doom"] == 1
    assert state["current_scene"]["id"] == "study"
    assert result["result"]["activity"] == "travel"


def test_clock_settlement_rolls_back_with_command_publication_failure(timed_world, monkeypatch):
    service = StructuredPlayService(timed_world.database_url)
    before = read_state(timed_world)

    def fail_publication(*args, **kwargs):
        raise RuntimeError("publication transaction failed")

    monkeypatch.setattr(service, "_append_events", fail_publication)
    with pytest.raises(RuntimeError, match="publication transaction failed"):
        service.execute_command(
            world_id=timed_world.world_id,
            principal=Principal(kind="keeper", user_id="u-keeper"),
            command_id="atomic-clock",
            kind="advance_time",
            payload={"minutes": 2880, "activity": "wait", "reason": "已完成等待。"},
            expected_revision=None,
        )
    assert read_state(timed_world) == before


@pytest.mark.parametrize("activity", [True, None, 12, "", "waiting", "等两天", {}, []])
def test_invalid_explicit_activity_is_rejected_before_state_change(activity):
    state = clock_world()
    before = copy.deepcopy(state)
    payload = {"minutes": 2880, "reason": "wait", "activity": activity}
    with pytest.raises(StructuredError) as wire_error:
        validate_command("advance_time", payload)
    assert wire_error.value.code == "invalid_action"
    with pytest.raises(StructuredError) as domain_error:
        cmd_advance_time(
            state,
            payload,
            CommandContext(world_id="test", principal=Principal(kind="keeper", user_id="keeper")),
        )
    assert domain_error.value.code == "invalid_action"
    assert state == before
