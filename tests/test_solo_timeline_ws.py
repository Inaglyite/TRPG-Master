"""云端单人房间专用时间线协议（solo_* 消息）的权限与语义测试。"""

from __future__ import annotations

import asyncio
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
from src.multiplayer_messages import run_room_message_loop
from src.room_runtime import GameRoom, RoomEventHub, RoomManager
from src.solo_timeline_ws import (
    POINTER_KEY,
    resolve_solo_current_world_id,
)
from src.world_branches import WorldBranchService


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'solo-timeline.db'}"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path):
    # WorldBranchService 内部用 database_url(runtime_root)，环境变量优先；
    # 指到同一个临时 sqlite，服务层与测试种子才落在同一库。
    with patch.dict(
        os.environ,
        {
            "TRPG_WRITE_COMPAT_EXPORTS": "0",
            "TRPG_DATABASE_URL": sqlite_url(tmp_path),
        },
    ):
        yield


def seed_tree(tmp_path: Path, *, with_second_member: bool = False, pointer: str = ""):
    """一棵 solo 世界树：root（playing）+ branch（resumable），claim 在 root 上。"""
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "tl_owner", "owner password 123")
    guest = create_user(url, "tl_guest", "guest password 123")
    root_metadata = {
        "name": "私密单人世界",
        "room_status": "playing",
        "max_players": 1,
        "play_mode": "solo",
    }
    if pointer:
        root_metadata[POINTER_KEY] = pointer
    with session_scope(url) as session:
        session.add(
            World(
                id="world-root",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json=root_metadata,
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
                id=new_id("member"),
                world_id="world-root",
                user_id=owner.id,
                role="owner",
            )
        )
        session.add(
            WorldInvestigator(
                id="inv-root",
                world_id="world-root",
                character_key="howard",
                character_ref={"source": "module", "id": "howard"},
                controller_user_id=owner.id,
                status="claimed",
            )
        )
        session.add(
            World(
                id="world-branch",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={
                    "display_name": "分支A",
                    "branch": {
                        "parent_world_id": "world-root",
                        "source_turn_id": "turn-1",
                        "created_at": "2026-08-17T00:00:00+00:00",
                    },
                },
            )
        )
        session.add(
            WorldState(world_id="world-branch", schema_version=1, state={})
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="world-branch",
                user_id=owner.id,
                role="owner",
            )
        )
        if with_second_member:
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id="world-root",
                    user_id=guest.id,
                    role="player",
                )
            )
    # 兼容文件存档让两条时间线都可恢复（避免 SaveSlot 需要 Snapshot 外键）
    for world_id in ("world-root", "world-branch"):
        save_dir = tmp_path / "worlds" / world_id / "saves" / "slot_000"
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "messages.json").write_text("[]", encoding="utf-8")
    return url, owner, guest


def _controller(url: str, tmp_path: Path, manager: RoomManager):
    return SimpleNamespace(
        deps=SimpleNamespace(
            database_url=lambda: url,
            project_root=tmp_path,
            runtime_root=tmp_path,
            list_modules=lambda: [
                {"id": "mansion_of_madness", "title": "疯狂宅邸"}
            ],
            room_manager=lambda: manager,
        ),
    )


def _room(world_id: str, owner_id: str, *, play_mode: str = "solo") -> GameRoom:
    return GameRoom(
        world_id,
        SimpleNamespace(
            context=SimpleNamespace(
                module_name="mansion_of_madness", world_id=world_id
            ),
            turn_journal=SimpleNamespace(),
        ),
        RoomEventHub(world_id),
        owner_id,
        current_actor_user_id=owner_id,
        status="playing",
        play_mode=play_mode,
        connected_users={owner_id: 1},
    )


class _QueueSocket:
    def __init__(self, messages: list[dict]):
        self._messages = list(messages)
        self.sent: list[dict] = []

    async def receive_text(self):
        if not self._messages:
            raise RuntimeError("test complete")
        return json.dumps(self._messages.pop(0))

    async def send_json(self, payload):
        self.sent.append(dict(payload))


def _run_loop(controller, socket, room, owner_id: str, *, role: str = "owner"):
    async def scenario():
        await run_room_message_loop(
            controller,
            socket,
            room,
            SimpleNamespace(id=owner_id),
            room.world_id,
            "owner-tab",
            role,
        )

    with (
        patch("src.multiplayer_messages.websocket_user", return_value=object()),
        patch("src.multiplayer_messages.authorize_world", return_value=role),
    ):
        asyncio.run(scenario())


