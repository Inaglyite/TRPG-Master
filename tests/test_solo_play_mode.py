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
    AuditEvent,
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
    abandon_solo_world,
    accept_invite,
    archive_world,
    check_solo_abandon_access,
    create_invite,
    finish_room_action,
    reserve_room_action,
    transfer_owner,
    update_member_role,
)
from src.multiplayer_guards import (
    USER_TURN_GUARD,
    reset_action_guards,
)
from src.multiplayer_messages import UNSUPPORTED_ROOM_TYPES, run_room_message_loop
from src.room_runtime import GameRoom, RoomEventHub, RoomManager


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


def test_abandon_solo_world_archives_active_story_without_settling(tmp_path: Path):
    """放弃是单人归档，不是 end_game/settle_case 的替身。"""
    url = sqlite_url(tmp_path)
    owner, _guest = seed_solo_world(url)
    with session_scope(url) as session:
        world = session.get(World, "world-solo")
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "room_status": "playing",
        }

    action_id = "solo-abandon-test"
    reserve_room_action(
        url,
        "world-solo",
        action_id,
        owner.id,
        "solo_abandon",
        required_permission="manage",
    )
    result = abandon_solo_world(
        url,
        "world-solo",
        owner.id,
        reservation_action_id=action_id,
        runtime_room_status="playing",
    )

    assert result == {
        "world_id": "world-solo",
        "status": "archived",
        "abandoned": True,
        "tree_world_ids": ["world-solo"],
    }
    with session_scope(url) as session:
        assert session.get(World, "world-solo").status == "archived"
        action = (
            session.query(RoomAction).filter_by(world_id="world-solo", action_id=action_id).one()
        )
        assert action.status == "completed"
        audit_row = (
            session.query(AuditEvent)
            .filter_by(world_id="world-solo", event_type="world_archived")
            .one()
        )
        assert audit_row.details["archive_reason"] == "solo_abandoned"
        assert audit_row.details["action_id"] == action_id


def test_abandon_solo_world_cancels_running_turn_and_archives_whole_tree(tmp_path: Path):
    """放弃不需要先结束游戏：进行中租约被标 unknown，整棵分支树一起归档。"""
    url = sqlite_url(tmp_path)
    owner, _guest = seed_solo_world(url)
    with session_scope(url) as session:
        # 同一存档位下的一条分支时间线（metadata.branch 指回树根）。
        session.add(
            World(
                id="world-solo-branch",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={
                    "name": "私密单人世界",
                    "room_status": "playing",
                    "max_players": 1,
                    "play_mode": "solo",
                    "branch": {"parent_world_id": "world-solo"},
                },
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-solo-branch",
                user_id=owner.id,
                role="owner",
            )
        )
        # 另一个完全无关的同模组世界不能误伤。
        session.add(
            World(
                id="world-unrelated",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"play_mode": "solo"},
            )
        )
    reserve_room_action(url, "world-solo", "turn-running", owner.id, "action")

    # 从分支世界发起放弃：存档位语义要求整棵树（根 + 分支）一起删除。
    result = abandon_solo_world(
        url,
        "world-solo-branch",
        owner.id,
        runtime_room_status="playing",
    )

    assert result["status"] == "archived"
    assert result["abandoned"] is True
    assert sorted(result["tree_world_ids"]) == ["world-solo", "world-solo-branch"]
    with session_scope(url) as session:
        assert session.get(World, "world-solo").status == "archived"
        assert session.get(World, "world-solo-branch").status == "archived"
        assert session.get(World, "world-unrelated").status == "active"
        orphaned_turn = (
            session.query(RoomAction)
            .filter_by(world_id="world-solo", action_id="turn-running")
            .one()
        )
        assert orphaned_turn.status == "unknown"
        audit_row = (
            session.query(AuditEvent)
            .filter_by(world_id="world-solo-branch", event_type="world_archived")
            .one()
        )
        assert audit_row.details["cancelled_action_ids"] == ["turn-running"]
        assert sorted(audit_row.details["tree_world_ids"]) == [
            "world-solo",
            "world-solo-branch",
        ]

    # 被截断回合的迟到收尾不许把租约翻成 completed（unknown = 不可重试）。
    finish_room_action(url, "world-solo", "turn-running", "completed")
    with session_scope(url) as session:
        assert (
            session.query(RoomAction)
            .filter_by(world_id="world-solo", action_id="turn-running")
            .one()
            .status
            == "unknown"
        )


def test_abandon_solo_world_idempotent_retry_completes_partial_tree_archive(tmp_path: Path):
    """重试已归档世界时，仍要把上次没删干净的活跃分支补归档。"""
    url = sqlite_url(tmp_path)
    owner, _guest = seed_solo_world(url)
    with session_scope(url) as session:
        session.add(
            World(
                id="world-solo-branch",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={
                    "play_mode": "solo",
                    "branch": {"parent_world_id": "world-solo"},
                },
            )
        )
        # 模拟一次只删了树根的历史放弃。
        session.get(World, "world-solo").status = "archived"

    result = abandon_solo_world(url, "world-solo", owner.id)

    assert result["already_archived"] is True
    with session_scope(url) as session:
        assert session.get(World, "world-solo-branch").status == "archived"


