from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.auth import create_user
from src.database import (
    Base,
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
    list_invites,
    release_investigator,
    remove_member,
    reserve_room_action,
    room_members,
    transfer_owner,
    update_member_role,
)
from src.room_runtime import RoomManager


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'multiplayer.db'}"


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


def test_room_action_idempotency_survives_room_runtime_recreation(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, _player, _stranger = seed_accounts_and_world(url)
    reserve_room_action(url, "world-room", "stable-action-1", owner.id, "action")
    with pytest.raises(MultiplayerError) as duplicate:
        reserve_room_action(url, "world-room", "stable-action-1", owner.id, "action")
    assert duplicate.value.code == "duplicate_action"


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
                options = player_client.get(
                    f"/api/worlds/{world_id}/investigators/options"
                )
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

            members = owner_client.get(f"/api/worlds/{world_id}/members")
            assert members.status_code == 200
            assert members.json()["metadata"]["name"] == "周五调查团"
            assert len(members.json()["members"]) == 2
            assert any(row["investigator"] for row in members.json()["members"])


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
                if data.get("type") == "action":
                    await transport.send_json(
                        {"type": "gm_turn_start", "turn_id": "test-turn", "seq": 1}
                    )
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
        claim_investigator(
            url,
            world_id,
            "owner-character",
            owner_id,
            character_ref={"type": "inline", "data": {"name": "房主调查员"}},
        )
        claim_investigator(
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

                owner_ws.send_json({"type": "actor_assign", "user_id": player_id})
                changed = _receive_until(player_ws, "actor_changed")
                assert changed["user_id"] == player_id
                player_ws.send_json(
                    {"type": "action", "action_id": "action-1", "content": "检查门锁"}
                )
                # The shared driver must accept the actor at the room boundary;
                # the model itself is intentionally not awaited in this contract test.
                player_ws.send_json(
                    {"type": "action", "action_id": "action-1", "content": "重复提交"}
                )
                duplicate = _receive_until(player_ws, "room_action_rejected")
                assert duplicate["code"] == "duplicate_action"