def _rejections(socket: _QueueSocket) -> list[dict]:
    return [m for m in socket.sent if m["type"] == "room_action_rejected"]


def _spy_broadcasts(room: GameRoom) -> list[dict]:
    """房间广播走 hub 而非请求连接，测试用实例级探针观察。"""
    broadcasts: list[dict] = []
    original = room.hub.broadcast

    async def spy(payload, **kwargs):
        broadcasts.append(dict(payload))
        return await original(payload, **kwargs)

    room.hub.broadcast = spy
    return broadcasts


def test_multiplayer_room_rejects_solo_timeline_messages(tmp_path: Path):
    """多人房间（即使只剩一人）对全部 solo_* 消息保持拒绝。"""
    url, owner, _ = seed_tree(tmp_path)
    room = _room("world-root", owner.id, play_mode="multiplayer")
    socket = _QueueSocket(
        [
            {"type": "solo_world_list"},
            {"type": "solo_world_switch", "world_id": "world-branch"},
            {"type": "solo_world_rename", "world_id": "world-branch", "label": "x"},
            {"type": "solo_world_archive", "world_id": "world-branch"},
            {"type": "solo_branch_create", "turn_id": "turn-1"},
        ]
    )
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    rejections = _rejections(socket)
    assert len(rejections) == 5
    assert {r["code"] for r in rejections} == {"solo_world_required"}


def test_solo_timeline_rejected_with_second_member(tmp_path: Path):
    """solo 世界存在第二成员时（直接 SQL 绕过创建约束）时间线操作锁定。"""
    url, owner, _ = seed_tree(tmp_path, with_second_member=True)
    room = _room("world-root", owner.id)
    socket = _QueueSocket([{"type": "solo_world_list"}])
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    assert _rejections(socket)[0]["code"] == "solo_membership_violated"


def test_solo_timeline_requires_owner_role(tmp_path: Path):
    url, owner, _ = seed_tree(tmp_path)
    room = _room("world-root", owner.id)
    socket = _QueueSocket([{"type": "solo_world_list"}])
    try:
        _run_loop(
            _controller(url, tmp_path, RoomManager()),
            socket,
            room,
            owner.id,
            role="player",
        )
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    assert _rejections(socket)[0]["code"] == "owner_required"


def test_solo_world_list_returns_tree_with_local_wire_shape(tmp_path: Path):
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    room = _room("world-root", owner.id)
    socket = _QueueSocket([{"type": "solo_world_list"}])
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    world_list = next(m for m in socket.sent if m["type"] == "world_list")
    assert world_list["active_world_id"] == "world-root"
    ids = {w["world_id"] for w in world_list["worlds"]}
    assert ids == {"world-root", "world-branch"}
    branch = next(w for w in world_list["worlds"] if w["world_id"] == "world-branch")
    assert branch["is_branch"] is True
    assert branch["resumable"] is True
    adventure_list = next(m for m in socket.sent if m["type"] == "adventure_list")
    assert len(adventure_list["adventures"]) == 1
    adventure = adventure_list["adventures"][0]
    assert adventure["root_world_id"] == "world-root"
    assert {t["world_id"] for t in adventure["timelines"]} == ids


def test_solo_world_switch_commits_pointer_and_claims(tmp_path: Path):
    """切换成功：指针与 claim 原子迁移，广播后关闭连接，持久租约完成。"""
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    room = _room("world-root", owner.id)
    room.action_status_callback = lambda wid, aid, status: finish_room_action(
        url, wid, aid, status
    )
    socket = _QueueSocket([{"type": "solo_world_switch", "world_id": "world-branch"}])
    broadcasts = _spy_broadcasts(room)
    # 切换成功后 handler 返回 "close"，消息循环正常退出而非等到断连
    _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)

    switched = next(b for b in broadcasts if b["type"] == "solo_world_switched")
    assert switched["world_id"] == "world-branch"
    assert switched["reason"] == "switched"
    with session_scope(url) as session:
        root = session.get(World, "world-root")
        assert root.metadata_json[POINTER_KEY] == "world-branch"
        claim = session.get(WorldInvestigator, "inv-root")
        assert claim.world_id == "world-branch"
        action = (
            session.query(RoomAction)
            .filter_by(world_id="world-root", action_type="solo_world_switch")
            .one()
        )
        assert action.status == "completed"