def test_abandon_solo_world_refuses_competing_turn_and_multiplayer_world(tmp_path: Path):
    """专用放弃路径不越过回合租约，也永远不作用于多人房间。"""
    url = sqlite_url(tmp_path)
    owner, _guest = seed_solo_world(url)
    reserve_room_action(
        url,
        "world-solo",
        "turn-running",
        owner.id,
        "action",
    )
    with pytest.raises(MultiplayerError) as active:
        reserve_room_action(
            url,
            "world-solo",
            "solo-abandon-competing",
            owner.id,
            "solo_abandon",
            required_permission="manage",
        )
    assert active.value.code == "room_turn_in_progress"
    finish_room_action(url, "world-solo", "turn-running", "completed")

    with session_scope(url) as session:
        world = session.get(World, "world-solo")
        assert world.status == "active"
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "play_mode": "multiplayer",
            "max_players": 4,
        }
    with pytest.raises(MultiplayerError) as wrong_mode:
        check_solo_abandon_access(url, "world-solo", owner.id)
    assert wrong_mode.value.code == "solo_world_required"
    with session_scope(url) as session:
        assert session.get(World, "world-solo").status == "active"


def test_active_solo_abandon_http_is_idempotent_and_keeps_normal_delete_strict(
    tmp_path: Path,
):
    """进行中的单人世界只能经专用 POST 放弃，普通 DELETE 仍必须 409。"""
    url = sqlite_url(tmp_path)
    server, env_patch, db_patch = cloud_client(url)
    Base.metadata.create_all(get_engine(url))
    with env_patch, db_patch, TestClient(server.app, base_url="https://testserver") as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "abandon_owner", "password": "owner password 123"},
            ).status_code
            == 201
        )
        world_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo"},
            headers=ORIGIN,
        ).json()["world_id"]
        with session_scope(url) as session:
            world = session.get(World, world_id)
            world.metadata_json = {
                **dict(world.metadata_json or {}),
                "room_status": "playing",
            }

        strict_delete = client.delete(f"/api/worlds/{world_id}", headers=ORIGIN)
        assert strict_delete.status_code == 409
        assert strict_delete.json()["code"] == "room_active"

        abandoned = client.post(f"/api/worlds/{world_id}/abandon", headers=ORIGIN)
        assert abandoned.status_code == 204
        # 网络重试 / 双击不会把已归档的本人世界变成失败。
        assert client.post(f"/api/worlds/{world_id}/abandon", headers=ORIGIN).status_code == 204

        with session_scope(url) as session:
            world = session.get(World, world_id)
            assert world.status == "archived"
            action = (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_type="solo_abandon")
                .one()
            )
            assert action.status == "completed"


def test_active_solo_abandon_http_cancels_stale_turn_and_rejects_non_solo(tmp_path: Path):
    url = sqlite_url(tmp_path)
    server, env_patch, db_patch = cloud_client(url)
    Base.metadata.create_all(get_engine(url))
    with env_patch, db_patch, TestClient(server.app, base_url="https://testserver") as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "abandon_guard", "password": "owner password 123"},
            ).status_code
            == 201
        )
        solo_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo"},
            headers=ORIGIN,
        ).json()["world_id"]
        with session_scope(url) as session:
            owner_id = session.query(User).filter_by(username="abandon_guard").one().id
        # 房间未加载但持久租约仍在跑（另一进程或崩溃残留）：删除不再需要
        # 玩家先“结束游戏”，放弃直接截断回合并归档。
        reserve_room_action(url, solo_id, "still-running", owner_id, "action")
        abandoned = client.post(f"/api/worlds/{solo_id}/abandon", headers=ORIGIN)
        assert abandoned.status_code == 204
        with session_scope(url) as session:
            assert session.get(World, solo_id).status == "archived"
            stale = (
                session.query(RoomAction)
                .filter_by(world_id=solo_id, action_id="still-running")
                .one()
            )
            assert stale.status == "unknown"

        multiplayer_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness"},
            headers=ORIGIN,
        ).json()["world_id"]
        wrong_mode = client.post(f"/api/worlds/{multiplayer_id}/abandon", headers=ORIGIN)
        assert wrong_mode.status_code == 403
        assert wrong_mode.json()["code"] == "solo_world_required"
        with session_scope(url) as session:
            world = session.get(World, multiplayer_id)
            assert world.status == "active"
            # 已归档的多人世界也不能借“幂等”语义伪装成单人放弃成功。
            world.status = "archived"
        archived_wrong_mode = client.post(f"/api/worlds/{multiplayer_id}/abandon", headers=ORIGIN)
        assert archived_wrong_mode.status_code == 403
        assert archived_wrong_mode.json()["code"] == "solo_world_required"


