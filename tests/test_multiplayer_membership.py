from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth import (
    create_login_session,
    create_user,
    resolve_session,
    resolve_session_identity,
    revoke_session,
    token_hash,
)
from src.auth_http import AuthHttpDependencies, create_auth_router
from src.database import (
    Base,
    RoomAction,
    Turn,
    User,
    World,
    WorldInvestigator,
    WorldInvite,
    WorldMember,
    get_engine,
    new_id,
    session_scope,
)
from src.database_store import DatabaseWorldStore
from src.multiplayer import (
    MultiplayerError,
    accept_invite,
    claim_investigator,
    create_invite,
    finish_room_action,
    list_invites,
    recover_room_actions,
    release_investigator,
    remove_member,
    reserve_room_action,
    room_members,
    transfer_owner,
    update_member_role,
)
from src.multiplayer_messages import run_room_message_loop, safe_multiplayer_diagnostics
from src.multiplayer_recovery import turn_recovery_payload
from src.multiplayer_ws import owner_turn_required
from src.player_notes import PlayerNotesStore
from src.room_runtime import (
    GameRoom,
    RoomConnection,
    RoomDriverTransport,
    RoomEventHub,
    RoomManager,
)


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'multiplayer.db'}"


def test_multiplayer_diagnostics_remove_keeper_text_and_tool_arguments():
    secret = "真凶是布莱斯·法伦"
    report = {
        "turn_id": "turn-secret",
        "kind": "action",
        "duration_ms": 123,
        "model_calls": [
            {
                "model": "test-model",
                "status": "completed",
                "elapsed_ms": 12,
                "usage": {"prompt_tokens": 10},
            }
        ],
        "lorebook": {
            "sequence": 3,
            "token_estimate": 120,
            "selected": [{"entry_id": "future-killer"}],
            "reason_counts": {"selected": 1, "primary_key_miss": 2},
            "trace": [
                {
                    "entry_id": "future-killer",
                    "name": secret,
                    "matched_keys": ["凶手"],
                }
            ],
        },
        "mutations": [
            {
                "source": "tool",
                "name": "npc_reveal",
                "args": {"entry_text": secret},
                "success": True,
            }
        ],
        "tool_names": ["npc_reveal"],
        "performance": {"phases_ms": {"model": 12.5}},
        "event_counts": {"narrative_chunk": 2},
    }

    safe = safe_multiplayer_diagnostics(report)
    wire = json.dumps(safe, ensure_ascii=False)

    assert secret not in wire
    assert "future-killer" not in wire
    assert "entry_text" not in wire
    assert "matched_keys" not in wire
    assert safe["lorebook"]["selected_count"] == 1
    assert safe["mutation_summary"] == {
        "total": 1,
        "successful": 1,
        "failed": 0,
    }
    assert safe["tool_count"] == 1


def test_turn_recovery_payload_uses_public_records_and_enriches_assets():
    requested = {
        "turn_id": "turn-public",
        "status": "completed",
        "events": [{"type": "handout", "file": "letter.png"}],
    }
    engine = SimpleNamespace(
        context=SimpleNamespace(assets_dir=Path("/tmp/assets")),
        turn_recovery_status=lambda turn_id: {
            "requested": requested if turn_id == "turn-public" else None,
            "active": None,
            "latest_completed": None,
        },
    )

    with patch(
        "src.multiplayer_recovery.enrich_public_history_record",
        side_effect=lambda record, _engine: dict(record),
    ), patch(
        "src.multiplayer_recovery.asset_payload",
        return_value={"asset_url": "/api/assets/letter.png"},
    ):
        payload = turn_recovery_payload(engine, "turn-public")

    assert payload["type"] == "turn_recovery"
    assert payload["requested"]["events"][0]["asset_url"] == "/api/assets/letter.png"
    assert payload["active"] is None


def test_logout_revokes_and_disconnects_only_the_presented_session(tmp_path: Path):
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    user = create_user(url, "two_session_user", "two session password")
    first_token = create_login_session(url, user)
    second_token = create_login_session(url, user)
    disconnected: list[str] = []

    async def disconnect_session(session_hash: str) -> int:
        disconnected.append(session_hash)
        return 1

    app = FastAPI()
    app.include_router(
        create_auth_router(
            AuthHttpDependencies(
                lambda: url,
                disconnect_session=disconnect_session,
            )
        )
    )
    with TestClient(app) as client:
        client.cookies.set("trpg_session", first_token)
        response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert disconnected == [token_hash(first_token)]
    assert resolve_session(url, first_token) is None
    assert resolve_session(url, second_token).id == user.id


def test_revoked_session_authorization_fence_blocks_passive_room_broadcasts(
    tmp_path: Path,
):
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    user = create_user(url, "passive_session_user", "passive session password")
    token = create_login_session(url, user)
    identity = resolve_session_identity(url, token)
    assert identity is not None

    class CaptureSocket:
        def __init__(self):
            self.messages: list[dict] = []

        async def send_json(self, payload):
            self.messages.append(dict(payload))

    socket = CaptureSocket()

    async def scenario():
        hub = RoomEventHub("world-passive-revoke")
        await hub.attach(
            RoomConnection(
                "passive-tab",
                user.id,
                "player",
                socket,
                session_hash=identity.token_hash,
                authorization_check=identity.locally_valid,
            )
        )
        revoke_session(url, token)
        await hub.broadcast({"type": "narrative_chunk", "text": "after logout"})

    asyncio.run(scenario())
    assert socket.messages == []


def test_room_recovery_payload_contains_only_requesting_players_private_state(
    tmp_path: Path,
):
    import server

    state = {
        "active_investigator_id": "inv-alice",
        "pc": {"name": "Alice", "investigator_id": "inv-alice"},
        "investigator_controllers": {
            "user-alice": "inv-alice",
            "user-bob": "inv-bob",
        },
        "investigators": {
            "inv-alice": {"name": "Alice", "investigator_id": "inv-alice"},
            "inv-bob": {"name": "Bob", "investigator_id": "inv-bob"},
        },
        "clues_found": {
            "investigation": [
                {"id": "public", "text": "公共线索"},
                {
                    "id": "alice-secret",
                    "text": "Alice 的秘密",
                    "visibility": "private",
                    "owner_investigator_id": "inv-alice",
                },
                {
                    "id": "bob-secret",
                    "text": "Bob 的秘密",
                    "visibility": "private",
                    "owner_investigator_id": "inv-bob",
                },
            ]
        },
    }
    context = SimpleNamespace(
        world_store=SimpleNamespace(load=lambda: state),
        world_dir=tmp_path,
    )
    engine = SimpleNamespace(
        context=context,
        turn_journal=SimpleNamespace(public_history=lambda: [{"text": "公共叙事"}]),
    )
    room = GameRoom("world-private", engine, RoomEventHub("world-private"), "user-alice")
    alice_notes = PlayerNotesStore(tmp_path, user_id="user-alice")
    bob_notes = PlayerNotesStore(tmp_path, user_id="user-bob")
    with session_scope(alice_notes.database_url) as session:
        session.add_all(
            [
                User(
                    id="user-alice",
                    username="private_alice",
                    password_hash="test-only",
                ),
                User(
                    id="user-bob",
                    username="private_bob",
                    password_hash="test-only",
                ),
            ]
        )
    alice_notes.save("Alice 私人笔记", expected_revision=0)
    bob_notes.save("Bob 私人笔记", expected_revision=0)

    alice = asyncio.run(server.MULTIPLAYER_WS.room_full_recovery_payload(room, "user-alice"))
    bob = asyncio.run(server.MULTIPLAYER_WS.room_full_recovery_payload(room, "user-bob"))

    alice_wire = json.dumps(alice, ensure_ascii=False)
    bob_wire = json.dumps(bob, ensure_ascii=False)
    assert "公共叙事" in alice_wire and "公共叙事" in bob_wire
    assert "Alice 的秘密" in alice_wire
    assert "Alice 私人笔记" in alice_wire
    assert "Bob 的秘密" not in alice_wire
    assert "Bob 私人笔记" not in alice_wire
    assert "Bob 的秘密" in bob_wire
    assert "Bob 私人笔记" in bob_wire
    assert "Alice 的秘密" not in bob_wire
    assert "Alice 私人笔记" not in bob_wire


def test_pending_decision_is_recovered_only_for_its_actor(tmp_path: Path):
    import server

    class CaptureSocket:
        def __init__(self):
            self.messages: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.messages.append(dict(payload))

    state = {
        "investigator_controllers": {},
        "investigators": {},
        "clues_found": {},
    }
    context = SimpleNamespace(
        world_store=SimpleNamespace(load=lambda: state),
        world_dir=tmp_path,
    )
    engine = SimpleNamespace(
        context=context,
        turn_journal=SimpleNamespace(public_history=lambda: []),
    )
    room = GameRoom(
        "world-pending",
        engine,
        RoomEventHub("world-pending"),
        "user-alice",
        current_actor_user_id="user-alice",
    )
    transport = RoomDriverTransport(room)

    async def scenario():
        await room.reserve_action("user-alice", "pending-decision-turn")
        await transport.send_json(
            {
                "type": "decision_request",
                "id": "decision-7",
                "prompt": "选择处理方式",
                "options": [{"id": "wait", "label": "等待"}],
            }
        )
        alice = await server.MULTIPLAYER_WS.room_full_recovery_payload(room, "user-alice")
        bob = await server.MULTIPLAYER_WS.room_full_recovery_payload(room, "user-bob")
        socket = CaptureSocket()
        recovery_cursor = await server.MULTIPLAYER_WS.send_room_full_recovery(
            socket,
            room,
            "user-alice",
        )
        initial_attach = await room.hub.attach_with_replay(
            RoomConnection(
                "initial-recovered-tab",
                "user-alice",
                "owner",
                socket,
            ),
            recovery_cursor,
        )
        attached_socket = CaptureSocket()
        await room.hub.attach(
            RoomConnection(
                "recovered-tab",
                "user-alice",
                "owner",
                attached_socket,
            )
        )
        await server.MULTIPLAYER_WS.send_room_full_recovery(
            attached_socket,
            room,
            "user-alice",
            connection_id="recovered-tab",
        )
        room.release_action()
        return (
            alice,
            bob,
            initial_attach,
            socket.messages,
            attached_socket.messages,
        )

    alice, bob, initial_attach, messages, attached_messages = asyncio.run(scenario())

    assert alice["pending_reply"]["type"] == "decision_request"
    assert alice["pending_reply"]["id"] == "decision-7"
    assert alice["pending_reply"]["recovered"] is True
    assert bob["pending_reply"] is None
    assert initial_attach["gap"] is False
    assert [message["type"] for message in messages] == [
        "room_full_state",
        "decision_request",
    ]
    assert messages[1]["id"] == "decision-7"
    assert [message["type"] for message in attached_messages] == [
        "room_full_state",
        "decision_request",
    ]