def test_solo_world_switch_rejects_turn_in_progress(tmp_path: Path):
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    room = _room("world-root", owner.id)
    asyncio.run(room.reserve_control(owner.id, "hold-the-lock"))
    assert room.action_active is True
    socket = _QueueSocket([{"type": "solo_world_switch", "world_id": "world-branch"}])
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    assert _rejections(socket)[0]["code"] == "room_turn_in_progress"
    with session_scope(url) as session:
        assert session.get(World, "world-root").metadata_json[POINTER_KEY] == "world-root"
        assert session.get(WorldInvestigator, "inv-root").world_id == "world-root"


def test_solo_world_switch_rejects_world_outside_tree(tmp_path: Path):
    """任意 world_id 不能越权切换：不在当前分支树内即拒绝。"""
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-other",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"play_mode": "solo"},
            )
        )
    room = _room("world-root", owner.id)
    socket = _QueueSocket([{"type": "solo_world_switch", "world_id": "world-other"}])
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    assert _rejections(socket)[0]["code"] == "world_not_in_tree"


def test_solo_world_switch_rejects_non_resumable_timeline(tmp_path: Path):
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    # 删掉分支的兼容自动存档：不可恢复的时间线不能切入
    (tmp_path / "worlds" / "world-branch" / "saves" / "slot_000" / "messages.json").unlink()
    room = _room("world-root", owner.id)
    socket = _QueueSocket([{"type": "solo_world_switch", "world_id": "world-branch"}])
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    assert _rejections(socket)[0]["code"] == "timeline_not_resumable"


def test_solo_world_archive_removes_inactive_branch(tmp_path: Path):
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    room = _room("world-root", owner.id)
    room.action_status_callback = lambda wid, aid, status: finish_room_action(
        url, wid, aid, status
    )
    socket = _QueueSocket([{"type": "solo_world_archive", "world_id": "world-branch"}])
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    archived = next(m for m in socket.sent if m["type"] == "world_archived")
    assert archived["world_id"] == "world-branch"
    assert archived["fallback_world_id"] == "world-root"
    with session_scope(url) as session:
        assert session.get(World, "world-branch").status == "archived"


def test_solo_world_archive_rejects_current_and_root(tmp_path: Path):
    """当前时间线与主根时间线都不能归档。"""
    url, owner, _ = seed_tree(tmp_path, pointer="world-branch")
    room = _room("world-branch", owner.id)
    # 与线上一致：终态回调把持久租约置终，否则后续操作会被 running 行挡住
    room.action_status_callback = lambda wid, aid, status: finish_room_action(
        url, wid, aid, status
    )
    socket = _QueueSocket(
        [
            {"type": "solo_world_archive", "world_id": "world-branch"},
            {"type": "solo_world_archive", "world_id": "world-root"},
        ]
    )
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    rejections = _rejections(socket)
    assert [r["code"] for r in rejections] == ["archive_failed", "archive_failed"]
    assert "当前" in rejections[0]["message"]
    assert "主时间线" in rejections[1]["message"]
    with session_scope(url) as session:
        assert session.get(World, "world-root").status == "active"
        assert session.get(World, "world-branch").status == "active"


def test_solo_world_rename_updates_label_and_lists(tmp_path: Path):
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    room = _room("world-root", owner.id)
    socket = _QueueSocket(
        [{"type": "solo_world_rename", "world_id": "world-branch", "label": " 威胁管家之前 "}]
    )
    try:
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)
    except RuntimeError as exc:
        assert str(exc) == "test complete"
    renamed = next(m for m in socket.sent if m["type"] == "world_renamed")
    assert renamed == {
        "type": "world_renamed",
        "world_id": "world-branch",
        "label": "威胁管家之前",
    }
    assert any(m["type"] == "world_list" for m in socket.sent)
    with session_scope(url) as session:
        branch = session.get(World, "world-branch")
        assert branch.metadata_json["display_name"] == "威胁管家之前"


