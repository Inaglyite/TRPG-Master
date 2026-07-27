from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.auth import create_user
from src.database import (
    Base,
    RoomAction,
    World,
    WorldInvestigator,
    WorldInvite,
    WorldMember,
    get_engine,
    new_id,
    session_scope,
)
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
    PlayerNotesStore(tmp_path, user_id="user-alice").save("Alice 私人笔记", expected_revision=0)
    PlayerNotesStore(tmp_path, user_id="user-bob").save("Bob 私人笔记", expected_revision=0)

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
        await transport.send_json(
            {
                "type": "decision_request",
                "id": "decision-7",
                "prompt": "选择处理方式",
                "options": [{"id": "wait", "label": "等待"}],
            }
        )
        alice = await server.MULTIPLAYER_WS.room_full_recovery_payload(
            room, "user-alice"
        )
        bob = await server.MULTIPLAYER_WS.room_full_recovery_payload(room, "user-bob")
        socket = CaptureSocket()
        await server.MULTIPLAYER_WS.send_room_full_recovery(
            socket, room, "user-alice"
        )
        return alice, bob, socket.messages

    alice, bob, messages = asyncio.run(scenario())

    assert alice["pending_reply"]["type"] == "decision_request"
    assert alice["pending_reply"]["id"] == "decision-7"
    assert alice["pending_reply"]["recovered"] is True
    assert bob["pending_reply"] is None
    assert [message["type"] for message in messages] == [
        "room_full_state",
        "decision_request",
    ]
    assert messages[1]["id"] == "decision-7"
    assert messages[1]["recovered"] is True
    assert "room_event_id" not in messages[1]


def test_continue_with_slot_is_owner_control_but_plain_continue_is_actor_control():
    assert owner_turn_required("continue", {"slot_id": "slot_001"}) is True
    assert owner_turn_required("continue", {"slot_id": "   "}) is False
    assert owner_turn_required("continue", {}) is False
    assert owner_turn_required("save_load", {"slot_id": "slot_001"}) is True


def test_current_actor_member_mutation_is_serialized_with_turn_and_prompt(
    tmp_path: Path,
):
    import server

    room = GameRoom(
        "world-guard",
        SimpleNamespace(),
        RoomEventHub("world-guard"),
        "user-owner",
        current_actor_user_id="user-actor",
    )
    manager = RoomManager()
    actor_request = SimpleNamespace(
        method="PATCH",
        url=SimpleNamespace(path="/api/worlds/world-guard/members/user-actor"),
    )
    other_request = SimpleNamespace(
        method="DELETE",
        url=SimpleNamespace(path="/api/worlds/world-guard/members/user-other"),
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
            active_lease, active_rejection = (
                await server._reserve_current_actor_member_mutation(actor_request)
            )
            room.release_action()

            room.assign_actor("user-owner")
            await room.reserve_action(
                "user-owner",
                "owner-turn",
                require_current_actor=False,
            )
            owner_lease, owner_rejection = (
                await server._reserve_current_actor_member_mutation(
                    owner_transfer_request
                )
            )
            room.release_action()
            room.assign_actor("user-actor")

            room.set_pending_reply("decision", "user-actor", request_id="decision-1")
            pending_lease, pending_rejection = (
                await server._reserve_current_actor_member_mutation(actor_request)
            )
            room.clear_pending_reply()

            idle_lease, idle_rejection = (
                await server._reserve_current_actor_member_mutation(actor_request)
            )
            idle_locked = room.action_active
            if idle_lease is not None:
                idle_lease.release_action()

            other_lease, other_rejection = (
                await server._reserve_current_actor_member_mutation(other_request)
            )
        return (
            active_lease,
            active_rejection,
            owner_lease,
            owner_rejection,
            pending_lease,
            pending_rejection,
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
    assert idle_lease is room
    assert idle_rejection is None
    assert idle_locked is True
    assert other_lease is None
    assert other_rejection is None


def test_cloud_mode_disables_direct_module_asset_paths():
    import server

    with patch.object(server, "auth_required", return_value=True):
        response = asyncio.run(
            server.serve_asset("猩红文档", "莱特教授的尸体.png")
        )

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
            await transport.submit(
                json.dumps({"type": "start", "character_ref": {}})
            )
            failure = await capture.wait_for("error")
            first_status = room.status
            first_action_active = room.action_active

            room.status = "starting"
            await room.reserve_action(
                "user-owner",
                "start-retry",
                require_current_actor=False,
            )
            room.control_action_active = True
            await transport.submit(
                json.dumps({"type": "start", "character_ref": {}})
            )
            model_failure = await capture.wait_for("error")
            await capture.wait_for("done")
            model_failure_status = room.status
            model_failure_action_active = room.action_active

            room.status = "starting"
            await room.reserve_action(
                "user-owner",
                "start-after-model-failure",
                require_current_actor=False,
            )
            room.control_action_active = True
            await transport.submit(
                json.dumps({"type": "start", "character_ref": {}})
            )
            await capture.wait_for("done")
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
                    f"/api/invites/{invite.json()['token']}/accept", headers=headers
                )
                assert joined.status_code == 200
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
            f"/api/invites/{invite}/accept",
            headers=origin,
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

                player_ws.send_json(
                    {
                        "type": "player_notes_update",
                        "revision": 0,
                        "text": "只属于玩家的秘密笔记",
                    }
                )
                player_note = _receive_until(player_ws, "player_notes")
                assert player_note["text"] == "只属于玩家的秘密笔记"
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