def test_active_turn_recovery_keeps_pre_action_history_and_replays_live_events(
    tmp_path: Path,
):
    import server

    class CaptureSocket:
        def __init__(self):
            self.messages: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.messages.append(dict(payload))

    state = {
        "active_investigator_id": None,
        "investigator_controllers": {},
        "investigators": {},
        "clues_found": {},
    }
    journal_history = [{"turn_id": "history-read-during-active-action"}]
    context = SimpleNamespace(
        world_store=SimpleNamespace(load=lambda: state),
        world_dir=tmp_path,
    )
    engine = SimpleNamespace(
        context=context,
        turn_journal=SimpleNamespace(public_history=lambda: journal_history),
    )
    room = GameRoom(
        "world-active-recovery",
        engine,
        RoomEventHub("world-active-recovery"),
        "user-owner",
        current_actor_user_id="user-owner",
        status="playing",
    )
    room.recovery_history = [{"turn_id": "last-committed-before-action"}]

    async def scenario():
        await room.hub.broadcast({"type": "room_state", "status": "playing"})
        await room.reserve_action("user-owner", "active-action")
        await room.hub.broadcast(
            {
                "type": "narrative_chunk",
                "text": "this chunk arrived while reconnecting",
            }
        )
        with patch(
            "src.multiplayer_recovery.enrich_public_history",
            side_effect=lambda history, _engine: list(history),
        ):
            payload = await server.MULTIPLAYER_WS.room_full_recovery_payload(
                room,
                "user-owner",
            )
        socket = CaptureSocket()
        attached = await room.hub.attach_with_replay(
            RoomConnection("reconnected", "user-owner", "owner", socket),
            payload["latest_event_id"],
        )
        room.release_action()
        return payload, attached, socket.messages

    payload, attached, replayed = asyncio.run(scenario())

    assert payload["latest_event_id"] == 1
    assert payload["history"] == [{"turn_id": "last-committed-before-action"}]
    assert attached["gap"] is False
    assert [message["type"] for message in replayed] == ["narrative_chunk"]
    assert replayed[0]["room_event_id"] == 2


def test_initial_full_state_is_sent_before_events_created_at_its_boundary(
    tmp_path: Path,
):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "initial_barrier_owner", "owner password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-initial-barrier",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"room_status": "playing"},
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-initial-barrier",
                user_id=owner.id,
                role="owner",
            )
        )

    state = {
        "active_investigator_id": None,
        "investigator_controllers": {},
        "investigators": {},
        "clues_found": {},
    }
    context = SimpleNamespace(
        world_id="world-initial-barrier",
        module_name="mansion_of_madness",
        world_dir=tmp_path / "world-initial-barrier",
        world_store=SimpleNamespace(load=lambda: state),
    )
    engine = SimpleNamespace(
        context=context,
        narrative_model="test-narrative",
        judgement_model="test-judgement",
        turn_journal=SimpleNamespace(public_history=lambda: []),
        list_saves=lambda: [],
    )
    room = GameRoom(
        "world-initial-barrier",
        engine,
        RoomEventHub("world-initial-barrier"),
        owner.id,
        current_actor_user_id=owner.id,
        status="playing",
    )
    manager = RoomManager()
    manager._rooms[room.world_id] = room

    class InterleavingSocket:
        def __init__(self):
            self.query_params = {"world_id": room.world_id}
            self.client_state = SimpleNamespace(name="CONNECTED")
            self.messages: list[dict] = []
            self.injected = False

        async def accept(self):
            return None

        async def send_json(self, payload):
            if payload.get("type") == "room_full_state" and not self.injected:
                self.injected = True
                await room.hub.broadcast(
                    {
                        "type": "narrative_chunk",
                        "text": "created after snapshot boundary",
                    }
                )
            self.messages.append(dict(payload))

        async def receive_text(self):
            raise RuntimeError("test client disconnected")

        async def close(self, *, code, reason):
            del code, reason
            self.client_state.name = "DISCONNECTED"

    async def bootstrap(ws, _room):
        await ws.send_json({"type": "test_bootstrap"})

    socket = InterleavingSocket()
    with (
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        patch("src.multiplayer_ws.validate_websocket_origin"),
        patch(
            "src.multiplayer_ws.websocket_session",
            return_value=SimpleNamespace(
                user=SimpleNamespace(id=owner.id),
                token_hash="test-session",
                locally_valid=lambda: True,
            ),
        ),
        patch("src.multiplayer_ws.authorize_world", return_value="owner"),
        patch.object(
            server.MULTIPLAYER_WS,
            "room_bootstrap",
            new=bootstrap,
        ),
    ):
        asyncio.run(server.MULTIPLAYER_WS.websocket(socket))

    message_types = [message["type"] for message in socket.messages]
    full_state_index = message_types.index("room_full_state")
    boundary_event_index = next(
        index
        for index, message in enumerate(socket.messages)
        if message.get("text") == "created after snapshot boundary"
    )
    assert full_state_index < boundary_event_index


def test_failed_initial_bootstrap_is_retryable_and_leaves_no_ghost_connection(
    tmp_path: Path, capsys
):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "bootstrap_failure_owner", "owner password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-bootstrap-failure",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"room_status": "lobby"},
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-bootstrap-failure",
                user_id=owner.id,
                role="owner",
            )
        )

    room = GameRoom(
        "world-bootstrap-failure",
        object(),
        RoomEventHub("world-bootstrap-failure"),
        owner.id,
        current_actor_user_id=owner.id,
    )
    manager = RoomManager()
    manager._rooms[room.world_id] = room

    class FailedBootstrapSocket:
        def __init__(self):
            self.query_params = {"world_id": room.world_id}
            self.client_state = SimpleNamespace(name="CONNECTED")
            self.closed: tuple[int, str] | None = None

        async def accept(self):
            return None

        async def close(self, *, code, reason):
            self.closed = (code, reason)
            self.client_state.name = "DISCONNECTED"

    secret_detail = "/srv/trpg-master/runtime/private/bootstrap.db"

    async def failed_bootstrap(_ws, _room):
        raise RuntimeError(secret_detail)

    socket = FailedBootstrapSocket()
    with (
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        patch("src.multiplayer_ws.validate_websocket_origin"),
        patch(
            "src.multiplayer_ws.websocket_session",
            return_value=SimpleNamespace(
                user=SimpleNamespace(id=owner.id),
                token_hash="test-session",
                locally_valid=lambda: True,
            ),
        ),
        patch("src.multiplayer_ws.authorize_world", return_value="owner"),
        patch.object(
            server.MULTIPLAYER_WS,
            "room_bootstrap",
            new=failed_bootstrap,
        ),
    ):
        asyncio.run(server.MULTIPLAYER_WS.websocket(socket))

    assert room.connected_users == {}
    assert asyncio.run(room.hub.connection_snapshot()) == []
    assert socket.closed == (1011, "房间连接发生内部错误")
    assert secret_detail not in socket.closed[1]
    assert secret_detail in capsys.readouterr().err


def test_member_removed_during_pending_websocket_bootstrap_receives_no_recovery(
    tmp_path: Path,
):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "pending_race_owner", "owner password 123")
    player = create_user(url, "pending_race_player", "player password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-pending-race",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"room_status": "playing"},
            )
        )
        session.add_all(
            [
                WorldMember(
                    id=new_id("member"),
                    world_id="world-pending-race",
                    user_id=owner.id,
                    role="owner",
                ),
                WorldMember(
                    id=new_id("member"),
                    world_id="world-pending-race",
                    user_id=player.id,
                    role="player",
                ),
            ]
        )

    room = GameRoom(
        "world-pending-race",
        SimpleNamespace(),
        RoomEventHub("world-pending-race"),
        owner.id,
        current_actor_user_id=owner.id,
        status="playing",
    )
    manager = RoomManager()
    manager._rooms[room.world_id] = room

    class PendingSocket:
        def __init__(self):
            self.query_params = {"world_id": room.world_id}
            self.client_state = SimpleNamespace(name="CONNECTED")
            self.messages: list[dict] = []
            self.closed: tuple[int, str] | None = None

        async def accept(self):
            return None

        async def send_json(self, payload):
            self.messages.append(dict(payload))

        async def close(self, *, code, reason):
            self.closed = (code, reason)
            self.client_state.name = "DISCONNECTED"

    identity = SimpleNamespace(
        user=SimpleNamespace(id=player.id),
        token_hash="pending-race-session",
        locally_valid=lambda: True,
    )

    async def remove_during_bootstrap(_ws, target_room):
        remove_member(url, target_room.world_id, player.id, owner.id)
        await target_room.hub.disconnect_user(player.id)

    socket = PendingSocket()
    with (
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        patch("src.multiplayer_ws.validate_websocket_origin"),
        patch("src.multiplayer_ws.websocket_session", return_value=identity),
        patch.object(
            server.MULTIPLAYER_WS,
            "room_bootstrap",
            new=remove_during_bootstrap,
        ),
    ):
        asyncio.run(server.MULTIPLAYER_WS.websocket(socket))

    assert not any(message.get("type") == "room_full_state" for message in socket.messages)
    assert socket.closed == (4403, "房间成员权限已被移除")
    assert asyncio.run(room.hub.connection_snapshot()) == []
    with pytest.raises(MultiplayerError) as removed:
        room_members(url, room.world_id, player.id)
    assert removed.value.code == "not_a_member"


