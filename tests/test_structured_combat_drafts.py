"""Approved batches share domain guards/inventory, without replacing player consent."""

import asyncio
import copy
import json
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import PlayerRequest, WorldMember, WorldState, session_scope
from src.structured.agent import KeeperAgentRunner
from src.structured.errors import StructuredError
from src.structured.execution import INVENTORY_COMBAT_COMMANDS
from src.structured.service import _COMBAT_COMMANDS


@pytest.fixture
def draft_world(battle):
    _, _, alice, execute, snapshot, _ = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    return battle


def command(kind, **payload):
    return {"kind": kind, "payload": payload}


def draft(battle, commands, related=""):
    _, service, _, _, _, _ = battle
    return service.create_keeper_draft(
        world_id="sp-world",
        summary="战斗建议",
        proposed_commands=commands,
        related_request_id=related,
        narration="",
    )


def approve(battle, drafted, decision="approved", command_id=None):
    return battle[3](
        "resolve_draft",
        {"draft_id": drafted["draft_id"], "decision": decision},
        command_id=command_id,
    )


def status(url, draft_id):
    with session_scope(url) as session:
        return session.scalar(
            select(PlayerRequest).where(PlayerRequest.request_id == draft_id)
        ).status


@pytest.mark.parametrize(
    "next_command",
    [
        command("move_party", destination_scene_id="library"),
        command("advance_time", minutes=60, reason="不应跨过玩家决定"),
        command(
            "resolve_intent", request_id="attack-draft", resolution="completed", outcome="success"
        ),
        command("combat_end", reason="不能自动代替玩家结束对抗"),
    ],
)
def test_approval_rejects_the_whole_batch_if_it_crosses_player_wait(draft_world, next_command):
    url, service, alice, _, snapshot, draws = draft_world
    service.submit_action_request(
        world_id="sp-world",
        principal=alice,
        request={
            "request_id": "attack-draft",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "准备射击守卫。"},
        },
    )
    drafted = draft(
        draft_world,
        [
            command(
                "combat_action",
                actor_id="inv-alice",
                target_id="guard",
                action_type="firearm",
                weapon="手枪",
            ),
            next_command,
        ],
        "attack-draft",
    )
    before = snapshot()
    with pytest.raises(StructuredError, match="整份草稿未执行"):
        approve(draft_world, drafted)
    assert snapshot() == before and draws == []
    assert status(url, drafted["draft_id"]) == "queued"