def test_solo_branch_create_sets_control_plane_and_switches(tmp_path: Path):
    """建分支：继承 solo 约束/房间状态、补成员行、搬 claim、移动指针并切换。"""
    url, owner, _ = seed_tree(tmp_path, pointer="world-root")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-new-branch",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={
                    "display_name": "分支 · 入口大厅",
                    "branch": {
                        "parent_world_id": "world-root",
                        "source_turn_id": "turn-1",
                        "created_at": "2026-08-17T01:00:00+00:00",
                    },
                },
            )
        )

    def fake_create(self, source_context, source_journal, turn_id, *, label="", user_id=None):
        return SimpleNamespace(
            context=SimpleNamespace(
                world_id="world-new-branch", module_name="mansion_of_madness"
            ),
            messages=[],
            source_turn_id=turn_id,
            label="分支 · 入口大厅",
        )

    room = _room("world-root", owner.id)
    room.action_status_callback = lambda wid, aid, status: finish_room_action(
        url, wid, aid, status
    )
    socket = _QueueSocket(
        [{"type": "solo_branch_create", "turn_id": "turn-1", "label": ""}]
    )
    broadcasts = _spy_broadcasts(room)
    with patch.object(WorldBranchService, "create", fake_create):
        _run_loop(_controller(url, tmp_path, RoomManager()), socket, room, owner.id)

    switched = next(b for b in broadcasts if b["type"] == "solo_world_switched")
    assert switched["world_id"] == "world-new-branch"
    assert switched["reason"] == "branch_created"
    with session_scope(url) as session:
        branch = session.get(World, "world-new-branch")
        metadata = branch.metadata_json
        assert metadata["play_mode"] == "solo"
        assert metadata["max_players"] == 1
        assert metadata["room_status"] == "playing"
        assert metadata["name"] == "私密单人世界"
        member = (
            session.query(WorldMember)
            .filter_by(world_id="world-new-branch", user_id=owner.id)
            .one()
        )
        assert member.role == "owner"
        # claim 随行迁到分支（快照实体以 claim.id 为键）
        assert session.get(WorldInvestigator, "inv-root").world_id == "world-new-branch"
        root = session.get(World, "world-root")
        assert root.metadata_json[POINTER_KEY] == "world-new-branch"


def test_resolve_solo_current_world_id_pointer_semantics(tmp_path: Path):
    url, _owner, _ = seed_tree(tmp_path, pointer="world-branch")
    assert resolve_solo_current_world_id(url, "world-root") == "world-branch"
    assert resolve_solo_current_world_id(url, "world-branch") == "world-branch"

    # 指针目标已归档：自愈回退树根
    with session_scope(url) as session:
        session.get(World, "world-branch").status = "archived"
    assert resolve_solo_current_world_id(url, "world-root") == "world-root"


def test_resolve_solo_current_world_id_without_pointer(tmp_path: Path):
    url, _owner, _ = seed_tree(tmp_path)
    assert resolve_solo_current_world_id(url, "world-root") == "world-root"
    # 历史数据没有指针时，直接连分支会被引回树根
    assert resolve_solo_current_world_id(url, "world-branch") == "world-root"


CLOUD_ENV = {
    "TRPG_REQUIRE_AUTH": "1",
    "TRPG_ALLOW_REGISTRATION": "1",
    "TRPG_ALLOWED_ORIGINS": "https://testserver",
    "TRPG_WRITE_COMPAT_EXPORTS": "0",
    "TRPG_ROOM_IDLE_SECONDS": "0",
}
ORIGIN = {"origin": "https://testserver"}


def test_lobby_hides_branch_worlds_and_exposes_resume_target(tmp_path: Path):
    """大厅列表：分支世界不出现；solo 存档位带 resume_world_id 指向当前时间线。"""
    import server

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with (
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "tl_lobby", "password": "owner password 123"},
            ).status_code
            == 201
        )
        with session_scope(url) as session:
            user_id = session.query(User).filter_by(username="tl_lobby").one().id
            session.add(
                World(
                    id="world-root",
                    module_name="mansion_of_madness",
                    created_by=user_id,
                    metadata_json={
                        "name": "云端单人",
                        "room_status": "playing",
                        "max_players": 1,
                        "play_mode": "solo",
                        POINTER_KEY: "world-branch",
                    },
                )
            )
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id="world-root",
                    user_id=user_id,
                    role="owner",
                )
            )
            session.add(
                World(
                    id="world-branch",
                    module_name="mansion_of_madness",
                    created_by=user_id,
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
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id="world-branch",
                    user_id=user_id,
                    role="owner",
                )
            )
        listed = client.get("/api/worlds", headers=ORIGIN).json()["worlds"]
        ids = {item["world_id"] for item in listed}
        assert "world-branch" not in ids
        root_entry = next(item for item in listed if item["world_id"] == "world-root")
        assert root_entry["resume_world_id"] == "world-branch"