def test_continue_with_slot_is_owner_control_but_plain_continue_is_actor_control():
    assert owner_turn_required("continue", {"slot_id": "slot_001"}) is True
    assert owner_turn_required("continue", {"slot_id": "   "}) is False
    assert owner_turn_required("continue", {}) is False
    assert owner_turn_required("save_load", {"slot_id": "slot_001"}) is True


def test_player_notes_internal_error_is_logged_but_not_sent_to_client(tmp_path, capsys):
    secret_detail = f"{tmp_path}/private/player-notes.json: permission denied"

    class NoteSocket:
        def __init__(self):
            self.reads = 0
            self.messages: list[dict] = []

        async def receive_text(self):
            self.reads += 1
            if self.reads == 1:
                return json.dumps(
                    {
                        "type": "player_notes_update",
                        "revision": 0,
                        "text": "private note",
                    }
                )
            raise RuntimeError("test complete")

        async def send_json(self, payload):
            self.messages.append(dict(payload))

    room = GameRoom(
        "world-notes-error",
        SimpleNamespace(context=SimpleNamespace(world_dir=tmp_path)),
        RoomEventHub("world-notes-error"),
        "owner",
        current_actor_user_id="owner",
    )
    socket = NoteSocket()
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: "sqlite://"),
    )

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
        patch.object(PlayerNotesStore, "save", side_effect=OSError(secret_detail)),
        pytest.raises(RuntimeError, match="test complete"),
    ):
        asyncio.run(
            run_room_message_loop(
                controller,
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )
        )

    assert socket.messages == [
        {
            "type": "player_notes_error",
            "message": "玩家笔记暂时不可用，请稍后重试",
        }
    ]
    assert secret_detail not in json.dumps(socket.messages, ensure_ascii=False)
    assert secret_detail in capsys.readouterr().err


def test_durable_reservation_failure_releases_in_memory_room_lock():
    class OneMessageSocket:
        def __init__(self):
            self.reads = 0
            self.messages: list[dict] = []

        async def receive_text(self):
            self.reads += 1
            if self.reads == 1:
                return json.dumps(
                    {
                        "type": "save",
                        "slot_id": "slot_001",
                        "action_id": "db-unavailable",
                    }
                )
            raise RuntimeError("test complete")

        async def send_json(self, payload):
            self.messages.append(dict(payload))

    room = GameRoom(
        "world-reservation-error",
        SimpleNamespace(),
        RoomEventHub("world-reservation-error"),
        "owner",
        current_actor_user_id="owner",
        status="playing",
    )
    socket = OneMessageSocket()
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: "sqlite://"),
    )

    async def scenario():
        with (
            patch("src.multiplayer_messages.websocket_user", return_value=object()),
            patch("src.multiplayer_messages.authorize_world", return_value="owner"),
            patch(
                "src.multiplayer_messages.reserve_room_action",
                side_effect=RuntimeError("database unavailable"),
            ),
        ):
            with pytest.raises(RuntimeError, match="test complete"):
                await run_room_message_loop(
                    controller,
                    socket,
                    room,
                    SimpleNamespace(id="owner"),
                    room.world_id,
                    "owner-tab",
                    "owner",
                )

    asyncio.run(scenario())
    assert room.action_active is False
    assert socket.messages[-1]["code"] == "reservation_unavailable"


def test_unclaimed_owner_state_request_never_falls_back_to_active_player():
    class OneStateSocket:
        def __init__(self):
            self.reads = 0
            self.messages: list[dict] = []

        async def receive_text(self):
            self.reads += 1
            if self.reads == 1:
                return json.dumps({"type": "state"})
            raise RuntimeError("test complete")

        async def send_json(self, payload):
            self.messages.append(dict(payload))

    secret = "另一名调查员的私人状态"
    world_state = {
        "active_investigator_id": "inv-alice",
        "pc": {
            "investigator_id": "inv-alice",
            "controller_user_id": "alice",
            "name": "Alice",
            "backstory": secret,
        },
        "investigators": {
            "inv-alice": {
                "investigator_id": "inv-alice",
                "controller_user_id": "alice",
                "name": "Alice",
                "backstory": secret,
            }
        },
        "clues_found": {
            "private": [
                {
                    "id": "alice-secret",
                    "text": secret,
                    "visibility": "private",
                    "owner_investigator_id": "inv-alice",
                }
            ]
        },
    }
    context = SimpleNamespace(
        world_store=SimpleNamespace(load=lambda: world_state),
    )
    room = GameRoom(
        "world-owner-without-claim",
        SimpleNamespace(context=context),
        RoomEventHub("world-owner-without-claim"),
        "new-owner",
        current_actor_user_id="alice",
        status="playing",
    )
    socket = OneStateSocket()
    controller = SimpleNamespace(
        deps=SimpleNamespace(
            database_url=lambda: "sqlite://",
            enrich_clues=lambda clues, _state, _context: clues,
        ),
        authoritative_investigator_id=lambda *_args: None,
    )

    async def scenario():
        with (
            patch("src.multiplayer_messages.websocket_user", return_value=object()),
            patch("src.multiplayer_messages.authorize_world", return_value="owner"),
        ):
            with pytest.raises(RuntimeError, match="test complete"):
                await run_room_message_loop(
                    controller,
                    socket,
                    room,
                    SimpleNamespace(id="new-owner"),
                    room.world_id,
                    "new-owner-tab",
                    "owner",
                )

    asyncio.run(scenario())
    state_payload = next(
        payload for payload in socket.messages if payload["type"] == "state_data"
    )
    assert json.loads(state_payload["data"]) == {}
    assert secret not in state_payload["data"]
    assert secret not in state_payload["clues"]


def test_current_actor_member_mutation_is_serialized_with_turn_and_prompt(
    tmp_path: Path,
):
    import server

    combat_world_state = {"combat_state": {"active": False}}
    engine = SimpleNamespace(
        context=SimpleNamespace(
            world_store=SimpleNamespace(load=lambda: combat_world_state),
        )
    )
    room = GameRoom(
        "world-guard",
        engine,
        RoomEventHub("world-guard"),
        "user-owner",
        current_actor_user_id="user-actor",
    )
    room.driver_transport = RoomDriverTransport(room)
    manager = RoomManager()
    actor_request = SimpleNamespace(
        method="PATCH",
        url=SimpleNamespace(path="/api/worlds/world-guard/members/user-actor"),
    )
    other_request = SimpleNamespace(
        method="DELETE",
        url=SimpleNamespace(path="/api/worlds/world-guard/members/user-other"),
    )
    defender_request = SimpleNamespace(
        method="PATCH",
        url=SimpleNamespace(path="/api/worlds/world-guard/members/user-defender"),
    )
    owner_transfer_request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/worlds/world-guard/owner"),
    )

    async def scenario():
        await manager.get_or_create("world-guard", lambda: room)
        with patch.object(server, "ROOM_MANAGER", manager):
            await room.reserve_action(
                "user-actor",
                "active-turn",
                require_current_actor=False,
            )
            active_lease, active_rejection = await server._reserve_current_actor_member_mutation(
                actor_request
            )
            room.release_action()

            room.assign_actor("user-owner")
            await room.reserve_action(
                "user-owner",
                "owner-turn",
                require_current_actor=False,
            )
            owner_lease, owner_rejection = await server._reserve_current_actor_member_mutation(
                owner_transfer_request
            )
            room.release_action()
            room.assign_actor("user-actor")

            room.set_pending_reply("decision", "user-actor", request_id="decision-1")
            pending_lease, pending_rejection = await server._reserve_current_actor_member_mutation(
                actor_request
            )
            room.clear_pending_reply()

            room.set_pending_reply("decision", "user-defender", request_id="defend-1")
            defender_lease, defender_rejection = (
                await server._reserve_current_actor_member_mutation(defender_request)
            )
            room.clear_pending_reply()

            combat_world_state["combat_state"] = {
                "active": True,
                "participants": [
                    {"id": "inv-defender", "kind": "pc"},
                ],
            }
            combat_world_state["investigator_controllers"] = {
                "user-defender": "inv-defender"
            }
            combat_lease, combat_rejection = (
                await server._reserve_current_actor_member_mutation(defender_request)
            )
            combat_world_state["combat_state"] = {"active": False}

            idle_lease, idle_rejection = await server._reserve_current_actor_member_mutation(
                actor_request
            )
            idle_locked = room.action_active
            if idle_lease is not None:
                idle_lease.release_action()

            other_lease, other_rejection = await server._reserve_current_actor_member_mutation(
                other_request
            )
        return (
            active_lease,
            active_rejection,
            owner_lease,
            owner_rejection,
            pending_lease,
            pending_rejection,
            defender_lease,
            defender_rejection,
            combat_lease,
            combat_rejection,
            idle_lease,
            idle_rejection,
            idle_locked,
            other_lease,
            other_rejection,
        )

    (
        active_lease,
        active_rejection,
        owner_lease,
        owner_rejection,
        pending_lease,
        pending_rejection,
        defender_lease,
        defender_rejection,
        combat_lease,
        combat_rejection,
        idle_lease,
        idle_rejection,
        idle_locked,
        other_lease,
        other_rejection,
    ) = asyncio.run(scenario())

    assert active_lease is None
    assert active_rejection.status_code == 409
    assert owner_lease is None
    assert owner_rejection.status_code == 409
    assert pending_lease is None
    assert pending_rejection.status_code == 409
    assert defender_lease is None
    assert defender_rejection.status_code == 409
    assert combat_lease is None
    assert combat_rejection.status_code == 409
    assert idle_lease is room
    assert idle_rejection is None
    assert idle_locked is True
    assert other_lease is None
    assert other_rejection is None


