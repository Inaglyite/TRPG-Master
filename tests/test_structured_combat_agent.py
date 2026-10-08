"""Scripted callers prove the combat harness barrier, not model quality."""

import asyncio
import copy
import json
from unittest.mock import patch

import pytest
from test_structured_combat_transactions import battle as battle
from test_structured_commands import make_structured_world

from src.storage.database import World, WorldState, session_scope
from src.structured.agent import AgentBudget, KeeperAgentRunner
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService, wire_envelope
from src.structured.validation import validate_event


@pytest.mark.parametrize("max_commands", [1, 20])
def test_agent_approval_stops_before_remaining_world_commands(battle, max_commands):
    url, service, alice, execute, snapshot, draws = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    with session_scope(url) as session:
        world = session.get(World, "sp-world")
        world.metadata_json = {**world.metadata_json, "keeper_mode": "agent"}
    execute("control_keeper", {"action": "release"})
    service.submit_action_request(
        world_id="sp-world",
        principal=alice,
        request={
            "request_id": "attack-intent",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "向守卫射击"},
        },
    )
    calls = []

    async def caller(system, context):
        calls.append(json.loads(context))
        return json.dumps(
            {
                "commands": [
                    {
                        "kind": "combat_action",
                        "payload": {
                            "actor_id": "inv-alice",
                            "target_id": "guard",
                            "action_type": "firearm",
                            "weapon": "手枪",
                            "damage_spec": "1d3",
                        },
                    },
                    {"kind": "advance_time", "payload": {"minutes": 60, "reason": "不应执行"}},
                    {
                        "kind": "resolve_intent",
                        "payload": {
                            "request_id": "attack-intent",
                            "resolution": "completed",
                            "outcome": "success",
                        },
                    },
                ],
                "narration": "你举起手枪，等待掷骰。",
            },
            ensure_ascii=False,
        )

    before_draws = len(draws)
    before_time = snapshot()[0].get("world_clock", {}).get("elapsed_minutes", 0)
    events = []

    async def deliver(event):
        events.append(event)

    result = asyncio.run(
        KeeperAgentRunner(url, caller=caller, budget=AgentBudget(max_commands=max_commands)).run(
            world_id="sp-world", trigger_request_id="attack-intent", deliver=deliver
        )
    )
    assert result.stop_reason == "wait_combat_player"
    assert result.awaiting_parked
    assert len(calls) == 1
    state = snapshot()[0]
    assert state["combat_pending_roll"]
    assert state.get("world_clock", {}).get("elapsed_minutes", 0) == before_time
    assert len(draws) == before_draws
    if max_commands > 1:
        assert any(
            e["type"] == "message_completed" and e["payload"]["text"] == "你举起手枪，等待掷骰。"
            for e in events
        )
    assert any(
        e["type"] == "action_status" and e["payload"]["status"] == "awaiting_player" for e in events
    )


def test_gateway_resumes_after_committed_roll_but_not_replay(battle):
    url, _, _, _, snapshot, _ = battle
    gateway = StructuredGateway(url)
    frame = {
        "type": "command_request",
        "protocol_version": 1,
        "world_id": "sp-world",
        "command_id": "resume-roll",
        "expected_revision": snapshot()[1],
        "kind": "combat_roll",
        "payload": {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
    }
    delivered = []

    async def deliver(event):
        delivered.append(event)

    with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
        asyncio.run(
            gateway.handle_frame(
                world_id="sp-world", user_id="u-alice", frame=frame, deliver=deliver
            )
        )
        assert schedule.call_count == 1
        assert schedule.call_args.kwargs["trigger_request_id"] == ""
        asyncio.run(
            gateway.handle_frame(
                world_id="sp-world", user_id="u-alice", frame=frame, deliver=deliver
            )
        )
        assert schedule.call_count == 1
    assert any(e["type"] == "combat_roll_resolved" for e in delivered)

    async def unused_caller(system, context):
        raise AssertionError("context inspection must not call a model")

    context = json.loads(
        KeeperAgentRunner(url, caller=unused_caller)._build_context("sp-world", "", [])
    )
    receipt = next(r for r in context["recent_combat_results"] if r["command_id"] == "resume-roll")
    assert receipt["kind"] == "combat_roll"
    assert receipt["result"]["attack_roll"]
    assert len(context["recent_combat_results"]) <= 8


def test_gateway_defense_choice_does_not_resume_before_player_roll(battle):
    url, _, alice, execute, snapshot, _ = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        combat = state["combat_state"]
        combat["current_actor"] = "guard"
        combat["turn_index"] = combat["turn_order"].index("guard")
        row.state = state
    execute(
        "combat_action",
        {
            "actor_id": "guard",
            "target_id": "inv-alice",
            "action_type": "firearm",
            "damage_spec": "1d3",
        },
    )
    state, revision, *_ = snapshot()
    frame = {
        "type": "command_request",
        "protocol_version": 1,
        "world_id": "sp-world",
        "command_id": "defense-resume",
        "expected_revision": revision,
        "kind": "combat_decide",
        "payload": {
            "decision_id": state["combat_state"]["pending_decision"]["id"],
            "option_id": "no_defense",
        },
    }

    async def deliver(event):
        pass

    with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
        asyncio.run(
            StructuredGateway(url).handle_frame(
                world_id="sp-world", user_id="u-alice", frame=frame, deliver=deliver
            )
        )
        schedule.assert_not_called()
    assert snapshot()[0]["combat_pending_roll"]


def test_committed_move_refreshes_public_npc_candidates_without_reconnect(tmp_path):
    runtime = make_structured_world(tmp_path)
    service = StructuredPlayService(runtime.database_url)
    outcome = service.execute_command(
        world_id="sp-world",
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="move_party",
        payload={"destination_scene_id": "library"},
        command_id="candidate-move",
        expected_revision=None,
    )
    targets = next(
        e["payload"]["targets"]
        for e in outcome["events"]
        if e["type"] == "state_changed" and "targets" in e["payload"]
    )
    assert {t["id"] for t in targets if t["kind"] == "npc"} == {"keeper_npc"}
    assert all(set(t) == {"id", "name", "kind"} for t in targets)
    for event in outcome["events"]:
        validate_event(wire_envelope(event))
