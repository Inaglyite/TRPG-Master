from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.multiplayer.recovery import pending_reply_payload
from src.multiplayer.room_runtime import (
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


class SlowSocket(Socket):
    async def send_json(self, payload):
        await asyncio.sleep(1)
        await super().send_json(payload)


class BlockingFirstSocket(Socket):
    def __init__(self):
        super().__init__()
        self.first_send_started = asyncio.Event()
        self.release_first_send = asyncio.Event()

    async def send_json(self, payload):
        if not self.messages:
            self.first_send_started.set()
            await self.release_first_send.wait()
        await super().send_json(payload)


class CloseTrackingSocket(Socket):
    def __init__(self):
        super().__init__()
        self.closed: tuple[int, str] | None = None

    async def close(self, *, code, reason):
        self.closed = (code, reason)


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


async def _world_lifecycle_waits_for_loading_room():
    """归档租约不能从正在建房的 ``_loading`` 窗口中穿过去。"""
    manager = RoomManager()
    build_started = asyncio.Event()
    allow_build = asyncio.Event()
    archive_entered = asyncio.Event()
    room = GameRoom("world-a", object(), RoomEventHub("world-a"), "owner")

    async def factory():
        build_started.set()
        await allow_build.wait()
        return room

    loading = asyncio.create_task(manager.get_or_create("world-a", factory))
    await asyncio.wait_for(build_started.wait(), timeout=1)

    async def archive_boundary():
        async with manager.world_lifecycle("world-a"):
            assert await manager.get("world-a") is room
            archive_entered.set()

    archive = asyncio.create_task(archive_boundary())
    await asyncio.sleep(0)
    assert not archive_entered.is_set()
    allow_build.set()
    await loading
    await asyncio.wait_for(archive, timeout=1)


async def _event_visibility_ack_and_replay_are_connection_scoped():
    hub = RoomEventHub("world-a", replay_limit=16)
    owner_socket, alice_socket, alice_viewer_socket, bob_socket = (
        Socket(),
        Socket(),
        Socket(),
        Socket(),
    )
    await hub.attach(RoomConnection("owner-tab", "owner", "owner", owner_socket))
    await hub.attach(RoomConnection("alice-tab", "alice", "player", alice_socket))
    await hub.attach(RoomConnection("alice-viewer-tab", "alice", "viewer", alice_viewer_socket))
    await hub.attach(RoomConnection("bob-tab", "bob", "player", bob_socket))

    public_id = await hub.broadcast({"type": "narrative_chunk", "text": "雨声"})
    await hub.broadcast({"type": "private_clue", "text": "只给 Alice"}, visibility="player:alice")
    await hub.broadcast({"type": "personal_note", "text": "Alice 的笔记"}, visibility="user:alice")
    await hub.broadcast({"type": "owner_notice"}, visibility="owner")
    await hub.broadcast({"type": "tool_protocol", "secret": True}, visibility="server_only")

    assert [item["type"] for item in owner_socket.messages] == [
        "narrative_chunk",
        "owner_notice",
    ]
    assert [item["type"] for item in alice_socket.messages] == [
        "narrative_chunk",
        "private_clue",
        "personal_note",
    ]
    assert [item["type"] for item in alice_viewer_socket.messages] == [
        "narrative_chunk",
        "personal_note",
    ]
    assert [item["type"] for item in bob_socket.messages] == ["narrative_chunk"]
    assert await hub.acknowledge("alice-tab", public_id)
    replay = await hub.replay_after("alice-tab", public_id)
    assert [item["type"] for item in replay["events"]] == [
        "private_clue",
        "personal_note",
    ]
    viewer_replay = await hub.replay_after("alice-viewer-tab", public_id)
    assert [item["type"] for item in viewer_replay["events"]] == ["personal_note"]
    owner_replay = await hub.replay_after("owner-tab", public_id)
    assert [item["type"] for item in owner_replay["events"]] == ["owner_notice"]
    bob_replay = await hub.replay_after("bob-tab", public_id)
    assert bob_replay["events"] == []
    restarted_epoch = await hub.replay_after("alice-tab", 9999)
    assert restarted_epoch == {
        "gap": True,
        "events": [],
        "latest_event_id": await hub.latest_event_id(),
    }


async def _pending_handshake_is_revocable_before_private_recovery_activation():
    hub = RoomEventHub("world-pending-auth", replay_limit=16)
    socket = CloseTrackingSocket()
    await hub.attach_pending(
        RoomConnection(
            "pending-tab",
            "alice",
            "player",
            socket,
            session_hash="alice-session",
            active=False,
        )
    )

    await hub.broadcast(
        {"type": "private_event", "text": "never reach pending socket"},
        visibility="player:alice",
    )
    assert socket.messages == []

    assert await hub.disconnect_user("alice") == 1
    activation = await hub.activate_with_replay("pending-tab", 0)
    assert activation["delivered"] is False
    assert socket.closed is not None
    assert await hub.connection_snapshot() == []


async def _viewer_role_revokes_private_live_and_replay_visibility():
    hub = RoomEventHub("world-viewer-revocation", replay_limit=16)
    socket = Socket()
    await hub.attach(RoomConnection("alice-tab", "alice", "player", socket))

    first_id = await hub.broadcast(
        {"type": "private_event", "text": "visible while player"},
        visibility="player:alice",
    )
    await hub.update_user_role("alice", "viewer")
    await hub.broadcast(
        {"type": "private_event", "text": "hidden after downgrade"},
        visibility="player:alice",
    )

    assert [message["text"] for message in socket.messages] == ["visible while player"]
    replay = await hub.replay_after("alice-tab", first_id)
    assert replay["events"] == []


async def _disconnect_session_targets_only_one_login():
    hub = RoomEventHub("world-session-revoke")
    first, second = CloseTrackingSocket(), CloseTrackingSocket()
    await hub.attach(
        RoomConnection(
            "alice-first",
            "alice",
            "player",
            first,
            session_hash="session-first",
        )
    )
    await hub.attach(
        RoomConnection(
            "alice-second",
            "alice",
            "player",
            second,
            session_hash="session-second",
        )
    )

    assert await hub.disconnect_session("session-first") == 1
    await hub.broadcast({"type": "narrative_chunk", "text": "still connected"})

    assert first.closed is not None
    assert first.messages == []
    assert second.closed is None
    assert [message["text"] for message in second.messages] == ["still connected"]


async def _replay_drops_embedded_assets_and_slow_clients_do_not_block_room(
    monkeypatch,
):
    monkeypatch.setenv("TRPG_ROOM_SEND_TIMEOUT", "0.05")
    hub = RoomEventHub("world-assets", replay_limit=16)
    room = GameRoom("world-assets", object(), hub, "alice")
    fast_socket, slow_socket = Socket(), SlowSocket()
    await hub.attach(RoomConnection("fast", "alice", "player", fast_socket))
    room.member_connected("bob")
    disconnects = 0

    async def release_slow_presence():
        nonlocal disconnects
        disconnects += 1
        room.member_disconnected("bob")

    await hub.attach(
        RoomConnection(
            "slow",
            "bob",
            "player",
            slow_socket,
            transport_failure_callback=release_slow_presence,
        )
    )

    started = asyncio.get_running_loop().time()
    await hub.broadcast(
        {
            "type": "handout",
            "asset_data_uri": "data:image/png;base64,large",
            "speaker": {
                "avatar": {
                    "asset_data_uri": "data:image/png;base64,also-large",
                }
            },
        }
    )
    assert asyncio.get_running_loop().time() - started < 0.5
    assert fast_socket.messages[0]["asset_data_uri"].startswith("data:")
    replay = await hub.replay_after("fast", 0)
    assert "asset_data_uri" not in replay["events"][0]
    assert "asset_data_uri" not in replay["events"][0]["speaker"]["avatar"]
    connections = await hub.connection_snapshot()
    assert [item["connection_id"] for item in connections] == ["fast"]
    assert room.connected_users == {}
    assert disconnects == 1


async def _concurrent_broadcasts_are_delivered_in_event_id_order():
    hub = RoomEventHub("world-ordered", replay_limit=16)
    socket = BlockingFirstSocket()
    await hub.attach(RoomConnection("tab", "alice", "player", socket))

    first = asyncio.create_task(hub.broadcast({"type": "narrative_chunk", "text": "first"}))
    await asyncio.wait_for(socket.first_send_started.wait(), timeout=1)
    second = asyncio.create_task(hub.broadcast({"type": "narrative_chunk", "text": "second"}))
    while await hub.latest_event_id() < 2:
        await asyncio.sleep(0)

    socket.release_first_send.set()
    first_id, second_id = await asyncio.gather(first, second)

    assert (first_id, second_id) == (1, 2)
    assert [message["room_event_id"] for message in socket.messages] == [1, 2]
    assert [message["text"] for message in socket.messages] == ["first", "second"]


async def _attach_with_replay_orders_missed_events_before_future_broadcasts():
    hub = RoomEventHub("world-reconnect", replay_limit=16)
    missed_id = await hub.broadcast({"type": "narrative_chunk", "text": "missed during reconnect"})
    socket = BlockingFirstSocket()
    # The full image is written while the socket is not yet visible to the hub.
    socket.messages.append(
        {
            "type": "room_full_state",
            "latest_event_id": 0,
        }
    )
    connection = RoomConnection("tab", "alice", "player", socket)

    # BlockingFirstSocket only blocks when messages is empty, so clear the
    # bootstrap marker just long enough to force the replay send to overlap a
    # future broadcast, then restore it as the first observed frame.
    full_state = socket.messages.pop()
    attach = asyncio.create_task(hub.attach_with_replay(connection, 0))
    await asyncio.wait_for(socket.first_send_started.wait(), timeout=1)
    future = asyncio.create_task(
        hub.broadcast({"type": "narrative_chunk", "text": "future live event"})
    )
    while await hub.latest_event_id() < 2:
        await asyncio.sleep(0)
    socket.messages.insert(0, full_state)
    socket.release_first_send.set()

    replay_result, future_id = await asyncio.gather(attach, future)
    assert replay_result == {
        "gap": False,
        "latest_event_id": missed_id,
        "delivered": True,
    }
    assert future_id == 2
    assert [message["type"] for message in socket.messages] == [
        "room_full_state",
        "narrative_chunk",
        "narrative_chunk",
    ]
    assert [message["room_event_id"] for message in socket.messages[1:]] == [1, 2]


async def _stale_replay_cursor_never_attaches_a_ghost_connection():
    hub = RoomEventHub("world-gap", replay_limit=16)
    for index in range(20):
        await hub.broadcast({"type": "narrative_chunk", "text": str(index)})
    socket = Socket()

    result = await hub.attach_with_replay(
        RoomConnection("stale", "alice", "player", socket),
        0,
    )

    assert result["gap"] is True
    assert await hub.connection_snapshot() == []
    assert socket.messages == []


async def _active_action_pins_every_event_past_the_normal_replay_limit():
    hub = RoomEventHub("world-pinned-replay", replay_limit=16)
    room = GameRoom(
        "world-pinned-replay",
        object(),
        hub,
        "alice",
        current_actor_user_id="alice",
    )
    await room.reserve_action("alice", "long-stream")
    for index in range(40):
        await hub.broadcast(
            {
                "type": "narrative_chunk",
                "text": f"chunk-{index}",
            }
        )

    socket = Socket()
    replay = await hub.attach_with_replay(
        RoomConnection("reconnect", "alice", "owner", socket),
        room.active_action_start_event_id,
    )

    assert replay["gap"] is False
    assert len(socket.messages) == 40
    assert [message["room_event_id"] for message in socket.messages] == list(range(1, 41))

    await hub.detach("reconnect")
    room.release_action()
    after_unpin = await hub.attach_with_replay(
        RoomConnection("stale", "alice", "owner", Socket()),
        0,
    )
    assert after_unpin["gap"] is True


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


async def _failed_replay_pin_does_not_leave_action_locked():
    room = GameRoom(
        "world-pin-failure",
        object(),
        RoomEventHub("world-pin-failure"),
        "alice",
        current_actor_user_id="alice",
    )

    async def fail_pin():
        raise RuntimeError("pin failed")

    room.hub.pin_replay_boundary = fail_pin
    with pytest.raises(RuntimeError, match="pin failed"):
        await room.reserve_action("alice", "pin-failure")
    assert room.action_active is False
    assert room.active_action_id is None


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


async def _owner_control_releases_on_terminal_response():
    room = GameRoom(
        "world-control-terminal",
        object(),
        RoomEventHub("world-control-terminal"),
        "owner",
        current_actor_user_id="player",
    )
    terminal = []
    room.action_status_callback = lambda world_id, action_id, status: terminal.append(
        (world_id, action_id, status)
    )
    transport = RoomDriverTransport(room)
    await room.reserve_control("owner", "save-control-1")
    assert room.action_active and room.control_action_active
    await transport.send_json({"type": "saved", "ok": True, "slot_id": "slot_001"})
    assert not room.action_active
    assert not room.control_action_active
    assert terminal == [("world-control-terminal", "save-control-1", "completed")]

    room.current_actor_user_id = "player"
    await room.reserve_action("player", "failed-action")
    await transport.send_json({"type": "error", "message": "model failed"})
    assert room.action_active
    await transport.send_json({"type": "done"})
    assert terminal[-1] == (
        "world-control-terminal",
        "failed-action",
        "completed",
    )

    await room.reserve_action("player", "terminal-failure")
    await transport.send_json({"type": "error", "message": "turn aborted", "terminal": True})
    assert not room.action_active
    assert terminal[-1] == (
        "world-control-terminal",
        "terminal-failure",
        "failed",
    )
    # An explicitly failed action keeps its stable client ID retryable.
    await room.reserve_action("player", "terminal-failure")
    room.release_action()

    await room.reserve_action("player", "rewrite-action")
    await transport.send_json({"type": "turn_rewritten"})
    assert not room.action_active
    assert terminal[-1] == (
        "world-control-terminal",
        "rewrite-action",
        "completed",
    )


async def _terminal_enqueue_is_a_barrier_before_a_retry_can_start():
    hub = RoomEventHub("world-terminal-barrier")
    socket = BlockingFirstSocket()
    await hub.attach(RoomConnection("tab", "player", "player", socket))
    room = GameRoom(
        "world-terminal-barrier",
        object(),
        hub,
        "owner",
        current_actor_user_id="player",
    )
    transport = RoomDriverTransport(room)

    await room.reserve_action("player", "stable-action")
    # Hold the event boundary so the old action is released but its terminal
    # frame cannot yet enter the per-connection send chain.
    await hub._lock.acquire()
    terminal_send = asyncio.create_task(
        transport.send_json(
            {
                "type": "error",
                "terminal": True,
                "message": "明确失败，可以重试",
            }
        )
    )
    try:
        for _ in range(100):
            if room.terminal_event_pending:
                break
            await asyncio.sleep(0)
        assert not room.action_active
        assert room.terminal_event_pending is True
        with pytest.raises(ActionReservationError) as pending:
            await room.reserve_action("player", "stable-action")
        assert pending.value.code == "room_turn_in_progress"
    finally:
        hub._lock.release()

    # Once the terminal frame is queued, a fast client may submit its retry.
    # Every event from that retry will share the same ordered connection tail,
    # so waiting for the physical socket write would only create a rejection
    # race for clients that have already observed the terminal frame.
    await asyncio.wait_for(socket.first_send_started.wait(), timeout=1)
    assert room.terminal_event_pending is False
    await room.reserve_action("player", "stable-action")
    socket.release_first_send.set()
    await terminal_send
    assert room.terminal_event_pending is False
    assert room.action_active
    room.release_action()


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
    assert not room.accept_pending_reply("decision", "bob", request_id="decision-1")
    assert not room.accept_pending_reply("decision", "alice", request_id="wrong-decision")
    assert room.accept_pending_reply("decision", "alice", request_id="decision-1")
    assert not room.accept_pending_reply("decision", "alice", request_id="decision-1")
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
            "data": '{"name":"Alice","secret":"private"}',
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


async def _driver_routes_combat_defense_only_to_target_investigator_controller():
    state = {
        "active_investigator_id": "inv-alice",
        "pc": {
            "investigator_id": "inv-alice",
            "controller_user_id": "alice",
        },
        "investigator_controllers": {
            "alice": "inv-alice",
            "bob": "inv-bob",
        },
        "investigators": {
            "inv-alice": {"controller_user_id": "alice"},
            "inv-bob": {"controller_user_id": "bob"},
        },
    }
    engine = SimpleNamespace(
        context=SimpleNamespace(
            world_store=SimpleNamespace(load=lambda: state),
        )
    )
    hub = RoomEventHub("world-defense")
    alice_socket, bob_socket = Socket(), Socket()
    await hub.attach(RoomConnection("alice-tab", "alice", "player", alice_socket))
    await hub.attach(RoomConnection("bob-tab", "bob", "player", bob_socket))
    room = GameRoom(
        "world-defense",
        engine,
        hub,
        "alice",
        current_actor_user_id="alice",
    )

    await RoomDriverTransport(room).send_json(
        {
            "type": "decision_request",
            "id": "defend-bob",
            "kind": "combat_defense",
            "responding_investigator_id": "inv-bob",
            "target_investigator_id": "inv-bob",
        }
    )
    alice_recovered = await hub.build_at_boundary(
        lambda _event_id, events: pending_reply_payload(room, "alice", events)
    )
    bob_recovered = await hub.build_at_boundary(
        lambda _event_id, events: pending_reply_payload(room, "bob", events)
    )

    assert alice_socket.messages == []
    assert [message["id"] for message in bob_socket.messages] == ["defend-bob"]
    assert alice_recovered is None
    assert bob_recovered["id"] == "defend-bob"
    assert bob_recovered["recovered"] is True
    assert room.pending_reply_user_id == "bob"
    assert not room.accept_pending_reply(
        "decision",
        "alice",
        request_id="defend-bob",
    )
    assert room.accept_pending_reply(
        "decision",
        "bob",
        request_id="defend-bob",
    )


async def _driver_assigns_and_broadcasts_next_investigator_after_combat_turn():
    state = {
        "active_investigator_id": "inv-alice",
        "pc": {
            "investigator_id": "inv-alice",
            "controller_user_id": "alice",
        },
        "investigator_controllers": {
            "alice": "inv-alice",
            "bob": "inv-bob",
        },
        "investigators": {
            "inv-alice": {"controller_user_id": "alice"},
            "inv-bob": {"controller_user_id": "bob"},
        },
        "combat_state": {
            "active": True,
            "current_actor": "inv-bob",
            "participants": [
                {"id": "inv-alice", "kind": "pc"},
                {"id": "inv-bob", "kind": "pc"},
                {"id": "cultist", "kind": "npc"},
            ],
        },
    }
    engine = SimpleNamespace(
        context=SimpleNamespace(
            world_store=SimpleNamespace(load=lambda: state),
        )
    )
    hub = RoomEventHub("world-combat-actor")
    alice_socket, bob_socket = Socket(), Socket()
    await hub.attach(RoomConnection("alice-tab", "alice", "player", alice_socket))
    await hub.attach(RoomConnection("bob-tab", "bob", "player", bob_socket))
    room = GameRoom(
        "world-combat-actor",
        engine,
        hub,
        "alice",
        current_actor_user_id="alice",
        status="playing",
    )
    persisted: list[str | None] = []
    room.control_state_callback = lambda: persisted.append(
        room.current_actor_user_id
    )

    await RoomDriverTransport(room).send_json({"type": "done"})

    assert room.current_actor_user_id == "bob"
    assert persisted == ["bob"]
    assert [message["type"] for message in alice_socket.messages] == [
        "done",
        "actor_changed",
        "room_state",
    ]
    assert alice_socket.messages[-1]["current_actor_user_id"] == "bob"
    assert bob_socket.messages == alice_socket.messages


def test_room_manager_single_flights_concurrent_creation():
    asyncio.run(_room_manager_single_flights_concurrent_creation())


def test_room_manager_enforces_active_room_capacity():
    asyncio.run(_room_manager_enforces_active_room_capacity())


def test_world_lifecycle_waits_for_loading_room():
    asyncio.run(_world_lifecycle_waits_for_loading_room())


def test_event_visibility_ack_and_replay_are_connection_scoped():
    asyncio.run(_event_visibility_ack_and_replay_are_connection_scoped())


def test_pending_handshake_is_revocable_before_private_recovery_activation():
    asyncio.run(_pending_handshake_is_revocable_before_private_recovery_activation())


def test_viewer_role_revokes_private_live_and_replay_visibility():
    asyncio.run(_viewer_role_revokes_private_live_and_replay_visibility())


def test_disconnect_session_targets_only_one_login():
    asyncio.run(_disconnect_session_targets_only_one_login())


def test_replay_drops_embedded_assets_and_slow_clients_do_not_block_room(
    monkeypatch,
):
    asyncio.run(_replay_drops_embedded_assets_and_slow_clients_do_not_block_room(monkeypatch))


def test_concurrent_broadcasts_are_delivered_in_event_id_order():
    asyncio.run(_concurrent_broadcasts_are_delivered_in_event_id_order())


def test_attach_with_replay_orders_missed_events_before_future_broadcasts():
    asyncio.run(_attach_with_replay_orders_missed_events_before_future_broadcasts())


def test_stale_replay_cursor_never_attaches_a_ghost_connection():
    asyncio.run(_stale_replay_cursor_never_attaches_a_ghost_connection())


def test_active_action_pins_every_event_past_the_normal_replay_limit():
    asyncio.run(_active_action_pins_every_event_past_the_normal_replay_limit())


def test_action_policy_rejects_wrong_actor_duplicates_and_overlap():
    asyncio.run(_action_policy_rejects_wrong_actor_duplicates_and_overlap())


def test_failed_replay_pin_does_not_leave_action_locked():
    asyncio.run(_failed_replay_pin_does_not_leave_action_locked())


def test_owner_control_reservation_does_not_require_current_actor():
    asyncio.run(_owner_control_reservation_does_not_require_current_actor())


def test_owner_control_releases_on_terminal_response():
    asyncio.run(_owner_control_releases_on_terminal_response())


def test_terminal_enqueue_is_a_barrier_before_a_retry_can_start():
    asyncio.run(_terminal_enqueue_is_a_barrier_before_a_retry_can_start())


def test_room_is_removed_only_after_empty_idle_grace():
    asyncio.run(_room_is_removed_only_after_empty_idle_grace())


def test_driver_sends_decisions_only_to_current_actor():
    asyncio.run(_driver_sends_decisions_only_to_current_actor())


def test_driver_routes_combat_defense_only_to_target_investigator_controller():
    asyncio.run(_driver_routes_combat_defense_only_to_target_investigator_controller())


def test_driver_assigns_and_broadcasts_next_investigator_after_combat_turn():
    asyncio.run(_driver_assigns_and_broadcasts_next_investigator_after_combat_turn())


async def _driver_returns_room_to_lobby_after_case_settled():
    hub = RoomEventHub("world-case-settled")
    owner_socket, player_socket = Socket(), Socket()
    await hub.attach(RoomConnection("owner-tab", "owner", "owner", owner_socket))
    await hub.attach(RoomConnection("player-tab", "player", "player", player_socket))
    room = GameRoom(
        "world-case-settled",
        object(),
        hub,
        "owner",
        current_actor_user_id="player",
        status="playing",
        ready_users={"owner", "player"},
    )
    statuses: list[str] = []

    def apply_room_status(status: str) -> None:
        # 生产绑定是 multiplayer_ws.set_room_status：回调内同时写 room.status
        # 并持久化；apply_status 在 callback 非空时不再直接赋值。
        room.status = status
        statuses.append(status)

    room.status_change_callback = apply_room_status
    transport = RoomDriverTransport(room)
    await room.reserve_control("owner", "settle-case-1")
    assert room.control_action_active

    await transport.send_json(
        {
            "type": "case_settled",
            "ok": True,
            "ending_type": "good",
            "title": "封印重归寂静",
            "summary": "调查员阻止了仪式。",
        }
    )

    # 成功结算后房间回到大厅：可重新开局（start 要求 lobby），
    # 也可由房主归档删除（archive 拒绝 starting/playing），不再死锁。
    assert room.status == "lobby"
    assert statuses == ["lobby"]
    # ready 必须清空并在 room_state 里广播空集：否则保留的 ready 会让
    # 立即重开跳过全员二次确认，而进程重启后 ready 丢失，行为不一致。
    assert room.ready_users == set()
    # 行动者重置为当前房主：下一局 start 的 actor_id（current_actor_user_id
    # or user.id）取 owner，不会沿用上一局的旧 actor；旧 actor 在 lobby
    # 释放 claim 后也不会再卡住开场（investigator_required）。
    assert room.current_actor_user_id == "owner"
    assert not room.action_active
    assert not room.control_action_active
    for socket in (owner_socket, player_socket):
        assert any(
            message["type"] == "room_state"
            and message["status"] == "lobby"
            and message["current_actor_user_id"] == "owner"
            and message["ready_user_ids"] == []
            for message in socket.messages
        )
        assert socket.messages[-1]["type"] == "case_settled"

    # 控制锁已释放：房主可以立即登记下一项操作（例如重新开始）。
    await room.reserve_control("owner", "settle-case-2")
    room.release_action()


def test_driver_returns_room_to_lobby_after_case_settled():
    asyncio.run(_driver_returns_room_to_lobby_after_case_settled())


async def _driver_keeps_room_playing_after_failed_case_settled():
    hub = RoomEventHub("world-case-settled-failed")
    owner_socket, player_socket = Socket(), Socket()
    await hub.attach(RoomConnection("owner-tab", "owner", "owner", owner_socket))
    await hub.attach(RoomConnection("player-tab", "player", "player", player_socket))
    room = GameRoom(
        "world-case-settled-failed",
        object(),
        hub,
        "owner",
        current_actor_user_id="player",
        status="playing",
        ready_users={"owner", "player"},
    )
    statuses: list[str] = []

    def apply_room_status(status: str) -> None:
        room.status = status
        statuses.append(status)

    room.status_change_callback = apply_room_status
    transport = RoomDriverTransport(room)
    await room.reserve_control("owner", "settle-case-failed-1")
    assert room.control_action_active

    await transport.send_json(
        {
            "type": "case_settled",
            "ok": False,
            "error": "当前世界状态没有 pc",
        }
    )

    # 失败结算（ok=false）不是终局：案件仍在进行，房间必须保持 playing，
    # 状态、行动者与 ready 都不得被改动，也不得广播回到大厅的 room_state。
    assert room.status == "playing"
    assert statuses == []
    assert room.ready_users == {"owner", "player"}
    assert room.current_actor_user_id == "player"
    assert not room.action_active
    assert not room.control_action_active
    for socket in (owner_socket, player_socket):
        assert not any(
            message["type"] == "room_state" and message["status"] == "lobby"
            for message in socket.messages
        )
        assert socket.messages[-1]["type"] == "case_settled"

    # 控制锁已释放：房主仍可提交新的控制行动（例如重试结算）。
    await room.reserve_control("owner", "settle-case-failed-2")
    room.release_action()


def test_driver_keeps_room_playing_after_failed_case_settled():
    asyncio.run(_driver_keeps_room_playing_after_failed_case_settled())


async def _character_state_without_controller_is_never_broadcast():
    hub = RoomEventHub("world-charstate")
    alice_socket, bob_socket = Socket(), Socket()
    await hub.attach(RoomConnection("alice-tab", "alice", "player", alice_socket))
    await hub.attach(RoomConnection("bob-tab", "bob", "player", bob_socket))
    room = GameRoom(
        "world-charstate",
        object(),
        hub,
        "alice",
        current_actor_user_id="alice",
    )
    transport = RoomDriverTransport(room)

    # 没有控制器归属的 character_state 绝不能退回 public 广播：
    # 完整调查员载荷（hp/san/物品/线索）只应到达目标玩家，否则一律不广播。
    await transport.send_json(
        {
            "type": "character_state",
            "data": '{"name":"Alice","hp":1,"san":0,"secret":"must-not-leak"}',
        }
    )
    assert alice_socket.messages == []
    assert bob_socket.messages == []

    # 带控制器时仍只投递给目标玩家。
    await transport.send_json(
        {
            "type": "character_state",
            "target_user_id": "alice",
            "data": '{"name":"Alice"}',
        }
    )
    assert [message["type"] for message in alice_socket.messages] == [
        "character_state"
    ]
    assert bob_socket.messages == []


def test_character_state_without_controller_is_never_broadcast():
    asyncio.run(_character_state_without_controller_is_never_broadcast())