def test_start_revalidates_roster_after_room_action_lease():
    lease_waiting = asyncio.Event()
    continue_lease = asyncio.Event()
    submitted: list[dict] = []
    roster_state = {
        "roster": [
            {
                "investigator_id": "inv-owner",
                "user_id": "owner",
                "character_ref": {"type": "inline", "data": {"name": "Owner"}},
            },
            {
                "investigator_id": "inv-player",
                "user_id": "player",
                "character_ref": {"type": "inline", "data": {"name": "Player"}},
            },
        ]
    }

    class StartSocket:
        def __init__(self):
            self.reads = 0
            self.messages: list[dict] = []

        async def receive_text(self):
            self.reads += 1
            if self.reads == 1:
                return json.dumps({"type": "start", "action_id": "start-race"})
            raise RuntimeError("test complete")

        async def send_json(self, payload):
            self.messages.append(dict(payload))

    class Driver:
        async def submit(self, payload):
            submitted.append(json.loads(payload))

    room = GameRoom(
        "world-start-race",
        SimpleNamespace(),
        RoomEventHub("world-start-race"),
        "owner",
        current_actor_user_id="owner",
        status="lobby",
        ready_users={"owner", "player"},
        connected_users={"owner": 1, "player": 1},
    )
    room.driver_transport = Driver()
    reserve_action = room.reserve_action

    async def blocked_reserve_action(*args, **kwargs):
        lease_waiting.set()
        await continue_lease.wait()
        await reserve_action(*args, **kwargs)

    room.reserve_action = blocked_reserve_action
    socket = StartSocket()
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: "sqlite://"),
        room_roster=lambda _world_id: (
            list(roster_state["roster"]),
            {"owner", "player"},
        ),
    )

    async def scenario():
        task = asyncio.create_task(
            run_room_message_loop(
                controller,
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )
        )
        await lease_waiting.wait()
        # Simulate a claim release committing while the start request is waiting
        # at the room-action lease boundary.
        roster_state["roster"] = roster_state["roster"][:1]
        continue_lease.set()
        with pytest.raises(RuntimeError, match="test complete"):
            await task

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
        patch("src.multiplayer_messages.reserve_room_action") as durable_reserve,
    ):
        asyncio.run(scenario())

    rejection = next(
        message
        for message in socket.messages
        if message.get("type") == "room_action_rejected"
    )
    assert rejection["code"] == "room_not_ready"
    assert rejection["missing_claim_user_ids"] == ["player"]
    assert submitted == []
    assert room.status == "lobby"
    assert room.action_active is False
    durable_reserve.assert_not_called()


def test_cloud_mode_disables_direct_module_asset_paths():
    import server

    with patch.object(server, "auth_required", return_value=True):
        response = asyncio.run(server.serve_asset("猩红文档", "莱特教授的尸体.png"))

    assert response.status_code == 404


def test_opening_failures_return_room_to_lobby_and_allow_retry(tmp_path: Path):
    import server

    class CaptureSocket:
        def __init__(self):
            self.queue: asyncio.Queue[dict] = asyncio.Queue()

        async def send_json(self, payload: dict) -> None:
            await self.queue.put(dict(payload))

        async def wait_for(self, message_type: str) -> dict:
            while True:
                message = await asyncio.wait_for(self.queue.get(), timeout=3)
                if message.get("type") == message_type:
                    return message

    class FakeEngine:
        def __init__(self):
            self.context = SimpleNamespace(
                world_id="world-start",
                module_name="test-module",
                runtime_root=tmp_path,
                world_dir=tmp_path / "world-start",
                world_store=SimpleNamespace(load=lambda: {"pc": {"name": "Alice"}}),
            )
            self.narrative_model = "test-model"
            self.judgement_model = "test-model"
            self.turn_journal = SimpleNamespace(read=lambda _turn_id: {})
            self.cb = None
            self._active_turn_id = None
            self.reset_attempts = 0
            self.model_attempts = 0

        @property
        def active_turn_id(self):
            return self._active_turn_id

        def reset(self, _character_ref=None):
            self.reset_attempts += 1
            if self.reset_attempts == 1:
                raise ValueError("测试开场初始化失败")

        def list_saves(self):
            return []

        def begin_turn_record(self, **_kwargs):
            self._active_turn_id = "opening-turn"
            return self._active_turn_id

        def finish_turn_record(self, **_kwargs):
            self._active_turn_id = None

        def handle_action(self, _content=None):
            self.model_attempts += 1
            # Model success means the real engine has committed before on_done.
            self._active_turn_id = None
            if self.model_attempts == 1:
                self.cb.on_error("测试模型失败")
                return
            self.cb.on_done()

        def record_turn_event(self, _payload):
            return None

        def cancel_active_turn(self):
            return None

        def is_valid_npc_id(self, _npc_id):
            return False

        def log_unknown_npc_speaker(self, _npc_id):
            return None

        def npc_speaker_aliases(self):
            return {}

    async def scenario():
        loop = asyncio.get_running_loop()
        engine = FakeEngine()
        room = GameRoom(
            "world-start",
            engine,
            RoomEventHub("world-start"),
            "user-owner",
            current_actor_user_id="user-owner",
            status="starting",
        )
        transport = RoomDriverTransport(room)
        capture = CaptureSocket()
        await room.hub.attach(
            RoomConnection(
                "capture",
                "user-owner",
                "owner",
                capture,
            )
        )

        def set_status(target_room, status):
            target_room.status = status

        def run_inline(_executor, callback, *args):
            future = loop.create_future()
            try:
                future.set_result(callback(*args))
            except Exception as exc:
                future.set_exception(exc)
            return future

        async def wait_for_terminal_delivery():
            for _ in range(100):
                if not room.terminal_event_pending:
                    return
                await asyncio.sleep(0)
            raise AssertionError("terminal delivery barrier did not clear")

        with (
            patch.object(server, "_list_mods", return_value=[]),
            patch.object(server, "list_character_options", return_value={"groups": []}),
            patch.object(server, "_load_theme", return_value={}),
            patch.object(
                server,
                "_model_settings_payload",
                return_value={"type": "model_settings"},
            ),
            patch.object(
                server.MULTIPLAYER_WS,
                "set_room_status",
                side_effect=set_status,
            ),
            patch.object(loop, "run_in_executor", side_effect=run_inline),
        ):
            task = asyncio.create_task(
                server.run_ws_session(transport, engine, user_id="user-owner")
            )
            await capture.wait_for("save_list")

            await room.reserve_action(
                "user-owner",
                "start-fails",
                require_current_actor=False,
            )
            room.control_action_active = True
            await transport.submit(json.dumps({"type": "start", "character_ref": {}}))
            failure = await capture.wait_for("error")
            await wait_for_terminal_delivery()
            first_status = room.status
            first_action_active = room.action_active

            room.status = "starting"
            await room.reserve_action(
                "user-owner",
                "start-retry",
                require_current_actor=False,
            )
            room.control_action_active = True
            await transport.submit(json.dumps({"type": "start", "character_ref": {}}))
            model_failure = await capture.wait_for("error")
            await capture.wait_for("done")
            await wait_for_terminal_delivery()
            model_failure_status = room.status
            model_failure_action_active = room.action_active

            room.status = "starting"
            await room.reserve_action(
                "user-owner",
                "start-after-model-failure",
                require_current_actor=False,
            )
            room.control_action_active = True
            await transport.submit(json.dumps({"type": "start", "character_ref": {}}))
            await capture.wait_for("done")
            await wait_for_terminal_delivery()
            retry_status = room.status
            retry_action_active = room.action_active

            await transport.close_input()
            await asyncio.wait_for(task, timeout=3)
        return (
            engine.reset_attempts,
            engine.model_attempts,
            failure,
            first_status,
            first_action_active,
            model_failure,
            model_failure_status,
            model_failure_action_active,
            retry_status,
            retry_action_active,
        )

    (
        attempts,
        model_attempts,
        failure,
        first_status,
        first_action_active,
        model_failure,
        model_failure_status,
        model_failure_action_active,
        retry_status,
        retry_action_active,
    ) = asyncio.run(scenario())

    assert attempts == 3
    assert model_attempts == 2
    assert failure["message"] == "测试开场初始化失败"
    assert first_status == "lobby"
    assert first_action_active is False
    assert model_failure["message"] == "测试模型失败"
    assert model_failure_status == "lobby"
    assert model_failure_action_active is False
    assert retry_status == "playing"
    assert retry_action_active is False


def test_unexpected_driver_exit_evicts_room_and_disconnects_members():
    import server

    class ClosableSocket:
        def __init__(self):
            self.closed: tuple[int, str] | None = None

        async def send_json(self, _payload):
            return None

        async def close(self, *, code: int, reason: str):
            self.closed = (code, reason)

    async def scenario():
        manager = RoomManager()
        room = GameRoom(
            "world-driver-crash",
            object(),
            RoomEventHub("world-driver-crash"),
            "user-owner",
            current_actor_user_id="user-owner",
            status="playing",
        )
        socket = ClosableSocket()
        await manager.get_or_create("world-driver-crash", lambda: room)
        room.member_connected("user-owner")
        await room.hub.attach(RoomConnection("owner-tab", "user-owner", "owner", socket))

        async def crash():
            raise RuntimeError("driver crashed")

        task = asyncio.create_task(crash())
        try:
            await task
        except RuntimeError:
            pass
        with patch.object(server, "ROOM_MANAGER", manager):
            await server.MULTIPLAYER_WS.report_room_driver_exit(room, task)

        replacement = GameRoom(
            "world-driver-crash",
            object(),
            RoomEventHub("world-driver-crash"),
            "user-owner",
        )
        recreated, created = await manager.get_or_create(
            "world-driver-crash",
            lambda: replacement,
        )
        return (
            created,
            recreated,
            await room.hub.connection_snapshot(),
            socket.closed,
        )

    created, recreated, old_connections, closed = asyncio.run(scenario())

    assert created is True
    assert recreated.world_id == "world-driver-crash"
    assert old_connections == []
    assert closed is not None


