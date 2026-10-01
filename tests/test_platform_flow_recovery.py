"""平台交互修复：真实临时数据库，验证执行、回滚、权限与失败投递。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_structured_agent import _collector, _ScriptedCaller
from test_structured_commands import make_structured_world

from src.multiplayer.keeper_http import set_keeper_authorization
from src.multiplayer.service import MultiplayerError
from src.storage.database import GameCommand, PlayerRequest, World, session_scope
from src.storage.database_store import DatabaseWorldStore
from src.structured.agent import KeeperAgentRunner
from src.structured.errors import StructuredError
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal, bind_agent_control, current_control
from src.structured.service import StructuredPlayService


@pytest.fixture
def game(tmp_path):
    context = make_structured_world(tmp_path)
    return context, StructuredPlayService(context.database_url)


def test_capabilities_advertise_every_implemented_command(game):
    from src.structured.service import _KIND_HANDLERS

    _context, service = game
    capabilities = service.session_snapshot(world_id="sp-world", principal=KEEPER)[
        "server_capabilities"
    ]
    assert set(capabilities["commands"]) == set(_KIND_HANDLERS)


def test_pending_draft_restores_for_keeper_but_not_player(game):
    _context, service = game
    request(service)
    draft_id = draft(service)
    restored = service.session_snapshot(world_id="sp-world", principal=KEEPER)
    assert restored["keeper_drafts"][0]["draft_id"] == draft_id
    assert restored["keeper_drafts"][0]["proposed_commands"][0]["kind"] == "move_party"
    assert service.session_snapshot(world_id="sp-world", principal=ALICE)["keeper_drafts"] == []
    execute(service, "resolve_draft", {"draft_id": draft_id, "decision": "rejected"})
    assert service.session_snapshot(world_id="sp-world", principal=KEEPER)["keeper_drafts"] == []


def test_replayed_retry_ack_does_not_schedule_model_twice(game):
    context, service = game
    request(service)
    execute(service, "resolve_intent", {"request_id": "player-1", "resolution": "paused"}, "pause")
    with session_scope(context.database_url) as session:
        world = session.get(World, "sp-world")
        world.metadata_json = {**world.metadata_json, "keeper_mode": "agent"}
    gateway = StructuredGateway(context.database_url)
    frame = {
        "type": "command_request",
        "protocol_version": 1,
        "world_id": "sp-world",
        "command_id": "retry-1",
        "kind": "control_keeper",
        "expected_revision": snapshot(context).revision,
        "payload": {"action": "retry", "request_id": "player-1"},
    }
    frames = []

    async def replay():
        for _ in range(2):
            await gateway.handle_frame(
                world_id="sp-world", user_id="u-keeper", frame=frame, deliver=_collector(frames)
            )

    with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
        asyncio.run(replay())
        assert schedule.call_count == 1
    assert status(context, "player-1") == "queued"


def test_keeper_http_route_authenticates_projects_and_audits(game, monkeypatch):
    from src.multiplayer.keeper_http import register_keeper_routes
    from src.multiplayer.service import room_members

    context, _service = game

    async def no_room(_world):
        return None

    deps = SimpleNamespace(
        database_url=lambda: context.database_url, room_manager=lambda: SimpleNamespace(get=no_room)
    )
    monkeypatch.setattr(
        "src.multiplayer.keeper_http.request_user",
        lambda request, _db: (
            SimpleNamespace(id=request.headers["x-test-user"])
            if "x-test-user" in request.headers
            else None
        ),
    )
    app, router = FastAPI(), APIRouter()
    register_keeper_routes(router, deps)
    app.include_router(router)
    url = "/api/worlds/sp-world/members/u-bob/keeper"
    with TestClient(app) as client:
        assert client.patch(url, json={"can_keeper": True}).status_code == 401
        assert (
            client.patch(
                url, headers={"x-test-user": "u-alice"}, json={"can_keeper": True}
            ).status_code
            == 403
        )
        assert (
            client.patch(
                url, headers={"x-test-user": "u-owner"}, json={"can_keeper": "true"}
            ).status_code
            == 400
        )
        response = client.patch(url, headers={"x-test-user": "u-owner"}, json={"can_keeper": True})
        assert response.status_code == 200 and response.json()["can_keeper"] is True
    member = next(
        m
        for m in room_members(context.database_url, "sp-world", "u-owner")["members"]
        if m["user_id"] == "u-bob"
    )
    assert member["can_keeper"] is True and member["role"] == "player"


KEEPER = Principal(kind="keeper", user_id="u-keeper")
ALICE = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))


def execute(service, kind, payload, command_id="command-1", principal=KEEPER):
    return service.execute_command(
        world_id="sp-world",
        principal=principal,
        kind=kind,
        payload=payload,
        command_id=command_id,
        expected_revision=None,
    )


def snapshot(context):
    return DatabaseWorldStore(context.database_url, "sp-world", context.world_dir).snapshot()


def status(context, request_id):
    with session_scope(context.database_url) as session:
        return session.execute(
            select(PlayerRequest.status).where(
                PlayerRequest.world_id == "sp-world",
                PlayerRequest.request_id == request_id,
            )
        ).scalar_one()


def request(service, kind="move"):
    service.submit_action_request(
        world_id="sp-world",
        principal=ALICE,
        request={
            "request_id": "player-1",
            "investigator_id": "inv-alice",
            "action": {"kind": "move", "destination_scene_id": "library"}
            if kind == "move"
            else {"kind": "freeform", "text": "去图书馆后调查书架"},
        },
    )


def draft(service, commands=None):
    return service.create_keeper_draft(
        world_id="sp-world",
        summary="前往图书馆",
        proposed_commands=commands
        if commands is not None
        else [
            {
                "kind": "move_party",
                "payload": {"destination_scene_id": "library", "travel_minutes": 10},
            }
        ],
        narration="你走进图书馆。",
        related_request_id="player-1",
    )["draft_id"]


def test_approval_executes_saved_commands_narration_and_is_idempotent(game):
    context, service = game
    request(service)
    draft_id = draft(service)
    before = snapshot(context)
    assert before.state["current_scene"]["id"] == "study"
    approved = execute(service, "resolve_draft", {"draft_id": draft_id, "decision": "approved"})
    after = snapshot(context)
    assert after.state["current_scene"]["id"] == "library"
    assert after.revision == before.revision + 1
    assert status(context, draft_id) == "completed"
    assert status(context, "player-1") == "completed"
    assert any(event["type"] == "scene_changed" for event in approved["events"])
    assert any(
        event["type"] == "message_completed" and event["payload"]["text"] == "你走进图书馆。"
        for event in approved["events"]
    )
    replay = execute(service, "resolve_draft", {"draft_id": draft_id, "decision": "approved"})
    assert replay["deduplicated"] and replay["events"] == []
    assert snapshot(context).revision == after.revision
    with pytest.raises(StructuredError):
        execute(
            service,
            "resolve_draft",
            {"draft_id": draft_id, "decision": "approved"},
            "another-approval",
        )


def test_approval_failure_rolls_back_prior_commands_and_keeps_draft(game):
    context, service = game
    request(service)
    draft_id = draft(
        service,
        [
            {"kind": "move_party", "payload": {"destination_scene_id": "library"}},
            {"kind": "move_party", "payload": {"destination_scene_id": "missing"}},
        ],
    )
    before = snapshot(context)
    with pytest.raises(StructuredError):
        execute(service, "resolve_draft", {"draft_id": draft_id, "decision": "approved"})
    after = snapshot(context)
    assert after.revision == before.revision and after.state == before.state
    assert status(context, draft_id) == "queued"
    assert status(context, "player-1") == "queued"
    with session_scope(context.database_url) as session:
        assert session.execute(select(GameCommand)).scalars().all() == []


@pytest.mark.parametrize("decision", ["rejected", "edited"])
def test_non_approval_never_executes(game, decision):
    context, service = game
    request(service)
    draft_id = draft(service)
    execute(service, "resolve_draft", {"draft_id": draft_id, "decision": decision})
    assert snapshot(context).state["current_scene"]["id"] == "study"
    assert status(context, "player-1") == "queued"


def test_stale_draft_cannot_execute_against_changed_world(game):
    context, service = game
    draft_id = draft(service)
    execute(service, "advance_time", {"minutes": 5, "reason": "先整理装备"}, "time")
    with pytest.raises(StructuredError) as exc:
        execute(service, "resolve_draft", {"draft_id": draft_id, "decision": "approved"})
    assert exc.value.code == "revision_conflict"
    assert snapshot(context).state["current_scene"]["id"] == "study"


def test_assisted_draft_keeps_model_input_revision_during_concurrent_change(game):
    context, service = game
    request(service)

    async def caller(_system, _context):
        execute(service, "advance_time", {"minutes": 5, "reason": "整理装备"}, "during-model")
        return json.dumps(
            {
                "commands": [
                    {"kind": "move_party", "payload": {"destination_scene_id": "library"}}
                ],
                "narration": "你抵达图书馆。",
            }
        )

    runner = KeeperAgentRunner(context.database_url, caller=caller)
    asyncio.run(runner.run_assisted(world_id="sp-world", trigger_request_id="player-1"))
    pending = service.session_snapshot(world_id="sp-world", principal=KEEPER)["keeper_drafts"]
    draft_id = pending[0]["draft_id"]
    with pytest.raises(StructuredError) as exc:
        execute(service, "resolve_draft", {"draft_id": draft_id, "decision": "approved"})
    assert exc.value.code == "revision_conflict"
    assert status(context, draft_id) == "queued"
    assert snapshot(context).state["current_scene"]["id"] == "study"


def test_arrival_does_not_finish_compound_investigation(game):
    context, service = game
    request(service, "freeform")
    execute(service, "resolve_draft", {"draft_id": draft(service), "decision": "approved"})
    assert snapshot(context).state["current_scene"]["id"] == "library"
    assert status(context, "player-1") == "queued"


def test_takeover_fences_old_agent_and_owner_is_not_keeper(game):
    context, service = game
    with session_scope(context.database_url) as session:
        bind_agent_control(session, "sp-world", "old-run")
    with pytest.raises(StructuredError) as exc:
        execute(
            service,
            "control_keeper",
            {"action": "take"},
            principal=Principal(kind="keeper", user_id="u-owner"),
        )
    assert exc.value.code == "keeper_required"
    execute(service, "control_keeper", {"action": "take"})
    with pytest.raises(StructuredError) as exc:
        execute(
            service,
            "move_party",
            {"destination_scene_id": "library"},
            "old-agent",
            principal=Principal(kind="agent", run_id="old-run"),
        )
    assert exc.value.code == "controller_epoch_stale"
    assert snapshot(context).state["current_scene"]["id"] == "study"
    execute(service, "move_party", {"destination_scene_id": "library"}, "human-move")
    assert snapshot(context).state["current_scene"]["id"] == "library"


def test_retry_requeues_only_failed_request_and_releases_control(game):
    context, service = game
    request(service)
    execute(
        service,
        "resolve_intent",
        {"request_id": "player-1", "resolution": "paused", "note": "网络中断"},
        "pause",
    )
    with session_scope(context.database_url) as session:
        world = session.get(World, "sp-world")
        world.metadata_json = {**world.metadata_json, "keeper_mode": "agent"}
    outcome = execute(
        service, "control_keeper", {"action": "retry", "request_id": "player-1"}, "retry"
    )
    assert status(context, "player-1") == "queued"
    assert any(
        e["type"] == "action_status" and e["payload"]["status"] == "queued"
        for e in outcome["events"]
    )
    with session_scope(context.database_url) as session:
        assert current_control(session, "sp-world").controller_kind == "none"
    with pytest.raises(StructuredError):
        execute(
            service, "control_keeper", {"action": "retry", "request_id": "player-1"}, "retry-again"
        )


@pytest.mark.parametrize("mode", ["agent", "assisted"])
def test_model_failure_persists_and_delivers_pause(game, mode):
    context, service = game
    request(service)
    frames = []
    runner = KeeperAgentRunner(
        context.database_url, caller=_ScriptedCaller([RuntimeError("offline")])
    )
    run = runner.run if mode == "agent" else runner.run_assisted
    result = asyncio.run(
        run(world_id="sp-world", trigger_request_id="player-1", deliver=_collector(frames))
    )
    assert result.status == "paused"
    assert status(context, "player-1") == "paused"
    assert any(
        f["type"] == "action_status"
        and f["payload"]["request_id"] == "player-1"
        and f["payload"]["status"] == "paused"
        for f in frames
    )
    assert snapshot(context).state["current_scene"]["id"] == "study"


def test_owner_must_explicitly_grant_keeper_and_last_keeper_is_protected(game):
    context, _service = game
    with pytest.raises(MultiplayerError):
        set_keeper_authorization(context.database_url, "sp-world", "u-bob", "u-alice", True)
    with pytest.raises(MultiplayerError) as exc:
        set_keeper_authorization(context.database_url, "sp-world", "u-keeper", "u-owner", False)
    assert exc.value.code == "keeper_required"
    granted = set_keeper_authorization(context.database_url, "sp-world", "u-owner", "u-owner", True)
    assert granted["can_keeper"] is True
    assert (
        set_keeper_authorization(context.database_url, "sp-world", "u-keeper", "u-owner", False)[
            "can_keeper"
        ]
        is False
    )