def test_active_solo_abandon_http_serializes_loaded_room_then_retires_it(
    tmp_path: Path,
):
    """已加载的 playing 单人房也必须先取本地锁，再归档并摘除运行时。"""
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    manager = RoomManager()
    with (
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        owner_id = client.post(
            "/api/auth/register",
            json={"username": "loaded_abandon", "password": "owner password 123"},
        ).json()["id"]
        world_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo"},
            headers=ORIGIN,
        ).json()["world_id"]
        room = GameRoom(
            world_id,
            SimpleNamespace(),
            RoomEventHub(world_id),
            owner_id,
            current_actor_user_id=owner_id,
            status="playing",
            play_mode="solo",
        )

        async def install_room():
            installed, created = await manager.get_or_create(world_id, lambda: room)
            assert created is True
            assert installed is room

        asyncio.run(install_room())
        response = client.post(f"/api/worlds/{world_id}/abandon", headers=ORIGIN)
        assert response.status_code == 204
        assert room.action_active is False
        assert asyncio.run(manager.get(world_id)) is None
        with session_scope(url) as session:
            assert session.get(World, world_id).status == "archived"
            action = (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_type="solo_abandon")
                .one()
            )
            assert action.status == "completed"


def test_active_solo_abandon_http_cancels_busy_room_turn(tmp_path: Path):
    """回合进行中（本地锁 + 持久租约都占着）也能放弃：归档、断连、租约 unknown。"""
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    manager = RoomManager()
    with (
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        owner_id = client.post(
            "/api/auth/register",
            json={"username": "busy_abandon", "password": "owner password 123"},
        ).json()["id"]
        world_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo"},
            headers=ORIGIN,
        ).json()["world_id"]
        room = GameRoom(
            world_id,
            SimpleNamespace(),
            RoomEventHub(world_id),
            owner_id,
            current_actor_user_id=owner_id,
            status="playing",
            play_mode="solo",
        )
        # 回合线程会晚到收尾：这里只验证放弃路径本身不被它阻塞。

        class _Socket:
            def __init__(self):
                self.sent: list[dict] = []
                self.closed: tuple[int, str] | None = None

            async def send_json(self, payload):
                self.sent.append(dict(payload))

            async def close(self, *, code: int, reason: str):
                self.closed = (code, reason)

        socket = _Socket()

        async def install_busy_room():
            installed, created = await manager.get_or_create(world_id, lambda: room)
            assert created is True
            assert installed is room
            # 进行中回合：本地行动锁被占用。
            await room.reserve_control(owner_id, "turn-in-flight")
            from src.room_runtime import RoomConnection

            await room.hub.attach(RoomConnection("conn-owner", owner_id, "owner", socket))

        asyncio.run(install_busy_room())
        reserve_room_action(url, world_id, "turn-in-flight", owner_id, "action")

        response = client.post(f"/api/worlds/{world_id}/abandon", headers=ORIGIN)

        assert response.status_code == 204
        assert asyncio.run(manager.get(world_id)) is None
        assert any(payload.get("type") == "room_deleted" for payload in socket.sent)
        assert socket.closed is not None and socket.closed[0] == 4404
        with session_scope(url) as session:
            assert session.get(World, world_id).status == "archived"
            orphaned = (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_id="turn-in-flight")
                .one()
            )
            assert orphaned.status == "unknown"
            # 强制路径不登记 solo_abandon 租约（锁被回合占着，登记会自撞）。
            assert (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_type="solo_abandon")
                .first()
                is None
            )


def test_active_solo_abandon_releases_leases_after_unexpected_failure(tmp_path: Path):
    """数据库/运行时意外错误不能把已加载房间永久锁在“处理中”。"""
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    manager = RoomManager()
    with (
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
        patch.object(server, "ROOM_MANAGER", manager),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        owner_id = client.post(
            "/api/auth/register",
            json={"username": "abandon_cleanup", "password": "owner password 123"},
        ).json()["id"]
        world_id = client.post(
            "/api/worlds",
            json={"module": "mansion_of_madness", "play_mode": "solo"},
            headers=ORIGIN,
        ).json()["world_id"]
        room = GameRoom(
            world_id,
            SimpleNamespace(),
            RoomEventHub(world_id),
            owner_id,
            current_actor_user_id=owner_id,
            status="playing",
            play_mode="solo",
        )

        async def install_room():
            await manager.get_or_create(world_id, lambda: room)

        asyncio.run(install_room())
        with patch(
            "src.multiplayer_archive_http.abandon_solo_world",
            side_effect=RuntimeError("database temporarily unavailable"),
        ):
            failed = client.post(f"/api/worlds/{world_id}/abandon", headers=ORIGIN)
        assert failed.status_code == 503
        assert failed.json()["code"] == "abandon_unavailable"
        assert room.action_active is False
        with session_scope(url) as session:
            assert session.get(World, world_id).status == "active"
            action = (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_type="solo_abandon")
                .one()
            )
            assert action.status == "failed"


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
        {"type": "action", "action_id": f"burst-{index}", "content": "查看"} for index in range(3)
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
        {"type": "action", "action_id": f"quota-{index}", "content": "查看"} for index in range(2)
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