def test_unexpected_driver_exit_evicts_room_after_last_member_left():
    import server

    async def scenario():
        manager = RoomManager()
        room = GameRoom(
            "world-driver-crash-empty",
            object(),
            RoomEventHub("world-driver-crash-empty"),
            "user-owner",
            status="playing",
        )
        await manager.get_or_create("world-driver-crash-empty", lambda: room)
        room.member_connected("user-owner")
        room.member_disconnected("user-owner")

        async def crash():
            raise RuntimeError("driver crashed after disconnect")

        task = asyncio.create_task(crash())
        try:
            await task
        except RuntimeError:
            pass
        with patch.object(server, "ROOM_MANAGER", manager):
            await server.MULTIPLAYER_WS.report_room_driver_exit(room, task)

        replacement = GameRoom(
            "world-driver-crash-empty",
            object(),
            RoomEventHub("world-driver-crash-empty"),
            "user-owner",
        )
        recreated, created = await manager.get_or_create(
            "world-driver-crash-empty",
            lambda: replacement,
        )
        return room, recreated, created

    old_room, recreated, created = asyncio.run(scenario())

    assert created is True
    assert recreated is not old_room


def seed_accounts_and_world(url: str):
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "room_owner", "owner password 123")
    player = create_user(url, "room_player", "player password 123")
    stranger = create_user(url, "room_stranger", "stranger password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-room",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"name": "测试房间", "room_status": "lobby"},
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-room",
                user_id=owner.id,
                role="owner",
            )
        )
    return owner, player, stranger


def test_room_control_is_reconciled_after_slow_runtime_construction(
    tmp_path: Path,
):
    """DB changes made while RoomManager is loading must win over stale captures."""
    import server

    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    invite = create_invite(url, "world-room", owner.id, max_uses=1)
    accept_invite(url, invite["token"], player.id)
    transfer_owner(url, "world-room", player.id, owner.id)
    with session_scope(url) as session:
        world = session.get(World, "world-room")
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "room_status": "playing",
            # Models the member removal/role change half of the construction
            # race: the captured actor no longer belongs to the playable roster.
            "current_actor_user_id": stranger.id,
        }

    stale_room = GameRoom(
        "world-room",
        object(),
        RoomEventHub("world-room"),
        owner.id,
        current_actor_user_id=stranger.id,
        status="lobby",
        ready_users={owner.id, stranger.id},
    )
    with patch.object(server, "DATABASE_URL", url):
        server.MULTIPLAYER_WS.refresh_room_control(stale_room)

    assert stale_room.owner_user_id == player.id
    assert stale_room.current_actor_user_id == player.id
    assert stale_room.status == "playing"
    assert stale_room.ready_users == {owner.id}
    with session_scope(url) as session:
        world = session.get(World, "world-room")
        assert world.metadata_json["current_actor_user_id"] == player.id


@pytest.mark.parametrize(
    ("opening_statuses", "expected_status"),
    [
        pytest.param(["completed"], "playing", id="latest-opening-committed"),
        pytest.param(
            ["completed", "failed"],
            "lobby",
            id="older-opening-must-not-mask-latest-failure",
        ),
    ],
)
def test_starting_room_recovers_from_latest_opening_outcome(
    tmp_path: Path,
    opening_statuses: list[str],
    expected_status: str,
):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    env = {
        "TRPG_DATABASE_URL": url,
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOW_REGISTRATION": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
        "TRPG_WRITE_COMPAT_EXPORTS": "0",
        "TRPG_ROOM_IDLE_SECONDS": "0",
    }
    origin = {"origin": "https://testserver"}
    manager = RoomManager()
    restore_calls: list[str] = []

    class FakeEngine:
        def __init__(self, context):
            self.context = context
            self.narrative_model = "test-narrative"
            self.judgement_model = "test-judgement"
            self.turn_journal = SimpleNamespace(public_history=lambda: [])

        def configure_models(self, narrative, judgement):
            self.narrative_model = narrative
            self.judgement_model = judgement

        def prepare_session(self):
            return None

        def restore_latest_committed_history(self):
            restore_calls.append(self.context.world_id)

        def list_saves(self):
            return []

    async def fake_room_driver(transport, _engine, *, user_id=None):
        del user_id
        try:
            while True:
                await transport.receive_text()
        except RuntimeError:
            return

    context = SimpleNamespace(
        world_id="world-committed-opening",
        module_name="mansion_of_madness",
        world_dir=tmp_path / "world-committed-opening",
        world_store=SimpleNamespace(
            load=lambda: {
                "active_investigator_id": None,
                "investigator_controllers": {},
                "investigators": {},
                "clues_found": {},
            },
            update=lambda mutator: mutator({}),
        ),
    )

    with (
        patch.dict(os.environ, env),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        patch.object(server, "GameEngine", side_effect=FakeEngine),
        patch.object(server, "run_ws_session", new=fake_room_driver),
        patch(
            "src.multiplayer_ws.RuntimeContext.create",
            return_value=context,
        ),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "committed_opening_owner",
                "password": "owner password 123",
            },
        )
        assert registered.status_code == 201
        owner_id = registered.json()["id"]
        owner_cookie = client.cookies.get("trpg_session")
        assert owner_cookie
        with session_scope(url) as session:
            session.add(
                World(
                    id="world-committed-opening",
                    module_name="mansion_of_madness",
                    created_by=owner_id,
                    metadata_json={
                        "name": "已提交开场",
                        "room_status": "starting",
                    },
                )
            )
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id="world-committed-opening",
                    user_id=owner_id,
                    role="owner",
                )
            )
            session.flush()
            created_at = datetime(2026, 1, 1, tzinfo=UTC)
            for index, status in enumerate(opening_statuses):
                turn_id = f"opening-before-crash-{index}"
                session.add(
                    Turn(
                        pk=new_id("turn"),
                        id=turn_id,
                        world_id="world-committed-opening",
                        parent_turn_id=None,
                        origin_world_id="world-committed-opening",
                        kind="opening",
                        status=status,
                        owner_token="previous-process",
                        player_input=None,
                        record={"turn_id": turn_id},
                        messages=[],
                        created_at=created_at + timedelta(seconds=index),
                    )
                )
        with client.websocket_connect(
            "/ws/room?world_id=world-committed-opening",
            headers={
                **origin,
                "cookie": f"trpg_session={owner_cookie}",
            },
        ) as websocket:
            full_state = _receive_until(websocket, "room_full_state")

        assert full_state["status"] == expected_status
        assert restore_calls == (
            ["world-committed-opening"] if expected_status == "playing" else []
        )
        with session_scope(url) as session:
            world = session.get(World, "world-committed-opening")
            assert world.metadata_json["room_status"] == expected_status