def test_valid_approval_parks_then_actual_player_roll_spends_ammo_once(draft_world):
    url, service, alice, execute, snapshot, draws = draft_world
    service.submit_action_request(
        world_id="sp-world",
        principal=alice,
        request={
            "request_id": "attack-draft",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "准备射击守卫，再检查遗物。"},
        },
    )
    drafted = draft(
        draft_world,
        [
            command(
                "combat_action",
                actor_id="inv-alice",
                target_id="guard",
                action_type="firearm",
                weapon="手枪",
                damage_spec="1d3",
            ),
            command(
                "publish_message",
                speaker={"kind": "keeper"},
                audience={"kind": "public"},
                text="你举起手枪，等待自己的掷骰。",
            ),
            command(
                "resolve_intent",
                request_id="attack-draft",
                resolution="awaiting_player",
                outcome="not_executed",
            ),
        ],
        "attack-draft",
    )
    result = approve(draft_world, drafted, command_id="approve-once")
    state = snapshot()[0]
    assert draws == [] and state["pc"]["inventory"] == ["手枪（3发）"]
    assert state["combat_pending_roll"]["source_request_id"] == "attack-draft"
    assert status(url, "attack-draft") == "awaiting_player"
    assert status(url, drafted["draft_id"]) == "completed"
    after = snapshot()
    assert approve(draft_world, drafted, command_id="approve-once")["deduplicated"]
    assert snapshot() == after
    rolled = execute(
        "combat_roll",
        {"roll_id": state["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
        "owner-roll-once",
    )
    assert rolled["result"]["source_request_id"] == "attack-draft"
    assert snapshot()[0]["pc"]["inventory"] == ["手枪（2发）"]
    assert len(result["result"]["executed_commands"]) == 3


def test_draft_npc_resolution_syncs_roster_and_private_stat_events(draft_world):
    url, _, _, execute, snapshot, _ = draft_world
    while snapshot()[0]["combat_state"]["current_actor"] != "guard":
        execute(
            "combat_action",
            {
                "actor_id": snapshot()[0]["combat_state"]["current_actor"],
                "action_type": "other",
                "description": "观察对手",
            },
        )
    # An uncontrolled investigator uses the existing automatic defensive rule.
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["investigator_controllers"].pop("u-alice")
        state["pc"]["controller_user_id"] = None
        state["investigators"]["inv-alice"]["controller_user_id"] = None
        row.state = state
    hp_before = snapshot()[0]["pc"]["hp"]
    drafted = draft(
        draft_world,
        [
            command(
                "combat_action",
                actor_id="guard",
                target_id="inv-alice",
                action_type="firearm",
                damage_spec="1d3",
            )
        ],
    )
    result = approve(draft_world, drafted)
    state = snapshot()[0]
    assert state["pc"]["hp"] < hp_before
    assert state["investigators"]["inv-alice"]["hp"] == state["pc"]["hp"]
    stats = [e for e in result["events"] if e["type"] == "state_changed"]
    assert stats and all(e["audience"]["kind"] != "public" for e in stats)
    assert {e["payload"]["investigator_id"] for e in stats} == {"inv-alice"}


def test_nested_ending_does_not_allow_post_ending_world_changes(draft_world):
    _, _, _, execute, snapshot, _ = draft_world
    execute("combat_end", {"reason": "准备结案"})
    drafted = draft(
        draft_world,
        [
            command("end_game", ending_type="neutral", title="离开调查"),
            command("advance_time", minutes=60, reason="结案后不应生效"),
        ],
    )
    before = snapshot()
    with pytest.raises(StructuredError, match="游戏已结束"):
        approve(draft_world, drafted)
    assert snapshot() == before
    assert not snapshot()[0].get("game_over")


def test_nested_ending_then_closing_narration_is_valid(draft_world):
    _, _, _, execute, snapshot, _ = draft_world
    execute("combat_end", {"reason": "准备结案"})
    drafted = draft(
        draft_world,
        [
            command("end_game", ending_type="neutral", title="离开调查"),
            command(
                "publish_message",
                speaker={"kind": "keeper"},
                audience={"kind": "public"},
                text="你们整理留下的记录。",
            ),
        ],
    )
    approve(draft_world, drafted)
    assert snapshot()[0]["game_over"]["type"] == "neutral"


def test_outbox_failure_rolls_back_nested_combat_and_draft_status(draft_world):
    url, service, _, _, snapshot, _ = draft_world
    drafted = draft(
        draft_world,
        [
            command(
                "combat_action",
                actor_id="inv-alice",
                target_id="guard",
                action_type="firearm",
                weapon="手枪",
            )
        ],
    )
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("draft outbox failure")):
        with pytest.raises(RuntimeError):
            approve(draft_world, drafted)
    assert snapshot() == before
    assert status(url, drafted["draft_id"]) == "queued"


def test_revoked_keeper_cannot_replay_a_private_approved_draft(draft_world):
    url, _, _, _, snapshot, _ = draft_world
    drafted = draft(draft_world, [])
    approve(draft_world, drafted, command_id="private-approval")
    with session_scope(url) as session:
        member = session.scalar(
            select(WorldMember).where(
                WorldMember.world_id == "sp-world", WorldMember.user_id == "u-keeper"
            )
        )
        member.can_keeper = False
    before = snapshot()
    with pytest.raises(StructuredError):
        approve(draft_world, drafted, command_id="private-approval")
    assert snapshot() == before


def test_player_cannot_approve_a_draft_and_inventory_bridge_matches_combat_domain(draft_world):
    battle = draft_world
    assert INVENTORY_COMBAT_COMMANDS == _COMBAT_COMMANDS - {"combat_end", "end_game"}
    drafted = draft(battle, [])
    before = battle[4]()
    with pytest.raises(StructuredError):
        battle[3](
            "resolve_draft", {"draft_id": drafted["draft_id"], "decision": "approved"}, battle[2]
        )
    assert battle[4]() == before


@pytest.mark.parametrize(
    "commands",
    [
        ["malformed-secret-canary"],
        [{"kind": "combat_action", "payload": "malformed-secret-canary"}],
        [command("combat_action", actor_id="inv-alice")],
        [command("combat_roll", roll_id="invented", response="roll")],
        [
            command(
                "record_condition",
                investigator_id="inv-alice",
                condition="unconscious",
                operation="remove",
                expected_present=True,
                basis="malformed-secret-canary",
            )
        ],
        [
            command(
                "record_ruling",
                flag_id="x",
                value=True,
                expected_before=False,
                basis="malformed-secret-canary",
            )
        ],
        [command("advance_time", minutes=1, reason="预算探针")] * 13,
    ],
)
def test_invalid_model_draft_pauses_visibly_instead_of_publishing_bad_event(draft_world, commands):
    url, service, alice, _, snapshot, draws = draft_world
    service.submit_action_request(
        world_id="sp-world",
        principal=alice,
        request={
            "request_id": "bad-draft-trigger",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "请处理我的行动。"},
        },
    )
    before = snapshot()[0]
    calls = []
    delivered = []

    async def caller(system, context):
        calls.append(context)
        return json.dumps({"commands": commands, "narration": ""})

    async def deliver(event):
        delivered.append(event)

    result = asyncio.run(
        KeeperAgentRunner(url, caller=caller).run_assisted(
            world_id="sp-world", trigger_request_id="bad-draft-trigger", deliver=deliver
        )
    )
    assert result.status == "paused" and result.stop_reason == "draft_unavailable:invalid_draft"
    assert len(calls) == 1 and draws == [] and snapshot()[0] == before
    assert status(url, "bad-draft-trigger") == "paused"
    assert not any(event["type"] == "keeper_draft" for event in delivered)
    assert any(
        event["type"] == "action_status" and event["payload"]["status"] == "paused"
        for event in delivered
    )
    assert "malformed-secret-canary" not in json.dumps(delivered)


