"""云端单人大厅时间线 HTTP 控制面（不进房间管理）的契约测试。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.database import (
    Base,
    RoomAction,
    SaveSlot,
    Snapshot,
    Turn,
    User,
    World,
    WorldInvestigator,
    WorldMember,
    WorldState,
    get_engine,
    new_id,
    session_scope,
)
from src.multiplayer import finish_room_action
from src.room_runtime import GameRoom, RoomEventHub
from src.solo_timeline_ws import POINTER_KEY

CLOUD_ENV = {
    "TRPG_REQUIRE_AUTH": "1",
    "TRPG_ALLOW_REGISTRATION": "1",
    "TRPG_ALLOWED_ORIGINS": "https://testserver",
    "TRPG_WRITE_COMPAT_EXPORTS": "0",
    "TRPG_ROOM_IDLE_SECONDS": "0",
}
ORIGIN = {"origin": "https://testserver"}


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'solo-timeline-http.db'}"


def cloud_client(url: str):
    import server

    return (
        server,
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
    )


def _register(client, username: str) -> str:
    assert (
        client.post(
            "/api/auth/register",
            json={"username": username, "password": "timeline password 123"},
        ).status_code
        == 201
    )
    return username


def _user_id(url: str, username: str) -> str:
    with session_scope(url) as session:
        return session.query(User).filter_by(username=username).one().id


def seed_solo_tree(url: str, owner_id: str, *, pointer: str = "world-root") -> None:
    """root（playing，带指针）+ branch（resumable），claim 在 root 上。"""
    with session_scope(url) as session:
        session.add(
            World(
                id="world-root",
                module_name="mansion_of_madness",
                created_by=owner_id,
                metadata_json={
                    "name": "云端单人",
                    "room_status": "playing",
                    "max_players": 1,
                    "play_mode": "solo",
                    POINTER_KEY: pointer,
                },
            )
        )
        session.add(
            WorldState(
                world_id="world-root",
                schema_version=1,
                state={
                    "current_scene": {"name": "入口大厅"},
                    "pc": {"name": "霍华德"},
                },
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"), world_id="world-root", user_id=owner_id, role="owner"
            )
        )
        session.add(
            WorldInvestigator(
                id="inv-root",
                world_id="world-root",
                character_key="howard",
                character_ref={"source": "module", "id": "howard"},
                controller_user_id=owner_id,
                status="claimed",
            )
        )
        session.add(
            World(
                id="world-branch",
                module_name="mansion_of_madness",
                created_by=owner_id,
                metadata_json={
                    "display_name": "分支A",
                    "play_mode": "solo",
                    "max_players": 1,
                    "room_status": "playing",
                    "branch": {
                        "parent_world_id": "world-root",
                        "source_turn_id": "turn-1",
                        "created_at": "2026-08-17T00:00:00+00:00",
                    },
                },
            )
        )
        session.add(WorldState(world_id="world-branch", schema_version=1, state={}))
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-branch",
                user_id=owner_id,
                role="owner",
            )
        )
        # 显式 flush：本环境的 UOW 排序会把 Snapshot 排到 World 前面导致 FK
        # 失败，先把 worlds 及相关行落库再加快照/存档槽。
        session.flush()
        # 两条时间线都可恢复（SaveSlot 需要 Snapshot 外键）
        for world_id in ("world-root", "world-branch"):
            session.add(
                Snapshot(
                    id=f"snap-{world_id}",
                    world_id=world_id,
                    revision=1,
                    state={},
                )
            )
        session.flush()
        for world_id in ("world-root", "world-branch"):
            session.add(
                SaveSlot(
                    id=f"save-{world_id}",
                    world_id=world_id,
                    slot_key="slot_000",
                    kind="auto",
                    snapshot_id=f"snap-{world_id}",
                    metadata_json={"created_at": "2026-08-17T00:00:00+00:00"},
                    messages=[],
                )
            )


@pytest.fixture()
def client(tmp_path: Path):
    url = sqlite_url(tmp_path)
    server, env_patch, db_patch = cloud_client(url)
    Base.metadata.create_all(get_engine(url))
    with env_patch, db_patch, TestClient(
        server.app, base_url="https://testserver"
    ) as test_client:
        yield server, url, test_client


def test_list_timelines_returns_tree_and_pointer(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"), pointer="world-branch")

    response = http.get("/api/worlds/world-root/timelines", headers=ORIGIN)
    assert response.status_code == 200
    payload = response.json()
    assert payload["root_world_id"] == "world-root"
    assert payload["active_world_id"] == "world-branch"
    ids = {entry["world_id"] for entry in payload["worlds"]}
    assert ids == {"world-root", "world-branch"}
    branch = next(e for e in payload["worlds"] if e["world_id"] == "world-branch")
    assert branch["is_branch"] is True
    assert branch["resumable"] is True


def test_timelines_require_auth(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"))
    http.post("/api/auth/logout", headers=ORIGIN)
    assert http.get("/api/worlds/world-root/timelines", headers=ORIGIN).status_code == 401


def test_timelines_reject_multiplayer_world(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    owner_id = _user_id(url, "tl_http_owner")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-multi",
                module_name="mansion_of_madness",
                created_by=owner_id,
                metadata_json={"name": "多人", "room_status": "lobby"},
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"), world_id="world-multi", user_id=owner_id, role="owner"
            )
        )
    response = http.get("/api/worlds/world-multi/timelines", headers=ORIGIN)
    assert response.status_code == 403
    assert response.json()["code"] == "solo_world_required"


def test_timelines_reject_non_owner(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"))
    # 换登录另一个账号：不是成员即非房主
    _register(http, "tl_http_guest")
    response = http.get("/api/worlds/world-root/timelines", headers=ORIGIN)
    assert response.status_code == 403
    assert response.json()["code"] == "owner_required"


def test_switch_from_lobby_moves_pointer_and_claims(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"), pointer="world-root")

    response = http.post(
        "/api/worlds/world-root/timelines/switch",
        json={"target_world_id": "world-branch"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert response.json()["active_world_id"] == "world-branch"
    with session_scope(url) as session:
        root = session.get(World, "world-root")
        assert root.metadata_json[POINTER_KEY] == "world-branch"
        assert session.get(WorldInvestigator, "inv-root").world_id == "world-branch"
        action = (
            session.query(RoomAction)
            .filter_by(world_id="world-root", action_type="solo_world_switch")
            .one()
        )
        assert action.status == "completed"
    # 大厅列表的续玩目标随指针更新
    listed = http.get("/api/worlds", headers=ORIGIN).json()["worlds"]
    root_entry = next(item for item in listed if item["world_id"] == "world-root")
    assert root_entry["resume_world_id"] == "world-branch"


def test_switch_is_idempotent_for_current_timeline(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"), pointer="world-root")
    response = http.post(
        "/api/worlds/world-root/timelines/switch",
        json={"target_world_id": "world-root"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert response.json()["active_world_id"] == "world-root"


def test_switch_rejects_target_outside_tree(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    owner_id = _user_id(url, "tl_http_owner")
    seed_solo_tree(url, owner_id)
    with session_scope(url) as session:
        session.add(
            World(
                id="world-other",
                module_name="mansion_of_madness",
                created_by=owner_id,
                metadata_json={"play_mode": "solo"},
            )
        )
    response = http.post(
        "/api/worlds/world-root/timelines/switch",
        json={"target_world_id": "world-other"},
        headers=ORIGIN,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "world_not_in_tree"


def test_switch_rejects_while_turn_active(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    owner_id = _user_id(url, "tl_http_owner")
    seed_solo_tree(url, owner_id)
    with session_scope(url) as session:
        session.add(
            Turn(
                pk=new_id("turnpk"),
                id="turn-active",
                world_id="world-root",
                kind="action",
                status="active",
            )
        )
    response = http.post(
        "/api/worlds/world-root/timelines/switch",
        json={"target_world_id": "world-branch"},
        headers=ORIGIN,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "room_turn_in_progress"
    with session_scope(url) as session:
        assert session.get(World, "world-root").metadata_json[POINTER_KEY] == "world-root"


def test_rename_from_lobby(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"))
    response = http.post(
        "/api/worlds/world-root/timelines/rename",
        json={"target_world_id": "world-branch", "label": " 威胁管家之前 "},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert response.json()["label"] == "威胁管家之前"
    with session_scope(url) as session:
        branch = session.get(World, "world-branch")
        assert branch.metadata_json["display_name"] == "威胁管家之前"


def test_archive_from_lobby_and_protections(client):
    _server, url, http = client
    _register(http, "tl_http_owner")
    seed_solo_tree(url, _user_id(url, "tl_http_owner"), pointer="world-root")

    # 当前时间线（root）与主根保护
    current = http.post(
        "/api/worlds/world-root/timelines/archive",
        json={"target_world_id": "world-root"},
        headers=ORIGIN,
    )
    assert current.status_code == 409
    assert "当前" in current.json()["detail"] or "主时间线" in current.json()["detail"]

    archived = http.post(
        "/api/worlds/world-root/timelines/archive",
        json={"target_world_id": "world-branch"},
        headers=ORIGIN,
    )
    assert archived.status_code == 200
    assert archived.json()["world_id"] == "world-branch"
    with session_scope(url) as session:
        assert session.get(World, "world-branch").status == "archived"
    # 归档后不再出现在时间线列表
    payload = http.get("/api/worlds/world-root/timelines", headers=ORIGIN).json()
    assert {e["world_id"] for e in payload["worlds"]} == {"world-root"}


def test_archive_rejects_connected_target_room(client):
    """目标时间线的房间仍连着客户端时拒绝归档（409 timeline_in_use）。"""
    server, url, http = client
    _register(http, "tl_http_owner")
    owner_id = _user_id(url, "tl_http_owner")
    seed_solo_tree(url, owner_id)

    room = GameRoom(
        "world-branch",
        SimpleNamespace(context=SimpleNamespace(module_name="mansion_of_madness")),
        RoomEventHub("world-branch"),
        owner_id,
        current_actor_user_id=owner_id,
        status="playing",
        play_mode="solo",
        connected_users={owner_id: 1},
    )

    async def install_room():
        await server.ROOM_MANAGER.get_or_create("world-branch", lambda: room)

    asyncio.run(install_room())
    try:
        response = http.post(
            "/api/worlds/world-root/timelines/archive",
            json={"target_world_id": "world-branch"},
            headers=ORIGIN,
        )
        assert response.status_code == 409
        assert response.json()["code"] == "timeline_in_use"
        with session_scope(url) as session:
            assert session.get(World, "world-branch").status == "active"
    finally:
        asyncio.run(server.ROOM_MANAGER.remove("world-branch", room))


def test_switch_tears_down_loaded_current_room(client):
    """大厅切换时若旧当前世界的房间已加载（无连接），提交后拆除运行时。"""
    server, url, http = client
    _register(http, "tl_http_owner")
    owner_id = _user_id(url, "tl_http_owner")
    seed_solo_tree(url, owner_id, pointer="world-root")

    room = GameRoom(
        "world-root",
        SimpleNamespace(context=SimpleNamespace(module_name="mansion_of_madness")),
        RoomEventHub("world-root"),
        owner_id,
        current_actor_user_id=owner_id,
        status="playing",
        play_mode="solo",
        connected_users={},
    )
    room.action_status_callback = lambda wid, aid, status: finish_room_action(
        url, wid, aid, status
    )

    async def install_room():
        await server.ROOM_MANAGER.get_or_create("world-root", lambda: room)

    asyncio.run(install_room())
    response = http.post(
        "/api/worlds/world-root/timelines/switch",
        json={"target_world_id": "world-branch"},
        headers=ORIGIN,
    )
    assert response.status_code == 200

    async def room_gone():
        return await server.ROOM_MANAGER.get("world-root")

    assert asyncio.run(room_gone()) is None
    with session_scope(url) as session:
        assert session.get(World, "world-root").metadata_json[POINTER_KEY] == "world-branch"