def test_invitation_is_hashed_limited_and_idempotent_for_members(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    invite = create_invite(url, "world-room", owner.id, max_uses=1)
    assert "token" in invite
    with session_scope(url) as session:
        row = session.get(WorldInvite, invite["invite_id"])
        assert row.token_hash == hashlib.sha256(invite["token"].encode()).hexdigest()
        assert invite["token"] not in row.token_hash

    joined = accept_invite(url, invite["token"], player.id)
    assert joined == {
        "world_id": "world-room",
        "role": "player",
        "already_member": False,
    }
    assert accept_invite(url, invite["token"], player.id)["already_member"] is True
    with pytest.raises(MultiplayerError, match="使用次数") as exhausted:
        accept_invite(url, invite["token"], stranger.id)
    assert exhausted.value.code == "invite_exhausted"


def test_invite_listing_hides_tokens_and_owner_can_be_transferred(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    invite = create_invite(url, "world-room", owner.id, max_uses=2)
    accept_invite(url, invite["token"], player.id)

    listed = list_invites(url, "world-room", owner.id)
    assert listed["invites"][0]["invite_id"] == invite["invite_id"]
    assert "token" not in listed["invites"][0]
    with pytest.raises(MultiplayerError) as forbidden:
        list_invites(url, "world-room", player.id)
    assert forbidden.value.code == "owner_required"

    transferred = transfer_owner(url, "world-room", player.id, owner.id)
    assert transferred["owner_user_id"] == player.id
    state = room_members(url, "world-room", player.id)
    roles = {member["user_id"]: member["role"] for member in state["members"]}
    assert roles == {owner.id: "player", player.id: "owner"}
    with session_scope(url) as session:
        assert session.get(World, "world-room").created_by == player.id
    with pytest.raises(MultiplayerError) as former_owner:
        transfer_owner(url, "world-room", stranger.id, owner.id)
    assert former_owner.value.code == "owner_required"
    with pytest.raises(MultiplayerError) as stale_owner_action:
        reserve_room_action(
            url,
            "world-room",
            "former-owner-control",
            owner.id,
            "save",
            required_permission="manage",
        )
    assert stale_owner_action.value.code == "owner_required"


def test_player_invite_respects_room_capacity(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    with session_scope(url) as session:
        world = session.get(World, "world-room")
        world.metadata_json = {**world.metadata_json, "max_players": 2}
    invite = create_invite(url, "world-room", owner.id, max_uses=2)
    accept_invite(url, invite["token"], player.id)
    with pytest.raises(MultiplayerError) as full:
        accept_invite(url, invite["token"], stranger.id)
    assert full.value.code == "world_full"
    viewer_invite = create_invite(url, "world-room", owner.id, role="viewer", max_uses=1)
    accept_invite(url, viewer_invite["token"], stranger.id)
    with pytest.raises(MultiplayerError) as promote_full:
        update_member_role(url, "world-room", stranger.id, owner.id, "player")
    assert promote_full.value.code == "world_full"
    with pytest.raises(MultiplayerError) as transfer_full:
        transfer_owner(url, "world-room", stranger.id, owner.id)
    assert transfer_full.value.code == "world_full"
    with pytest.raises(MultiplayerError) as viewer_action:
        reserve_room_action(
            url,
            "world-room",
            "viewer-action",
            stranger.id,
            "action",
            required_permission="play",
        )
    assert viewer_action.value.code == "player_required"


def test_playing_room_allows_viewers_but_rejects_new_player_admission(
    tmp_path: Path,
):
    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    player_invite = create_invite(
        url,
        "world-room",
        owner.id,
        role="player",
        max_uses=2,
    )
    viewer_invite = create_invite(
        url,
        "world-room",
        owner.id,
        role="viewer",
        max_uses=2,
    )
    with session_scope(url) as session:
        world = session.get(World, "world-room")
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "room_status": "playing",
        }

    with pytest.raises(MultiplayerError) as player_join:
        accept_invite(url, player_invite["token"], player.id)
    assert player_join.value.code == "room_already_started"
    assert player_join.value.status_code == 409

    joined_viewer = accept_invite(url, viewer_invite["token"], player.id)
    assert joined_viewer == {
        "world_id": "world-room",
        "role": "viewer",
        "already_member": False,
    }
    # Accepting another invite never silently upgrades an existing viewer.
    assert accept_invite(url, player_invite["token"], player.id) == {
        "world_id": "world-room",
        "role": "viewer",
        "already_member": True,
    }
    assert accept_invite(url, viewer_invite["token"], stranger.id)["role"] == "viewer"

    with pytest.raises(MultiplayerError) as promote:
        update_member_role(url, "world-room", player.id, owner.id, "player")
    assert promote.value.code == "room_already_started"
    assert promote.value.status_code == 409
    with pytest.raises(MultiplayerError) as transfer:
        transfer_owner(url, "world-room", player.id, owner.id)
    assert transfer.value.code == "room_already_started"


def test_room_action_idempotency_survives_room_runtime_recreation(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, _player, _stranger = seed_accounts_and_world(url)
    reserve_room_action(url, "world-room", "stable-action-1", owner.id, "action")
    with pytest.raises(MultiplayerError) as duplicate:
        reserve_room_action(url, "world-room", "stable-action-1", owner.id, "action")
    assert duplicate.value.code == "duplicate_action"
    finish_room_action(url, "world-room", "stable-action-1", "completed")
    assert recover_room_actions(url, "world-room") == 0
    with pytest.raises(MultiplayerError):
        reserve_room_action(url, "world-room", "stable-action-1", owner.id, "action")

    reserve_room_action(url, "world-room", "interrupted-action", owner.id, "action")
    assert recover_room_actions(url, "world-room") == 1
    # A restart cannot know whether the world transaction committed just
    # before the independent action-status write. Fail closed rather than
    # replaying a possibly completed side effect.
    with pytest.raises(MultiplayerError) as uncertain:
        reserve_room_action(
            url,
            "world-room",
            "interrupted-action",
            owner.id,
            "action",
        )
    assert uncertain.value.code == "duplicate_action"
    with session_scope(url) as session:
        statuses = {
            row.action_id: row.status
            for row in session.query(RoomAction).filter_by(world_id="world-room")
        }
    assert statuses == {
        "stable-action-1": "completed",
        "interrupted-action": "unknown",
    }


def test_member_roles_and_investigator_claims_are_authoritative(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    token = create_invite(url, "world-room", owner.id, max_uses=2)["token"]
    accept_invite(url, token, player.id)
    accept_invite(url, token, stranger.id)

    first = claim_investigator(url, "world-room", "detective-huang", player.id)
    with pytest.raises(MultiplayerError) as taken:
        claim_investigator(url, "world-room", "detective-huang", stranger.id)
    assert taken.value.code == "investigator_taken"

    update_member_role(url, "world-room", player.id, owner.id, "viewer")
    state = room_members(url, "world-room", owner.id)
    player_row = next(member for member in state["members"] if member["user_id"] == player.id)
    assert player_row["role"] == "viewer"
    assert player_row["investigator"] is None
    with session_scope(url) as session:
        assert session.get(WorldInvestigator, first["id"]).status == "available"

    with pytest.raises(MultiplayerError) as viewer_claim:
        claim_investigator(url, "world-room", "detective-huang", player.id)
    assert viewer_claim.value.code == "player_required"

    remove_member(url, "world-room", stranger.id, stranger.id)
    with pytest.raises(MultiplayerError) as missing:
        room_members(url, "world-room", stranger.id)
    assert missing.value.code == "not_a_member"


def test_http_player_demotion_revokes_world_state_private_controller(tmp_path: Path):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "demotion_owner", "owner password 123")
    player = create_user(url, "demotion_player", "player password 123")
    owner_token = create_login_session(url, owner)
    with session_scope(url) as session:
        session.add(
            World(
                id="world-demotion",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={
                    "room_status": "playing",
                    "max_players": 2,
                },
            )
        )
        session.add_all(
            [
                WorldMember(
                    id=new_id("member"),
                    world_id="world-demotion",
                    user_id=owner.id,
                    role="owner",
                ),
                WorldMember(
                    id=new_id("member"),
                    world_id="world-demotion",
                    user_id=player.id,
                    role="player",
                ),
                WorldInvestigator(
                    id="investigator-player",
                    world_id="world-demotion",
                    character_key="detective-player",
                    character_ref={"source": "default", "id": "detective-player"},
                    controller_user_id=player.id,
                    status="claimed",
                ),
            ]
        )

    store = DatabaseWorldStore(
        url,
        "world-demotion",
        tmp_path / "worlds" / "world-demotion",
    )
    store.initialize(
        {
            "pc": {
                "name": "Player",
                "investigator_id": "investigator-player",
                "controller_user_id": player.id,
            },
            "active_investigator_id": "investigator-player",
            "investigator_controllers": {
                player.id: "investigator-player",
            },
            "investigators": {
                "investigator-player": {
                    "name": "Player",
                    "investigator_id": "investigator-player",
                    "controller_user_id": player.id,
                }
            },
            "clues_found": {
                "investigation": [
                    {
                        "id": "player-secret",
                        "text": "只属于原玩家",
                        "visibility": "private",
                        "owner_investigator_id": "investigator-player",
                    }
                ]
            },
        }
    )
    room = GameRoom(
        "world-demotion",
        SimpleNamespace(context=SimpleNamespace(world_store=store)),
        RoomEventHub("world-demotion"),
        owner.id,
        current_actor_user_id=owner.id,
        status="playing",
    )

    class ConnectedPlayerSocket:
        def __init__(self):
            self.closed: tuple[int, str] | None = None

        async def send_json(self, _payload):
            return None

        async def close(self, *, code, reason):
            self.closed = (code, reason)

    player_socket = ConnectedPlayerSocket()
    asyncio.run(
        room.hub.attach(
            RoomConnection(
                "demoted-player-tab",
                player.id,
                "player",
                player_socket,
            )
        )
    )
    manager = RoomManager()
    manager._rooms[room.world_id] = room
    env = {
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
    }
    with (
        patch.dict(os.environ, env),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        client.cookies.set("trpg_session", owner_token)
        response = client.patch(
            f"/api/worlds/{room.world_id}/members/{player.id}",
            json={"role": "viewer"},
            headers={"origin": "https://testserver"},
        )

    assert response.status_code == 200
    assert player_socket.closed == (4409, "房间角色已更新，请重新连接")
    assert asyncio.run(room.hub.connection_snapshot()) == []
    state = store.load()
    assert player.id not in state["investigator_controllers"]
    assert state["investigators"]["investigator-player"]["controller_user_id"] is None
    assert state["pc"]["controller_user_id"] is None
    with session_scope(url) as session:
        member = (
            session.query(WorldMember).filter_by(world_id=room.world_id, user_id=player.id).one()
        )
        claim = session.get(WorldInvestigator, "investigator-player")
        assert member.role == "viewer"
        assert claim.controller_user_id is None
        assert claim.status == "available"


def test_http_playing_room_rejects_player_join_and_viewer_promotion(
    tmp_path: Path,
):
    import server

    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    owner_token = create_login_session(url, owner)
    player_token = create_login_session(url, player)
    stranger_token = create_login_session(url, stranger)
    player_invite = create_invite(
        url,
        "world-room",
        owner.id,
        role="player",
        max_uses=2,
    )
    viewer_invite = create_invite(
        url,
        "world-room",
        owner.id,
        role="viewer",
        max_uses=2,
    )
    with session_scope(url) as session:
        world = session.get(World, "world-room")
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "room_status": "playing",
        }

    env = {
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
    }
    headers = {"origin": "https://testserver"}
    with (
        patch.dict(os.environ, env),
        patch.object(server, "DATABASE_URL", url),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        client.cookies.set("trpg_session", player_token)
        denied_join = client.post(
            "/api/invites/accept",
            json={"token": player_invite["token"]},
            headers=headers,
        )
        assert denied_join.status_code == 409
        assert denied_join.json()["code"] == "room_already_started"

        viewer_join = client.post(
            "/api/invites/accept",
            json={"token": viewer_invite["token"]},
            headers=headers,
        )
        assert viewer_join.status_code == 200
        assert viewer_join.json()["role"] == "viewer"
        existing_viewer = client.post(
            "/api/invites/accept",
            json={"token": player_invite["token"]},
            headers=headers,
        )
        assert existing_viewer.status_code == 200
        assert existing_viewer.json() == {
            "world_id": "world-room",
            "role": "viewer",
            "already_member": True,
        }

        client.cookies.set("trpg_session", owner_token)
        denied_promotion = client.patch(
            f"/api/worlds/world-room/members/{player.id}",
            json={"role": "player"},
            headers=headers,
        )
        assert denied_promotion.status_code == 409
        assert denied_promotion.json()["code"] == "room_already_started"

        client.cookies.set("trpg_session", stranger_token)
        second_viewer = client.post(
            "/api/invites/accept",
            json={"token": viewer_invite["token"]},
            headers=headers,
        )
        assert second_viewer.status_code == 200
        assert second_viewer.json()["role"] == "viewer"


def test_claim_can_be_released_only_by_controller_or_owner(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, player, stranger = seed_accounts_and_world(url)
    token = create_invite(url, "world-room", owner.id, max_uses=2)["token"]
    accept_invite(url, token, player.id)
    accept_invite(url, token, stranger.id)
    claim = claim_investigator(url, "world-room", "detective-huang", player.id)

    with pytest.raises(MultiplayerError) as denied:
        release_investigator(url, "world-room", claim["id"], stranger.id)
    assert denied.value.code == "claim_owner_required"
    release_investigator(url, "world-room", claim["id"], owner.id)
    assert (
        claim_investigator(url, "world-room", "detective-huang", stranger.id)["user_id"]
        == stranger.id
    )


def test_investigator_claims_are_locked_after_room_starts(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, player, _stranger = seed_accounts_and_world(url)
    token = create_invite(url, "world-room", owner.id, max_uses=1)["token"]
    accept_invite(url, token, player.id)
    claim = claim_investigator(url, "world-room", "detective-huang", player.id)
    with session_scope(url) as session:
        world = session.get(World, "world-room")
        world.metadata_json = {**world.metadata_json, "room_status": "playing"}

    with pytest.raises(MultiplayerError) as replace:
        claim_investigator(url, "world-room", "another-detective", player.id)
    assert replace.value.code == "room_already_started"
    with pytest.raises(MultiplayerError) as release:
        release_investigator(url, "world-room", claim["id"], player.id)
    assert release.value.code == "room_already_started"

    state = room_members(url, "world-room", owner.id)
    player_row = next(row for row in state["members"] if row["user_id"] == player.id)
    assert player_row["investigator"]["id"] == claim["id"]


def test_multiplayer_http_invite_join_and_claim_flow(tmp_path: Path):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    env = {
        "TRPG_DATABASE_URL": url,
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOW_REGISTRATION": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
        "TRPG_WRITE_COMPAT_EXPORTS": "0",
        "TRPG_ROOM_IDLE_SECONDS": "0",
    }
    headers = {"origin": "https://testserver"}
    with patch.dict(os.environ, env), patch.object(server, "DATABASE_URL", url):
        with TestClient(server.app, base_url="https://testserver") as owner_client:
            assert (
                owner_client.post(
                    "/api/auth/register",
                    json={"username": "http_owner", "password": "owner password 123"},
                ).status_code
                == 201
            )
            invalid = owner_client.post(
                "/api/worlds",
                json={
                    "module": "mansion_of_madness",
                    "name": "不应落库",
                    "max_players": "abc",
                },
                headers=headers,
            )
            assert invalid.status_code == 400
            assert invalid.json()["code"] == "invalid_max_players"
            with session_scope(url) as session:
                assert session.query(World).count() == 0
            created = owner_client.post(
                "/api/worlds",
                json={
                    "module": "mansion_of_madness",
                    "name": "周五调查团",
                    "max_players": 3,
                },
                headers=headers,
            )
            assert created.status_code == 201
            world_id = created.json()["world_id"]
            invite = owner_client.post(
                f"/api/worlds/{world_id}/invites",
                json={"role": "player", "max_uses": 1},
                headers=headers,
            )
            assert invite.status_code == 201
            listed_invites = owner_client.get(f"/api/worlds/{world_id}/invites")
            assert listed_invites.status_code == 200
            assert "token" not in listed_invites.json()["invites"][0]

            with TestClient(server.app, base_url="https://testserver") as player_client:
                assert (
                    player_client.post(
                        "/api/auth/register",
                        json={"username": "http_player", "password": "player password 123"},
                    ).status_code
                    == 201
                )
                joined = player_client.post(
                    "/api/invites/accept",
                    json={"token": invite.json()["token"]},
                    headers=headers,
                )
                assert joined.status_code == 200
                api_paths = server.app.openapi()["paths"]
                assert "/api/invites/accept" in api_paths
                assert "/api/invites/{token}/accept" not in api_paths
                options = player_client.get(f"/api/worlds/{world_id}/investigators/options")
                assert options.status_code == 200
                character_key = next(
                    character["id"]
                    for group in options.json()["groups"]
                    for character in group["characters"]
                )
                claimed = player_client.post(
                    f"/api/worlds/{world_id}/investigators/claim",
                    json={"character_key": character_key},
                    headers=headers,
                )
                assert claimed.status_code == 200
                transferred = owner_client.post(
                    f"/api/worlds/{world_id}/owner",
                    json={"user_id": claimed.json()["user_id"]},
                    headers=headers,
                )
                assert transferred.status_code == 200
                assert transferred.json()["owner_user_id"] == claimed.json()["user_id"]
                assert player_client.get(f"/api/worlds/{world_id}/invites").status_code == 200

            members = owner_client.get(f"/api/worlds/{world_id}/members")
            assert members.status_code == 200
            assert members.json()["metadata"]["name"] == "周五调查团"
            assert len(members.json()["members"]) == 2
            assert any(row["investigator"] for row in members.json()["members"])
            roles = {row["username"]: row["role"] for row in members.json()["members"]}
            assert roles == {"http_owner": "player", "http_player": "owner"}


def _receive_until(websocket, message_type: str, limit: int = 20):
    for _ in range(limit):
        message = websocket.receive_json()
        if message.get("type") == message_type:
            return message
    raise AssertionError(f"did not receive {message_type}")


def test_shared_room_websocket_creates_one_engine_and_enforces_actor(tmp_path: Path):
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    env = {
        "TRPG_DATABASE_URL": url,
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOW_REGISTRATION": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
        "TRPG_WRITE_COMPAT_EXPORTS": "0",
        "TRPG_ROOM_IDLE_SECONDS": "0",
    }
    origin = {"origin": "https://testserver"}
    manager = RoomManager()
    created_engines = []
    submitted_messages = []

    class FakeEngine:
        def __init__(self, context):
            self.context = context
            self.narrative_model = "test-narrative"
            self.judgement_model = "test-judgement"

        def configure_models(self, narrative, judgement):
            self.narrative_model = narrative
            self.judgement_model = judgement

        def prepare_session(self):
            return None

        def list_saves(self):
            return []

    def engine_factory(*args, **_kwargs):
        engine = FakeEngine(*args)
        created_engines.append(engine)
        return engine

    async def fake_room_driver(transport, _engine, *, user_id=None):
        del user_id
        try:
            while True:
                data = json.loads(await transport.receive_text())
                submitted_messages.append(data)
                if data.get("type") == "action":
                    await transport.send_json(
                        {"type": "gm_turn_start", "turn_id": "test-turn", "seq": 1}
                    )
                elif data.get("type") in {"start", "save_load"}:
                    await transport.send_json({"type": "done"})
                elif data.get("type") == "save_create":
                    await transport.send_json({"type": "saved", "ok": True, "slot_id": "slot_001"})
        except RuntimeError:
            return

    with (
        patch.dict(os.environ, env),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        patch.object(server, "GameEngine", side_effect=engine_factory),
        patch.object(server, "run_ws_session", new=fake_room_driver),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        owner = client.post(
            "/api/auth/register",
            json={"username": "socket_owner", "password": "owner password 123"},
        )
        owner_id = owner.json()["id"]
        owner_cookie = client.cookies.get("trpg_session")
        created = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "name": "共享引擎房"},
            headers=origin,
        )
        world_id = created.json()["world_id"]
        invite = client.post(
            f"/api/worlds/{world_id}/invites",
            json={"max_uses": 1},
            headers=origin,
        ).json()["token"]
        player = client.post(
            "/api/auth/register",
            json={"username": "socket_player", "password": "player password 123"},
        )
        player_id = player.json()["id"]
        player_cookie = client.cookies.get("trpg_session")
        client.post(
            "/api/invites/accept",
            json={"token": invite},
            headers=origin,
        )
        viewer = create_user(url, "socket_viewer", "viewer password 123")
        viewer_cookie = create_login_session(url, viewer)
        with session_scope(url) as session:
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id=world_id,
                    user_id=viewer.id,
                    role="viewer",
                )
            )
        owner_claim = claim_investigator(
            url,
            world_id,
            "owner-character",
            owner_id,
            character_ref={"type": "inline", "data": {"name": "房主调查员"}},
        )
        player_claim = claim_investigator(
            url,
            world_id,
            "player-character",
            player_id,
            character_ref={"type": "inline", "data": {"name": "玩家调查员"}},
        )

        with client.websocket_connect(
            f"/ws/room?world_id={world_id}",
            headers={**origin, "cookie": f"trpg_session={owner_cookie}"},
        ) as owner_ws:
            owner_state = _receive_until(owner_ws, "room_state")
            assert owner_state["current_actor_user_id"] == owner_id
            owner_ws.send_json({"type": "actor_assign", "user_id": player_id})
            offline = _receive_until(owner_ws, "room_action_rejected")
            assert offline["code"] == "actor_offline"
            with client.websocket_connect(
                f"/ws/room?world_id={world_id}",
                headers={**origin, "cookie": f"trpg_session={viewer_cookie}"},
            ) as viewer_ws:
                _receive_until(viewer_ws, "room_state")
                with client.websocket_connect(
                    f"/ws/room?world_id={world_id}",
                    headers={**origin, "cookie": f"trpg_session={viewer_cookie}"},
                ) as viewer_second_ws:
                    _receive_until(viewer_second_ws, "room_state")
                    owner_ws.send_json({"type": "ping"})
                    _receive_until(owner_ws, "pong")
                    viewer_ws.send_json(
                        {
                            "type": "player_notes_update",
                            "revision": 0,
                            "text": "旁观者自己的笔记",
                        }
                    )
                    viewer_note = _receive_until(viewer_ws, "player_notes")
                    viewer_second_note = _receive_until(viewer_second_ws, "player_notes")
                    assert viewer_second_note["text"] == viewer_note["text"]
                    owner_ws.send_json({"type": "ping"})
                    assert owner_ws.receive_json()["type"] == "pong"

                    owner_ws.send_json({"type": "actor_assign", "user_id": viewer.id})
                    ineligible = _receive_until(owner_ws, "room_action_rejected")
                    assert ineligible["code"] == "invalid_actor"
            with client.websocket_connect(
                f"/ws/room?world_id={world_id}",
                headers={**origin, "cookie": f"trpg_session={player_cookie}"},
            ) as player_ws:
                _receive_until(player_ws, "room_state")
                assert len(created_engines) == 1
                player_ws.send_json({"type": "actor_assign", "user_id": player_id})
                denied = _receive_until(player_ws, "room_action_rejected")
                assert denied["code"] == "owner_required"
                player_ws.send_json(
                    {
                        "type": "save_load",
                        "slot_id": "slot_000",
                        "action_id": "player-load-denied",
                    }
                )
                load_denied = _receive_until(player_ws, "room_action_rejected")
                assert load_denied["code"] == "owner_required"

                with client.websocket_connect(
                    f"/ws/room?world_id={world_id}",
                    headers={**origin, "cookie": f"trpg_session={player_cookie}"},
                ) as player_second_ws:
                    _receive_until(player_second_ws, "room_state")
                    owner_ws.send_json({"type": "ping"})
                    _receive_until(owner_ws, "pong")
                    player_ws.send_json(
                        {
                            "type": "player_notes_update",
                            "revision": 0,
                            "text": "只属于玩家的秘密笔记",
                        }
                    )
                    player_note = _receive_until(player_ws, "player_notes")
                    second_note = _receive_until(player_second_ws, "player_notes")
                    assert player_note["text"] == "只属于玩家的秘密笔记"
                    assert second_note["text"] == player_note["text"]
                    assert second_note["revision"] == player_note["revision"]

                    owner_ws.send_json({"type": "ping"})
                    assert owner_ws.receive_json()["type"] == "pong"

                    player_ws.send_json({"type": "player_notes_get"})
                    loaded_note = _receive_until(player_ws, "player_notes")
                    assert loaded_note["text"] == "只属于玩家的秘密笔记"
                    player_second_ws.send_json({"type": "ping"})
                    assert player_second_ws.receive_json()["type"] == "pong"

                    player_ws.send_json(
                        {
                            "type": "player_notes_update",
                            "revision": 0,
                            "text": "过期覆盖",
                        }
                    )
                    conflict = _receive_until(player_ws, "player_notes_conflict")
                    assert conflict["text"] == "只属于玩家的秘密笔记"
                    player_second_ws.send_json({"type": "ping"})
                    assert player_second_ws.receive_json()["type"] == "pong"

                    player_ws.send_json(
                        {
                            "type": "player_notes_update",
                            "revision": "not-an-integer",
                            "text": "无效更新",
                        }
                    )
                    _receive_until(player_ws, "player_notes_error")
                    player_second_ws.send_json({"type": "ping"})
                    assert player_second_ws.receive_json()["type"] == "pong"
                owner_ws.send_json({"type": "player_notes_get"})
                owner_note = _receive_until(owner_ws, "player_notes")
                assert owner_note["text"] == ""
                player_ws.send_json({"type": "world_list"})
                unsupported = _receive_until(player_ws, "protocol_error")
                assert unsupported["code"] == "unsupported_room_message"
                player_ws.send_json({"type": "turn_diagnostics_get"})
                diagnostics_denied = _receive_until(player_ws, "room_action_rejected")
                assert diagnostics_denied["code"] == "owner_required"

                owner_ws.send_json({"type": "start", "action_id": "start-before-ready"})
                not_ready = _receive_until(owner_ws, "room_action_rejected")
                assert not_ready["code"] == "room_not_ready"
                owner_ws.send_json({"type": "room_ready", "ready": True})
                player_ws.send_json({"type": "room_ready", "ready": True})
                _receive_until(owner_ws, "room_state")
                owner_ws.send_json({"type": "start", "action_id": "start-ready"})
                _receive_until(owner_ws, "done")
                start_message = next(item for item in submitted_messages if item["type"] == "start")
                assert start_message["_room_investigator_id"] == owner_claim["id"]
                assert len(start_message["_room_roster"]) == 2

                owner_ws.send_json({"type": "actor_assign", "user_id": player_id})
                changed = _receive_until(player_ws, "actor_changed")
                assert changed["user_id"] == player_id
                owner_ws.send_json(
                    {
                        "type": "save_load",
                        "slot_id": "slot_000",
                        "action_id": "owner-load-as-non-actor",
                    }
                )
                _receive_until(owner_ws, "done")
                owner_load = next(
                    item for item in submitted_messages if item["type"] == "save_load"
                )
                assert owner_load["_room_user_id"] == owner_id
                assert owner_load["_room_investigator_id"] == player_claim["id"]
                assert owner_load["_room_actor_user_id"] == player_id
                owner_ws.send_json({"type": "save_create"})
                invalid_control = _receive_until(owner_ws, "room_action_rejected")
                assert invalid_control["code"] == "invalid_action_id"
                owner_ws.send_json({"type": "save_create", "action_id": "owner-save-create"})
                _receive_until(owner_ws, "saved")
                owner_ws.send_json({"type": "save_create", "action_id": "owner-save-create"})
                duplicate_control = _receive_until(owner_ws, "room_action_rejected")
                assert duplicate_control["code"] == "duplicate_action"
                owner_ws.send_json({"type": "load"})
                legacy_load = _receive_until(owner_ws, "room_action_rejected")
                assert legacy_load["code"] == "unsupported_in_room"
                player_ws.send_json(
                    {
                        "type": "action",
                        "action_id": "action-1",
                        "content": "检查门锁",
                        "_room_user_id": owner_id,
                        "_room_investigator_id": owner_claim["id"],
                    }
                )
                _receive_until(player_ws, "gm_turn_start")
                action_message = next(
                    item for item in submitted_messages if item["type"] == "action"
                )
                assert action_message["_room_user_id"] == player_id
                assert action_message["_room_investigator_id"] == player_claim["id"]
                assert action_message["_room_actor_user_id"] == player_id
                # The shared driver must accept the actor at the room boundary;
                # the model itself is intentionally not awaited in this contract test.
                player_ws.send_json(
                    {"type": "action", "action_id": "action-1", "content": "重复提交"}
                )
                duplicate = _receive_until(player_ws, "room_action_rejected")
                assert duplicate["code"] == "duplicate_action"
def test_ws_room_theme_is_sent_once_for_creator_and_joiner(tmp_path: Path):
    """Every /ws/room entry gets exactly one theme frame for the current world.

    The room creator receives the theme from the shared driver's five-message
    join broadcast (mirroring server.run_ws_session); joiners receive it from
    the room bootstrap. Neither path may emit a second initialization frame,
    and no extra theme may be injected by the WebSocket layer itself.
    """
    import server
    from src import multiplayer_ws

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    env = {
        "TRPG_DATABASE_URL": url,
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOW_REGISTRATION": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
        "TRPG_WRITE_COMPAT_EXPORTS": "0",
        "TRPG_ROOM_IDLE_SECONDS": "0",
    }
    origin = {"origin": "https://testserver"}
    manager = RoomManager()

    class FakeEngine:
        def __init__(self, context):
            self.context = context
            self.narrative_model = "test-narrative"
            self.judgement_model = "test-judgement"

        def configure_models(self, narrative, judgement):
            self.narrative_model = narrative
            self.judgement_model = judgement

        def prepare_session(self):
            return None

        def list_saves(self):
            return []

    def engine_factory(*args, **_kwargs):
        return FakeEngine(*args)

    def fake_load_theme(context):
        return {"title": f"theme-{context.module_name}", "colors": {}, "fonts": {}}

    async def fake_room_driver(transport, _engine, *, user_id=None):
        del user_id
        # Mirror server.run_ws_session: the shared room driver broadcasts the
        # five initial frames (module_list/character_list/theme/model_settings/
        # save_list) to the room exactly once, before its message loop.
        await transport.send_json(
            {
                "type": "module_list",
                "modules": [],
                "active": _engine.context.module_name,
                "world_id": _engine.context.world_id,
                "module_name": _engine.context.module_name,
            }
        )
        await transport.send_json({"type": "character_list", "groups": []})
        await transport.send_json(
            {"type": "theme", "theme": fake_load_theme(_engine.context)}
        )
        await transport.send_json({"type": "model_settings"})
        await transport.send_json({"type": "save_list", "saves": _engine.list_saves()})
        try:
            while True:
                await transport.receive_text()
        except RuntimeError:
            return

    def collect_until_save_list(websocket) -> list[dict]:
        messages = []
        for _ in range(60):
            message = websocket.receive_json()
            messages.append(message)
            if message.get("type") == "save_list":
                return messages
        raise AssertionError("did not reach save_list before timeout")

    def theme_frames(messages: list[dict]) -> list[dict]:
        return [message for message in messages if message.get("type") == "theme"]

    with (
        patch.dict(os.environ, env),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        patch.object(server, "GameEngine", side_effect=engine_factory),
        patch.object(server, "run_ws_session", new=fake_room_driver),
        patch.object(server, "_load_theme", side_effect=fake_load_theme),
        patch.object(server, "_list_mods", return_value=[]),
        patch.object(
            server, "_model_settings_payload", return_value={"type": "model_settings"}
        ),
        patch.object(multiplayer_ws, "list_character_options", return_value={"groups": []}),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        client.post(
            "/api/auth/register",
            json={"username": "theme_owner", "password": "owner password 123"},
        )
        owner_cookie = client.cookies.get("trpg_session")
        created = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "name": "主题房"},
            headers=origin,
        )
        world_id = created.json()["world_id"]
        invite = client.post(
            f"/api/worlds/{world_id}/invites",
            json={"max_uses": 1},
            headers=origin,
        ).json()["token"]
        client.post(
            "/api/auth/register",
            json={"username": "theme_player", "password": "player password 123"},
        )
        player_cookie = client.cookies.get("trpg_session")
        client.post(
            "/api/invites/accept",
            json={"token": invite},
            headers=origin,
        )

        with client.websocket_connect(
            f"/ws/room?world_id={world_id}",
            headers={**origin, "cookie": f"trpg_session={owner_cookie}"},
        ) as owner_ws:
            owner_themes = theme_frames(collect_until_save_list(owner_ws))
            assert len(owner_themes) == 1
            assert owner_themes[0]["theme"]["title"] == "theme-mansion_of_madness"
            with client.websocket_connect(
                f"/ws/room?world_id={world_id}",
                headers={**origin, "cookie": f"trpg_session={player_cookie}"},
            ) as player_ws:
                player_themes = theme_frames(collect_until_save_list(player_ws))
                assert len(player_themes) == 1
                assert player_themes[0]["theme"]["title"] == "theme-mansion_of_madness"