def test_scripted_assisted_producer_then_human_approval_then_player_roll(draft_world):
    url, service, alice, execute, snapshot, draws = draft_world
    service.submit_action_request(
        world_id="sp-world",
        principal=alice,
        request={
            "request_id": "valid-assisted-trigger",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "我举枪准备射击。"},
        },
    )

    async def caller(system, context):
        return json.dumps(
            {
                "assessment": "准备动作，等待玩家掷骰。",
                "commands": [
                    command(
                        "combat_action",
                        actor_id="inv-alice",
                        target_id="guard",
                        action_type="firearm",
                        weapon="手枪",
                        damage_spec="1d3",
                    ),
                    command(
                        "resolve_intent",
                        request_id="valid-assisted-trigger",
                        resolution="awaiting_player",
                        outcome="not_executed",
                    ),
                ],
                "narration": "你举起手枪，尚未开火。",
            }
        )

    before = snapshot()[0]
    result = asyncio.run(
        KeeperAgentRunner(url, caller=caller).run_assisted(
            world_id="sp-world", trigger_request_id="valid-assisted-trigger"
        )
    )
    assert result.stop_reason == "draft_ready" and draws == [] and snapshot()[0] == before
    with session_scope(url) as session:
        row = session.scalar(
            select(PlayerRequest).where(PlayerRequest.request_type == "keeper_draft")
        )
        draft_id = row.request_id
    approve(draft_world, {"draft_id": draft_id})
    assert draws == []
    roll = snapshot()[0]["combat_pending_roll"]
    execute("combat_roll", {"roll_id": roll["roll_id"], "response": "roll"}, alice)
    assert snapshot()[0]["pc"]["inventory"] == ["手枪（2发）"]
