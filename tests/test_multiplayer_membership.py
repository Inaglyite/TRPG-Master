from __future__ import annotations

import hashlib
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
    release_investigator,
    remove_member,
    room_members,
    update_member_role,
)


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
                claimed = player_client.post(
                    f"/api/worlds/{world_id}/investigators/claim",
                    json={"character_key": "detective-huang"},
                    headers=headers,
                )
                assert claimed.status_code == 200

            members = owner_client.get(f"/api/worlds/{world_id}/members")
            assert members.status_code == 200
            assert members.json()["metadata"]["name"] == "周五调查团"
            assert len(members.json()["members"]) == 2
            assert any(row["investigator"] for row in members.json()["members"])
