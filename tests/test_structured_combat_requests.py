"""Causality survives player response; a compound freeform stays GM-controlled."""

import asyncio
from unittest.mock import patch

from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import PlayerRequest, session_scope
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal


def test_roll_resumes_original_intent_but_does_not_complete_compound_request(battle):
    url, service, alice, execute, snapshot, _ = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    for request_id, principal, investigator in (
        ("original", alice, "inv-alice"),
        (
            "unrelated",
            Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",)),
            "inv-bob",
        ),
    ):
        service.submit_action_request(
            world_id="sp-world",
            principal=principal,
            request={
                "request_id": request_id,
                "investigator_id": investigator,
                "action": {"kind": "freeform", "text": "先攻击，再继续调查房间。"},
            },
        )
    service.execute_command(
        world_id="sp-world",
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="combat_action",
        payload={
            "actor_id": "inv-alice",
            "target_id": "guard",
            "action_type": "firearm",
            "weapon": "手枪",
            "damage_spec": "1d3",
        },
        command_id="linked-prep",
        expected_revision=None,
        cause_id="original",
    )
    assert snapshot()[0]["combat_pending_roll"]["source_request_id"] == "original"
    frame = {
        "type": "command_request",
        "protocol_version": 1,
        "world_id": "sp-world",
        "command_id": "linked-roll",
        "expected_revision": snapshot()[1],
        "kind": "combat_roll",
        "cause_id": "unrelated",
        "payload": {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
    }

    async def deliver(event):
        pass

    with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
        asyncio.run(
            StructuredGateway(url).handle_frame(
                world_id="sp-world", user_id="u-alice", frame=frame, deliver=deliver
            )
        )
        assert schedule.call_count == 1
        assert schedule.call_args.kwargs["trigger_request_id"] == "original"
    with session_scope(url) as session:
        rows = session.scalars(
            select(PlayerRequest).where(PlayerRequest.world_id == "sp-world")
        ).all()
        assert {r.request_id: r.status for r in rows if r.request_type == "action_request"} == {
            "original": "queued",
            "unrelated": "queued",
        }


def test_wrong_investigator_cause_does_not_bind_combat_pending(battle):
    _, service, alice, execute, snapshot, _ = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
    service.submit_action_request(
        world_id="sp-world",
        principal=bob,
        request={
            "request_id": "bob-intent",
            "investigator_id": "inv-bob",
            "action": {"kind": "freeform", "text": "另一个行动"},
        },
    )
    service.execute_command(
        world_id="sp-world",
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="combat_action",
        payload={
            "actor_id": "inv-alice",
            "target_id": "guard",
            "action_type": "firearm",
            "weapon": "手枪",
            "damage_spec": "1d3",
        },
        command_id="wrong-link",
        expected_revision=None,
        cause_id="bob-intent",
    )
    assert snapshot()[0]["combat_pending_roll"]["source_request_id"] == ""
