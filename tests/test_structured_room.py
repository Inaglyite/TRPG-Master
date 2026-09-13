"""structured_v1 世界创建与房间开场（M1 无模型闭环）。

- 创建时校验 execution_profile/keeper_mode；结构化世界创建者自动 can_keeper。
- 结构化房间开局不建模型会话、不跑开场回合：翻转状态 + 按成员投影下发快照。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.modules.module_registry import ModuleRegistry
from src.multiplayer.service import MultiplayerError
from src.multiplayer.world_creation import create_owned_world
from src.storage.database import User, World, WorldMember, session_scope
from src.structured.room_integration import (
    handle_structured_room_start,
    room_world_modes,
)


def _make_module_root(root: Path) -> None:
    module_dir = root / "mod" / "test-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.md").write_text("# Test", encoding="utf-8")
    (module_dir / "world_state_initial.json").write_text(
        '{"module": "test-module", "pc": {"name": "占位", "hp": 1},'
        ' "current_scene": {"id": "hall", "name": "大厅"},'
        ' "scene_catalog": {"hall": {"id": "hall", "name": "大厅"}}}',
        encoding="utf-8",
    )


class WorldCreationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        _make_module_root(self.root)
        self.registry = ModuleRegistry(self.root, self.root)
        self.db_url = f"sqlite:///{self.root / 'trpg-master.db'}"
        from src.storage.database import initialize_database

        initialize_database(self.db_url)
        with session_scope(self.db_url) as session:
            session.add(User(id="u-creator", username="creator", password_hash="x"))

    def tearDown(self):
        self._temp.cleanup()

    def _create(self, **extra):
        import asyncio

        data = {"module": "test-module", "name": "测试房", **extra}
        return asyncio.run(
            create_owned_world(
                database_url=self.db_url,
                creator_id="u-creator",
                creator_username="creator",
                data=data,
                module_registry=self.registry,
                default_module_name="test-module",
                project_root=self.root,
                runtime_root=self.root,
            )
        )

    def test_structured_world_creation_grants_creator_keeper(self):
        result = self._create(execution_profile="structured_v1", keeper_mode="human")
        with session_scope(self.db_url) as session:
            world = session.get(World, result["world_id"])
            self.assertEqual(("structured_v1", "human"), room_world_modes(self.db_url, world.id))
            member = (
                session.query(WorldMember).filter_by(world_id=world.id, user_id="u-creator").one()
            )
            self.assertTrue(member.can_keeper)

    def test_legacy_default_unchanged(self):
        result = self._create()
        with session_scope(self.db_url) as session:
            world = session.get(World, result["world_id"])
            self.assertEqual(("legacy", "human"), room_world_modes(self.db_url, world.id))
            member = (
                session.query(WorldMember).filter_by(world_id=world.id, user_id="u-creator").one()
            )
            self.assertFalse(member.can_keeper)

    def test_invalid_profile_rejected(self):
        with self.assertRaises(MultiplayerError) as raised:
            self._create(execution_profile="structured_v9")
        self.assertEqual("invalid_execution_profile", raised.exception.code)
        with self.assertRaises(MultiplayerError) as raised:
            self._create(execution_profile="structured_v1", keeper_mode="oracle")
        self.assertEqual("invalid_keeper_mode", raised.exception.code)


class _FakeHub:
    def __init__(self, connections):
        self._connections = connections
        self.direct: list[tuple[str, dict]] = []

    async def connection_snapshot(self):
        return list(self._connections)

    async def send_direct(self, connection_id: str, payload: dict) -> bool:
        self.direct.append((connection_id, payload))
        return True


class _FakeRoom:
    def __init__(self, hub, status="lobby"):
        self.hub = hub
        self.status = status
        self.play_mode = "multiplayer"


class _FakeController:
    def __init__(self, db_url):
        class deps:
            @staticmethod
            def database_url():
                return db_url

        self.deps = deps()
        self.status_calls: list[str] = []

    def set_room_status(self, room, status):
        room.status = status
        self.status_calls.append(status)

    async def broadcast_room_state(self, room):
        pass


class _FakeWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id


class StructuredRoomStartTests(unittest.IsolatedAsyncioTestCase):
    """无模型开场：keeper 开局翻转状态并按成员投影下发快照。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        _make_module_root(self.root)
        self.db_url = f"sqlite:///{self.root / 'trpg-master.db'}"
        from src.storage.database import initialize_database

        initialize_database(self.db_url)
        with session_scope(self.db_url) as session:
            session.add_all(
                [
                    User(id="u-k", username="k", password_hash="x"),
                    User(id="u-p", username="p", password_hash="x"),
                ]
            )
        import asyncio

        self.world_id = asyncio.run(
            create_owned_world(
                database_url=self.db_url,
                creator_id="u-k",
                creator_username="k",
                data={
                    "module": "test-module",
                    "name": "结构化房",
                    "execution_profile": "structured_v1",
                    "keeper_mode": "human",
                },
                module_registry=ModuleRegistry(self.root, self.root),
                default_module_name="test-module",
                project_root=self.root,
                runtime_root=self.root,
            )
        )["world_id"]
        with session_scope(self.db_url) as session:
            session.add(WorldMember(id="m-p", world_id=self.world_id, user_id="u-p", role="player"))

    def tearDown(self):
        self._temp.cleanup()

    def _room(self, status="lobby"):
        hub = _FakeHub(
            [
                {"connection_id": "c-k", "user_id": "u-k", "role": "owner", "last_ack": 0},
                {"connection_id": "c-p", "user_id": "u-p", "role": "player", "last_ack": 0},
            ]
        )
        return _FakeRoom(hub, status=status)

    async def test_keeper_start_without_model_session(self):
        room = self._room()
        controller = _FakeController(self.db_url)
        ws = _FakeWs()
        await handle_structured_room_start(controller, room, ws, _FakeUser("u-k"), self.world_id)
        self.assertEqual(["playing"], controller.status_calls)
        self.assertEqual("playing", room.status)
        # 两个连接各自收到按自己 principal 投影的快照
        sent = {cid: env for cid, env in room.hub.direct}
        self.assertEqual({"c-k", "c-p"}, set(sent))
        self.assertEqual("session_snapshot", sent["c-k"]["type"])
        self.assertEqual("structured_v1", sent["c-k"]["payload"]["execution_profile"])
        # 玩家连接没有任何调查员认领 → investigator_id 为 null
        self.assertIsNone(sent["c-p"]["payload"]["investigator_id"])

    async def test_non_keeper_cannot_start(self):
        room = self._room()
        controller = _FakeController(self.db_url)
        ws = _FakeWs()
        await handle_structured_room_start(controller, room, ws, _FakeUser("u-p"), self.world_id)
        self.assertEqual([], controller.status_calls)
        self.assertEqual("keeper_required", ws.sent[0]["code"])

    async def test_double_start_rejected(self):
        room = self._room(status="playing")
        controller = _FakeController(self.db_url)
        ws = _FakeWs()
        await handle_structured_room_start(controller, room, ws, _FakeUser("u-k"), self.world_id)
        self.assertEqual([], controller.status_calls)
        self.assertEqual("room_already_started", ws.sent[0]["code"])


if __name__ == "__main__":
    unittest.main()
