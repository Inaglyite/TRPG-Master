from __future__ import annotations

import asyncio

import pytest

from src.room_runtime import (
    ActionReservationError,
    GameRoom,
    RoomCapacityError,
    RoomConnection,
    RoomDriverTransport,
    RoomEventHub,
    RoomManager,
)


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


async def _room_manager_single_flights_concurrent_creation():
    manager = RoomManager()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return GameRoom("world-a", object(), RoomEventHub("world-a"), "owner")

    results = await asyncio.gather(*(manager.get_or_create("world-a", factory) for _ in range(12)))
    assert calls == 1
    assert len({id(room) for room, _created in results}) == 1
    assert sum(created for _room, created in results) == 1


async def _room_manager_enforces_active_room_capacity():
    manager = RoomManager(max_rooms=1)
    await manager.get_or_create(
        "world-a",
        lambda: GameRoom("world-a", object(), RoomEventHub("world-a"), "owner"),
    )
    with pytest.raises(RoomCapacityError):
        await manager.get_or_create(
            "world-b",
            lambda: GameRoom("world-b", object(), RoomEventHub("world-b"), "owner"),
        )


async def _event_visibility_ack_and_replay_are_connection_scoped():
    hub = RoomEventHub("world-a", replay_limit=16)
    owner_socket, alice_socket, bob_socket = Socket(), Socket(), Socket()
    await hub.attach(RoomConnection("owner-tab", "owner", "owner", owner_socket))
    await hub.attach(RoomConnection("alice-tab", "alice", "player", alice_socket))
    await hub.attach(RoomConnection("bob-tab", "bob", "player", bob_socket))

    public_id = await hub.broadcast({"type": "narrative_chunk", "text": "雨声"})
    await hub.broadcast({"type": "private_clue", "text": "只给 Alice"}, visibility="player:alice")
    await hub.broadcast({"type": "owner_notice"}, visibility="owner")
    await hub.broadcast({"type": "tool_protocol", "secret": True}, visibility="server_only")

    assert [item["type"] for item in owner_socket.messages] == [
        "narrative_chunk",
        "owner_notice",
    ]
    assert [item["type"] for item in alice_socket.messages] == [
        "narrative_chunk",
        "private_clue",
    ]
    assert [item["type"] for item in bob_socket.messages] == ["narrative_chunk"]
    assert await hub.acknowledge("alice-tab", public_id)
    replay = await hub.replay_after("alice-tab", public_id)
    assert [item["type"] for item in replay["events"]] == ["private_clue"]


async def _action_policy_rejects_wrong_actor_duplicates_and_overlap():
    room = GameRoom(
        "world-a",
        object(),
        RoomEventHub("world-a"),
        "owner",
        current_actor_user_id="alice",
    )
    with pytest.raises(ActionReservationError) as wrong:
        await room.reserve_action("bob", "action-1")
    assert wrong.value.code == "not_current_actor"

    await room.reserve_action("alice", "action-1")
    with pytest.raises(ActionReservationError) as duplicate:
        await room.reserve_action("alice", "action-1")
    assert duplicate.value.code == "duplicate_action"
    with pytest.raises(ActionReservationError) as busy:
        await room.reserve_action("alice", "action-2")
    assert busy.value.code == "room_turn_in_progress"
    room.release_action()
    await room.reserve_action("alice", "action-2")
    room.release_action()


async def _owner_control_reservation_does_not_require_current_actor():
    room = GameRoom(
        "world-owner-control",
        object(),
        RoomEventHub("world-owner-control"),
        "owner",
        current_actor_user_id="player",
    )
    await room.reserve_action(
        "owner",
        "load-save-1",
        require_current_actor=False,
    )
    assert room.action_active
    room.release_action()

    with pytest.raises(ActionReservationError) as denied:
        await room.reserve_action("owner", "normal-action")
    assert denied.value.code == "not_current_actor"


async def _room_is_removed_only_after_empty_idle_grace():
    manager = RoomManager()
    room, _ = await manager.get_or_create(
        "world-a",
        lambda: GameRoom("world-a", object(), RoomEventHub("world-a"), "owner"),
    )
    room.member_connected("alice")
    room.member_disconnected("alice")
    assert not await manager.remove_if_idle("world-a", idle_seconds=999)
    assert await manager.remove_if_idle("world-a", idle_seconds=0)
    assert await manager.get("world-a") is None


def test_member_presence_changes_only_on_first_and_last_connection():
    room = GameRoom("world-a", object(), RoomEventHub("world-a"), "owner")
    assert room.member_connected("alice") is True
    assert room.member_connected("alice") is False
    assert room.connected_users == {"alice": 2}
    assert room.member_disconnected("alice") is False
    assert room.member_disconnected("alice") is True
    assert room.connected_users == {}


async def _driver_sends_decisions_only_to_current_actor():
    hub = RoomEventHub("world-a")
    alice_socket, bob_socket = Socket(), Socket()
    await hub.attach(RoomConnection("alice-tab", "alice", "player", alice_socket))
    await hub.attach(RoomConnection("bob-tab", "bob", "player", bob_socket))
    room = GameRoom(
        "world-a",
        object(),
        hub,
        "alice",
        current_actor_user_id="alice",
    )
    transport = RoomDriverTransport(room)

    await transport.send_json({"type": "decision_request", "id": "decision-1"})
    assert not room.accept_pending_reply(
        "decision", "bob", request_id="decision-1"
    )
    assert not room.accept_pending_reply(
        "decision", "alice", request_id="wrong-decision"
    )
    assert room.accept_pending_reply(
        "decision", "alice", request_id="decision-1"
    )
    assert not room.accept_pending_reply(
        "decision", "alice", request_id="decision-1"
    )
    await transport.send_json(
        {
            "type": "private_event",
            "target_user_id": "bob",
            "kind": "clue",
            "clue": {"text": "只有 Bob 看见"},
        }
    )
    await transport.send_json(
        {
            "type": "character_state",
            "target_user_id": "alice",
            "data": "{\"name\":\"Alice\",\"secret\":\"private\"}",
        }
    )
    await transport.send_json({"type": "narrative_chunk", "text": "公开叙述"})

    assert [message["type"] for message in alice_socket.messages] == [
        "decision_request",
        "character_state",
        "narrative_chunk",
    ]
    assert [message["type"] for message in bob_socket.messages] == [
        "private_event",
        "narrative_chunk",
    ]
    assert "target_user_id" not in bob_socket.messages[0]
    assert "target_user_id" not in alice_socket.messages[1]
    assert "Alice" not in str(bob_socket.messages)
    assert not any(message["type"] == "character_state" for message in bob_socket.messages)


def test_room_manager_single_flights_concurrent_creation():
    asyncio.run(_room_manager_single_flights_concurrent_creation())


def test_room_manager_enforces_active_room_capacity():
    asyncio.run(_room_manager_enforces_active_room_capacity())


def test_event_visibility_ack_and_replay_are_connection_scoped():
    asyncio.run(_event_visibility_ack_and_replay_are_connection_scoped())


def test_action_policy_rejects_wrong_actor_duplicates_and_overlap():
    asyncio.run(_action_policy_rejects_wrong_actor_duplicates_and_overlap())


def test_owner_control_reservation_does_not_require_current_actor():
    asyncio.run(_owner_control_reservation_does_not_require_current_actor())


def test_room_is_removed_only_after_empty_idle_grace():
    asyncio.run(_room_is_removed_only_after_empty_idle_grace())


def test_driver_sends_decisions_only_to_current_actor():
    asyncio.run(_driver_sends_decisions_only_to_current_actor())
