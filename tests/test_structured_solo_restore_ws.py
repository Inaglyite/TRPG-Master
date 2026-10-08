"""Real DB + real room/gateway locks, with transport-only socket doubles.

Not browser evidence; browser proof is recorded separately. No model calls.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_structured_solo_restore import solo as solo

from src.multiplayer.room_runtime import GameRoom, RoomConnection, RoomEventHub, RoomManager
from src.multiplayer.service import finish_room_action
from src.multiplayer.solo_timeline_ws import handle_solo_timeline_message, teardown_room_for_switch
from src.storage.database import GameCommand, RoomAction, session_scope
from src.storage.persistence import save_game
from src.structured.gateway import StructuredGateway
from src.structured.room_integration import gateway_for, handle_room_structured_frame


class Socket:
    def __init__(self):
        self.sent = []
        self.closed = []

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, *, code, reason):
        self.closed.append((code, reason))


async def room_setup(solo):
    context, _, _, _ = solo
    manager = RoomManager()
    hub = RoomEventHub(context.world_id)
    room = GameRoom(
        context.world_id,
        SimpleNamespace(context=context),
        hub,
        "u-owner",
        status="playing",
        play_mode="solo",
        action_status_callback=lambda world, action, status: finish_room_action(
            context.database_url,
            world,
            action,
            status,
        ),
    )
    room.driver_transport = SimpleNamespace(close_input=AsyncMock())
    await manager.get_or_create(context.world_id, lambda: room)
    sockets = [Socket(), Socket()]
    for index, socket in enumerate(sockets):
        await hub.attach(RoomConnection(str(index), "u-owner", "owner", socket))
    controller = SimpleNamespace(
        deps=SimpleNamespace(
            database_url=lambda: context.database_url,
            room_manager=lambda: manager,
        )
    )
    return controller, manager, room, sockets


def frame(solo):
    return {
        "type": "solo_save_load",
        "world_id": solo[0].world_id,
        "slot_id": "slot_001",
        "action_id": "restore-ws",
        "expected_revision": solo[3]()[1],
    }


def test_solo_restore_retires_runtime_and_all_tabs_after_commit(solo):
    context, _, execute, snapshot = solo
    save_game([], "slot_001", context=context)
    saved_hp = snapshot()[0]["pc"]["hp"]
    execute(
        "adjust_stat",
        {
            "investigator_id": "inv-alice",
            "field": "hp",
            "delta": -2,
            "reason": "伤害",
        },
    )

    async def scenario():
        controller, manager, room, sockets = await room_setup(solo)
        result = await handle_solo_timeline_message(
            controller,
            sockets[0],
            room,
            SimpleNamespace(id="u-owner"),
            "owner",
            frame(solo),
        )
        assert result == "close"
        assert room.terminal_event_pending and not room.action_active
        assert not room.control_action_active
        assert await manager.get(context.world_id) is None
        room.driver_transport.close_input.assert_awaited_once()
        for socket in sockets:
            assert socket.closed == [(4413, "存档已读取，请重新同步")]
            assert [event["type"] for event in socket.sent] == ["solo_save_restored"]
            assert socket.sent[0]["world_id"] == context.world_id
        assert snapshot()[0]["pc"]["hp"] == saved_hp
        with session_scope(context.database_url) as session:
            assert session.query(RoomAction).one().status == "completed"
            assert session.query(GameCommand).filter_by(kind="restore_save").count() == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing", "save_not_found"),
        ("stale", "revision_conflict"),
        ("extra_field", "invalid_action"),
        ("wrong_world", "invalid_action"),
        ("boolean_revision", "invalid_action"),
        ("not_owner", "owner_required"),
    ],
)
def test_refused_restore_leaves_runtime_and_authority_intact(solo, fault, code):
    context = solo[0]
    save_game([], "slot_001", context=context)
    before = solo[3]()

    async def scenario():
        controller, manager, room, sockets = await room_setup(solo)
        data = frame(solo)
        role = "owner"
        if fault == "missing":
            data["slot_id"] = "slot_999"
        elif fault == "stale":
            data["expected_revision"] += 1
        elif fault == "extra_field":
            data["state"] = {"hp": 999}
        elif fault == "wrong_world":
            data["world_id"] = "other-world"
        elif fault == "boolean_revision":
            data["expected_revision"] = True
        elif fault == "not_owner":
            role = "player"
        result = await handle_solo_timeline_message(
            controller,
            sockets[0],
            room,
            SimpleNamespace(id="u-owner"),
            role,
            data,
        )
        assert result == "handled"
        assert sockets[0].sent[-1]["code"] == code
        assert not sockets[0].closed and not sockets[1].closed
        assert not sockets[1].sent
        assert await manager.get(context.world_id) is room
        assert not room.terminal_event_pending and not room.action_active
        room.driver_transport.close_input.assert_not_awaited()
        assert solo[3]() == before

    asyncio.run(scenario())


def test_gateway_rechecks_admission_after_waiting_on_world_lock(solo):
    context = solo[0]
    gateway = StructuredGateway(context.database_url)
    before = solo[3]()

    async def scenario():
        received = []
        allowed = True
        lock = gateway._world_lock(context.world_id)
        await lock.acquire()
        with patch.object(gateway, "_execute", side_effect=AssertionError("must not execute")):
            queued = asyncio.create_task(
                gateway.handle_frame(
                    world_id=context.world_id,
                    user_id="u-owner",
                    frame={"type": "command_request", "command_id": "queued-before-restore"},
                    deliver=AsyncMock(side_effect=lambda event: received.append(event)),
                    admission_guard=lambda: allowed,
                )
            )
            await asyncio.sleep(0)
            assert not queued.done()
            allowed = False
            lock.release()
            await queued
        assert len(received) == 1
        assert received[0]["type"] == "request_error"
        assert received[0]["payload"]["code"] == "stale_target"
        assert received[0]["payload"]["retryable"] is True
        assert received[0]["payload"]["command_id"] == "queued-before-restore"
        assert solo[3]() == before

    asyncio.run(scenario())


def test_queued_room_frame_cannot_execute_after_timeline_teardown(solo):
    context = solo[0]
    before = solo[3]()

    async def scenario():
        controller, manager, room, sockets = await room_setup(solo)
        gateway = gateway_for(context.database_url)
        lock = gateway._world_lock(context.world_id)
        await lock.acquire()
        with patch.object(gateway, "_execute", side_effect=AssertionError("old frame executed")):
            queued = asyncio.create_task(
                handle_room_structured_frame(
                    controller,
                    room,
                    sockets[0],
                    SimpleNamespace(id="u-owner"),
                    context.world_id,
                    "0",
                    {"type": "command_request", "command_id": "queued-before-switch"},
                )
            )
            await asyncio.sleep(0)
            assert not queued.done()
            await teardown_room_for_switch(
                manager, room, "branch-world", label="分支", reason="switched"
            )
            assert room.terminal_event_pending
            lock.release()
            await queued
        assert solo[3]() == before
        refusal = sockets[0].sent[-1]
        assert refusal["type"] == "request_error"
        assert refusal["payload"]["code"] == "stale_target"
        assert refusal["payload"]["command_id"] == "queued-before-switch"
        assert await manager.get(context.world_id) is None

    asyncio.run(scenario())
