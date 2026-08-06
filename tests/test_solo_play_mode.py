"""Contracts for explicit play_mode worlds (cloud-private solo) and spend guards."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.auth import create_user
from src.database import (
    Base,
    RoomAction,
    User,
    World,
    WorldInvestigator,
    WorldInvite,
    WorldMember,
    get_engine,
    new_id,
    session_scope,
    utcnow,
)
from src.multiplayer import (
    MultiplayerError,
    accept_invite,
    archive_world,
    create_invite,
    transfer_owner,
    update_member_role,
)
from src.multiplayer_guards import (
    USER_TURN_GUARD,
    reset_action_guards,
)
from src.multiplayer_messages import UNSUPPORTED_ROOM_TYPES, run_room_message_loop
from src.room_runtime import GameRoom, RoomEventHub


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'solo.db'}"


@pytest.fixture(autouse=True)
def _reset_spend_guards():
    yield
    reset_action_guards()


def seed_solo_world(url: str, *, with_second_member: bool = False):
    """One active solo world owned by a fresh account (no play_mode invite path)."""
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "solo_owner", "owner password 123")
    guest = create_user(url, "solo_guest", "guest password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-solo",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={
                    "name": "私密单人世界",
                    "room_status": "lobby",
                    "max_players": 1,
                    "play_mode": "solo",
                },
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-solo",
                user_id=owner.id,
                role="owner",
            )
        )
        if with_second_member:
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id="world-solo",
                    user_id=guest.id,
                    role="player",
                )
            )
    return owner, guest


CLOUD_ENV = {
    "TRPG_REQUIRE_AUTH": "1",
    "TRPG_ALLOW_REGISTRATION": "1",
    "TRPG_ALLOWED_ORIGINS": "https://testserver",
    "TRPG_WRITE_COMPAT_EXPORTS": "0",
    "TRPG_ROOM_IDLE_SECONDS": "0",
}
ORIGIN = {"origin": "https://testserver"}


def cloud_client(url: str):
    """server.app TestClient bound to a temporary control-plane database."""
    import server

    return (
        server,
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
    )


def test_solo_world_creation_persists_play_mode_and_caps_members(tmp_path: Path):
    url = sqlite_url(tmp_path)
    server, env_patch, db_patch = cloud_client(url)
    Base.metadata.create_all(get_engine(url))
    with env_patch, db_patch, TestClient(server.app, base_url="https://testserver") as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "solo_creator", "password": "owner password 123"},
            ).status_code
            == 201
        )
        created = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "name": "云端单人", "play_mode": "solo"},
            headers=ORIGIN,
        )
        assert created.status_code == 201
        world_id = created.json()["world_id"]
        with session_scope(url) as session:
            metadata = session.get(World, world_id).metadata_json
            assert metadata["play_mode"] == "solo"
            assert metadata["max_players"] == 1

        listed = client.get("/api/worlds", headers=ORIGIN).json()["worlds"]
        solo_entry = next(item for item in listed if item["world_id"] == world_id)
        assert solo_entry["play_mode"] == "solo"
        assert solo_entry["max_players"] == 1

        # 缺省 play_mode 仍为多人世界
        multi = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "name": "普通房间"},
            headers=ORIGIN,
        )
        assert multi.status_code == 201
        multi_id = multi.json()["world_id"]
        listed = client.get("/api/worlds", headers=ORIGIN).json()["worlds"]
        multi_entry = next(item for item in listed if item["world_id"] == multi_id)
        assert multi_entry["play_mode"] == "multiplayer"
        assert multi_entry["max_players"] == 4

        # 无 play_mode 字段的旧世界在列表中默认 multiplayer
        with session_scope(url) as session:
            legacy = World(
                id="world-legacy",
                module_name="mansion_of_madness",
                created_by=session.query(User).filter_by(username="solo_creator").one().id,
                metadata_json={"name": "旧世界", "room_status": "lobby"},
            )
            session.add(legacy)
            session.flush()
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id="world-legacy",
                    user_id=legacy.created_by,
                    role="owner",
                )
            )
        listed = client.get("/api/worlds", headers=ORIGIN).json()["worlds"]
        legacy_entry = next(item for item in listed if item["world_id"] == "world-legacy")
        assert legacy_entry["play_mode"] == "multiplayer"


def test_world_creation_rejects_invalid_play_mode(tmp_path: Path):
    server, env_patch, db_patch = cloud_client(sqlite_url(tmp_path))
    Base.metadata.create_all(get_engine(sqlite_url(tmp_path)))
    with env_patch, db_patch, TestClient(server.app, base_url="https://testserver") as client:
        client.post(
            "/api/auth/register",
            json={"username": "mode_checker", "password": "owner password 123"},
        )
        for payload in (
            {"play_mode": "weird"},
            {"play_mode": "solo", "max_players": 4},
            {"play_mode": "solo", "max_players": 2},
            {"play_mode": "solo", "max_players": "many"},
        ):
            rejected = client.post(
                "/api/worlds",
                json={"module": "mansion_of_madness", **payload},
                headers=ORIGIN,
            )
            assert rejected.status_code == 400, payload
            assert rejected.json()["code"] == "invalid_play_mode"

        accepted = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo", "max_players": 1},
            headers=ORIGIN,
        )
        assert accepted.status_code == 201


def test_solo_world_blocks_invites_and_membership_operations(tmp_path: Path):
    url = sqlite_url(tmp_path)
    owner, guest = seed_solo_world(url, with_second_member=True)

    with pytest.raises(MultiplayerError) as create_denied:
        create_invite(url, "world-solo", owner.id)
    assert create_denied.value.code == "solo_world"
    assert create_denied.value.status_code == 403

    # 历史上已存在的邀请（防御）：accept 同样被拒
    import hashlib as _hashlib
    import secrets as _secrets
    from datetime import timedelta as _timedelta

    latecomer = create_user(url, "solo_latecomer", "late password 123")
    token = _secrets.token_urlsafe(24)
    with session_scope(url) as session:
        session.add(
            WorldInvite(
                id=new_id("invite"),
                world_id="world-solo",
                invited_by=owner.id,
                token_hash=_hashlib.sha256(token.encode()).hexdigest(),
                role="player",
                expires_at=utcnow() + _timedelta(hours=24),
                max_uses=1,
                used_count=0,
                created_at=utcnow(),
            )
        )
    with pytest.raises(MultiplayerError) as accept_denied:
        accept_invite(url, token, latecomer.id)
    assert accept_denied.value.code == "solo_world"
    assert accept_denied.value.status_code == 403

    # 成员改角色与房主移交（防御性）同样 403 solo_world
    with pytest.raises(MultiplayerError) as role_denied:
        update_member_role(url, "world-solo", guest.id, owner.id, "viewer")
    assert role_denied.value.code == "solo_world"
    with pytest.raises(MultiplayerError) as transfer_denied:
        transfer_owner(url, "world-solo", guest.id, owner.id)
    assert transfer_denied.value.code == "solo_world"

    # 归档删除保持允许
    archived = archive_world(url, "world-solo", owner.id)
    assert archived["status"] == "archived"


def test_solo_world_http_invite_blocked_and_archive_allowed(tmp_path: Path):
    server, env_patch, db_patch = cloud_client(sqlite_url(tmp_path))
    Base.metadata.create_all(get_engine(sqlite_url(tmp_path)))
    with env_patch, db_patch, TestClient(server.app, base_url="https://testserver") as client:
        client.post(
            "/api/auth/register",
            json={"username": "solo_http_owner", "password": "owner password 123"},
        )
        world_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo"},
            headers=ORIGIN,
        ).json()["world_id"]

        invite = client.post(f"/api/worlds/{world_id}/invites", json={}, headers=ORIGIN)
        assert invite.status_code == 403
        assert invite.json()["code"] == "solo_world"

        deleted = client.delete(f"/api/worlds/{world_id}", headers=ORIGIN)
        assert deleted.status_code == 204


class _QueueSocket:
    """Feed prepared messages, then abort the loop like a disconnect."""

    def __init__(self, messages: list[dict], room: GameRoom | None = None):
        self._messages = list(messages)
        self._room = room
        self.sent: list[dict] = []

    async def receive_text(self):
        if not self._messages:
            raise RuntimeError("test complete")
        # Simulate the previous turn reaching its terminal state before the
        # next client message arrives (frees room lock + in-flight marker).
        if self._room is not None:
            self._room.release_action(terminal_status="completed")
        return json.dumps(self._messages.pop(0))

    async def send_json(self, payload):
        self.sent.append(dict(payload))


class _Driver:
    def __init__(self):
        self.submitted: list[dict] = []

    async def submit(self, payload):
        self.submitted.append(json.loads(payload))


def _guard_controller(roster: list[dict], members: set[str]):
    return SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: "sqlite://"),
        room_roster=lambda _world_id: (list(roster), set(members)),
    )


def test_solo_start_skips_ready_gates_and_auto_claims_investigator(tmp_path: Path):
    """solo 世界：无 ready 无 claim 也能 start，自动认领，行动者=owner。"""
    import server

    url = sqlite_url(tmp_path)
    owner, _guest = seed_solo_world(url)
    ref = {"source": "module", "id": "solo-detective"}
    options = {"groups": [{"characters": [{"id": "solo-detective", "ref": ref}]}]}

    room = GameRoom(
        "world-solo",
        SimpleNamespace(context=SimpleNamespace(module_name="mansion_of_madness")),
        RoomEventHub("world-solo"),
        owner.id,
        current_actor_user_id=owner.id,
        status="lobby",
        play_mode="solo",
        ready_users=set(),  # 无人 ready
        connected_users={owner.id: 1},
    )
    driver = _Driver()
    room.driver_transport = driver
    socket = _QueueSocket([{"type": "start", "action_id": "solo-start-1"}])

    async def scenario():
        with pytest.raises(RuntimeError, match="test complete"):
            await run_room_message_loop(
                server.MULTIPLAYER_WS,
                socket,
                room,
                SimpleNamespace(id=owner.id),
                room.world_id,
                "owner-tab",
                "owner",
            )

    with (
        patch.object(server, "DATABASE_URL", url),
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
        patch("src.multiplayer_messages.list_character_options", return_value=options),
    ):
        asyncio.run(scenario())

    assert len(driver.submitted) == 1
    start_message = driver.submitted[0]
    assert start_message["type"] == "start"
    assert start_message["_room_actor_user_id"] == owner.id
    assert start_message["character_ref"] == ref
    assert len(start_message["_room_roster"]) == 1
    assert start_message["_room_roster"][0]["user_id"] == owner.id
    assert room.status == "starting"

    with session_scope(url) as session:
        claim = (
            session.query(WorldInvestigator)
            .filter_by(world_id="world-solo", controller_user_id=owner.id)
            .one()
        )
        assert claim.character_key == "solo-detective"
        assert claim.status == "claimed"
        assert claim.character_ref == ref
        assert session.get(World, "world-solo").metadata_json["room_status"] == "starting"
        action = (
            session.query(RoomAction)
            .filter_by(world_id="world-solo", action_id="solo-start-1")
            .one()
        )
        assert action.status == "running"


def test_multiplayer_world_start_keeps_ready_and_claim_gates(tmp_path: Path):
    """多人房间回归：缺 claim/ready/online 时 start 仍被 room_not_ready 拒绝。"""
    roster = [
        {
            "investigator_id": "inv-owner",
            "user_id": "owner",
            "character_ref": {"type": "inline", "data": {"name": "Owner"}},
        }
    ]
    room = GameRoom(
        "world-multi",
        SimpleNamespace(),
        RoomEventHub("world-multi"),
        "owner",
        current_actor_user_id="owner",
        status="lobby",
        play_mode="multiplayer",
        ready_users=set(),
        connected_users={"owner": 1},
    )
    driver = _Driver()
    room.driver_transport = driver
    socket = _QueueSocket([{"type": "start", "action_id": "multi-start-1"}])

    async def scenario():
        with pytest.raises(RuntimeError, match="test complete"):
            await run_room_message_loop(
                _guard_controller(roster, {"owner", "player"}),
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
    ):
        asyncio.run(scenario())

    rejection = next(m for m in socket.sent if m["type"] == "room_action_rejected")
    assert rejection["code"] == "room_not_ready"
    assert rejection["missing_claim_user_ids"] == ["player"]
    assert rejection["missing_ready_user_ids"] == ["owner", "player"]
    assert rejection["missing_online_user_ids"] == ["player"]
    assert driver.submitted == []
    assert room.status == "lobby"


def test_action_in_progress_rejects_second_turn_across_worlds():
    """同一账号在别的世界已有生成中回合时，新 action 被拒 action_in_progress。"""
    roster = [
        {
            "investigator_id": "inv-owner",
            "user_id": "owner",
            "character_ref": {"type": "inline", "data": {"name": "Owner"}},
        }
    ]
    room = GameRoom(
        "world-guarded",
        SimpleNamespace(),
        RoomEventHub("world-guarded"),
        "owner",
        current_actor_user_id="owner",
        status="playing",
        connected_users={"owner": 1},
    )
    driver = _Driver()
    room.driver_transport = driver
    socket = _QueueSocket([{"type": "action", "action_id": "act-1", "content": "查看"}])
    USER_TURN_GUARD.acquire("owner", "other-world", "other-action")

    async def scenario():
        with pytest.raises(RuntimeError, match="test complete"):
            await run_room_message_loop(
                _guard_controller(roster, {"owner"}),
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
    ):
        asyncio.run(scenario())

    rejection = next(m for m in socket.sent if m["type"] == "room_action_rejected")
    assert rejection["code"] == "action_in_progress"
    assert driver.submitted == []


def test_action_rate_limit_rejects_burst(monkeypatch: pytest.MonkeyPatch):
    """超过 TRPG_ACTION_RATE_PER_MINUTE 的行动被拒 rate_limited。"""
    monkeypatch.setenv("TRPG_ACTION_RATE_PER_MINUTE", "2")
    roster = [
        {
            "investigator_id": "inv-owner",
            "user_id": "owner",
            "character_ref": {"type": "inline", "data": {"name": "Owner"}},
        }
    ]
    room = GameRoom(
        "world-rate",
        SimpleNamespace(),
        RoomEventHub("world-rate"),
        "owner",
        current_actor_user_id="owner",
        status="playing",
        connected_users={"owner": 1},
    )
    driver = _Driver()
    room.driver_transport = driver
    room.action_status_callback = lambda world_id, action_id, _status: (
        USER_TURN_GUARD.release_action(world_id, action_id)
    )
    messages = [
        {"type": "action", "action_id": f"burst-{index}", "content": "查看"}
        for index in range(3)
    ]
    socket = _QueueSocket(messages, room=room)

    async def scenario():
        with pytest.raises(RuntimeError, match="test complete"):
            await run_room_message_loop(
                _guard_controller(roster, {"owner"}),
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
        patch("src.multiplayer_messages.reserve_room_action"),
    ):
        asyncio.run(scenario())

    assert [item["action_id"] for item in driver.submitted] == ["burst-0", "burst-1"]
    rejection = next(m for m in socket.sent if m["type"] == "room_action_rejected")
    assert rejection["code"] == "rate_limited"


def test_daily_turn_quota_rejects_when_exhausted(monkeypatch: pytest.MonkeyPatch):
    """超过 TRPG_DAILY_TURN_QUOTA 的行动被拒 daily_quota_exceeded。"""
    monkeypatch.setenv("TRPG_ACTION_RATE_PER_MINUTE", "10")
    monkeypatch.setenv("TRPG_DAILY_TURN_QUOTA", "1")
    roster = [
        {
            "investigator_id": "inv-owner",
            "user_id": "owner",
            "character_ref": {"type": "inline", "data": {"name": "Owner"}},
        }
    ]
    room = GameRoom(
        "world-quota",
        SimpleNamespace(),
        RoomEventHub("world-quota"),
        "owner",
        current_actor_user_id="owner",
        status="playing",
        connected_users={"owner": 1},
    )
    driver = _Driver()
    room.driver_transport = driver
    room.action_status_callback = lambda world_id, action_id, _status: (
        USER_TURN_GUARD.release_action(world_id, action_id)
    )
    messages = [
        {"type": "action", "action_id": f"quota-{index}", "content": "查看"}
        for index in range(2)
    ]
    socket = _QueueSocket(messages, room=room)

    async def scenario():
        with pytest.raises(RuntimeError, match="test complete"):
            await run_room_message_loop(
                _guard_controller(roster, {"owner"}),
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value="owner"),
        patch("src.multiplayer_messages.reserve_room_action"),
    ):
        asyncio.run(scenario())

    assert [item["action_id"] for item in driver.submitted] == ["quota-0"]
    rejection = next(m for m in socket.sent if m["type"] == "room_action_rejected")
    assert rejection["code"] == "daily_quota_exceeded"


def test_cloud_mode_disables_legacy_ws_and_exposes_no_model_settings_http(tmp_path: Path):
    """账号模式下 /ws 单人通道关闭，且无任何 HTTP 路由可改模型设置。"""
    server, env_patch, db_patch = cloud_client(sqlite_url(tmp_path))
    Base.metadata.create_all(get_engine(sqlite_url(tmp_path)))
    with env_patch, db_patch, TestClient(server.app, base_url="https://testserver") as client:
        client.post(
            "/api/auth/register",
            json={"username": "ws_guard_user", "password": "owner password 123"},
        )
        cookie = client.cookies.get("trpg_session")
        assert cookie
        with pytest.raises(WebSocketDisconnect) as disconnected:
            with client.websocket_connect(
                "/ws",
                headers={**ORIGIN, "cookie": f"trpg_session={cookie}"},
            ) as ws:
                ws.receive_json()
        assert disconnected.value.code == 4409

    http_paths = {route.path for route in server.app.routes if isinstance(route, APIRoute)}
    assert all("model_settings" not in path for path in http_paths)
    # 房间通道内模型设置更新同样是禁用消息类型
    assert "model_settings_update" in UNSUPPORTED_ROOM_TYPES
