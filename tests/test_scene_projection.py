"""玩家可见“当前场景”投影的契约测试。

顶栏那一行位置只有一份权威来源：世界状态里的 ``current_scene``。这些用例锁定
三件事——投影只读已结算状态、只输出玩家可知名、以及本地与云端各条同步链路
（初始化、状态查询、移动、重连镜像、读取失败）都带上它。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.gameplay.scene_projection import player_scene_view, player_scene_view_for
from src.multiplayer.room_runtime import GameRoom, RoomEventHub

SCARLET = "猩红文档"


def _world(**scene: object) -> dict:
    return {"current_scene": {"id": "miskatonic_medical", **scene}}


# ---- 单元：投影只从已结算状态派生，且不泄露内部字段 ----


class SceneProjectionUnitTests(unittest.TestCase):
    def test_settled_scene_name_is_projected(self):
        view = player_scene_view(_world(name="医学院地下停尸房"))
        self.assertEqual({"name": "医学院地下停尸房"}, view)

    def test_only_the_player_facing_name_is_exported(self):
        view = player_scene_view(
            {
                "current_scene": {
                    "id": "miskatonic_medical",
                    "name": "密斯卡托尼克大学医学院",
                    "description": "医学院地下深处冰冷的停尸房。莱特的尸体锁在冷柜中。",
                    "exits": ["miskatonic_university"],
                    "npcs_present": ["john_whitcroft"],
                    "document": "scenes/医学院构造.md",
                }
            }
        )
        assert view is not None
        self.assertEqual(["name"], list(view))
        blob = json.dumps(view, ensure_ascii=False)
        for leaked in (
            "miskatonic_medical",
            "停尸房",
            "冷柜",
            "john_whitcroft",
            "scenes/",
        ):
            self.assertNotIn(leaked, blob)

    def test_scene_id_used_as_name_is_treated_as_unknown(self):
        # 老加载器在缺 name 时会把场景 id 当名称写进状态；那串 id 是内部键，
        # 显示出来等于泄露，因此宁可按“位置未知”处理。
        self.assertIsNone(
            player_scene_view({"current_scene": {"id": "witch_altar", "name": "witch_altar"}})
        )

    def test_missing_or_blank_scene_yields_unknown_instead_of_guessing(self):
        self.assertIsNone(player_scene_view({}))
        self.assertIsNone(player_scene_view({"current_scene": None}))
        self.assertIsNone(player_scene_view({"current_scene": {"id": "x"}}))
        self.assertIsNone(player_scene_view({"current_scene": {"id": "x", "name": "   "}}))
        self.assertIsNone(player_scene_view(None))

    def test_module_public_name_overrides_the_internal_scene_title(self):
        world = {
            "current_scene": {"id": "witch_altar", "name": "地窖祭坛"},
            "scene_catalog": {"witch_altar": {"public_name": "陌生的地窖"}},
        }
        self.assertEqual({"name": "陌生的地窖"}, player_scene_view(world))

    def test_catalog_public_name_reaches_old_saves_without_a_scene_move(self):
        # 旧存档的 current_scene 里没有 public_name；模组刷新后目录里有，
        # 于是不必移动、也不必重开就能换到玩家可见名。
        world = {
            "current_scene": {"id": "miskatonic_medical", "name": "密斯卡托尼克大学医学院"},
            "scene_catalog": {"miskatonic_medical": {"public_name": "医学院地下停尸房"}},
        }
        self.assertEqual({"name": "医学院地下停尸房"}, player_scene_view(world))

    def test_world_local_public_name_wins_over_the_catalog(self):
        world = {
            "current_scene": {
                "id": "miskatonic_medical",
                "name": "密斯卡托尼克大学医学院",
                "public_name": "停尸间",
            },
            "scene_catalog": {"miskatonic_medical": {"public_name": "医学院地下停尸房"}},
        }
        self.assertEqual({"name": "停尸间"}, player_scene_view(world))

    def test_name_is_trimmed_and_capped(self):
        self.assertEqual({"name": "医学 院"}, player_scene_view(_world(name="  医学  院  ")))
        long_view = player_scene_view(_world(name="地" * 400))
        assert long_view is not None
        self.assertEqual(120, len(long_view["name"]))

    def test_broken_world_store_reports_unknown_instead_of_raising(self):
        def boom() -> dict:
            raise RuntimeError("world store unavailable")

        context = SimpleNamespace(world_store=SimpleNamespace(load=boom))
        self.assertIsNone(player_scene_view_for(context))


# ---- 本地单机 WebSocket：初始化与状态查询都带场景投影 ----


class LocalSceneSyncTests(unittest.TestCase):
    def _receive_until(self, ws, wanted: str, limit: int = 8) -> dict:
        for _ in range(limit):
            payload = ws.receive_json()
            if payload.get("type") == wanted:
                return payload
        self.fail(f"没有收到 {wanted} 协议帧")

    def _bootstrap(self, ws) -> dict:
        """排空首连初始化帧（世界身份随 module_list 下发），返回世界标识。"""
        ws.send_json({"type": "ping"})
        world_id = ""
        while True:
            payload = ws.receive_json()
            if payload.get("type") == "module_list":
                world_id = str(payload.get("world_id") or "")
            elif payload.get("type") == "pong":
                return world_id

    def _state(self, ws) -> dict:
        ws.send_json({"type": "state"})
        return self._receive_until(ws, "state_data")

    def _open(self, runtime_root: Path):
        import server

        return patch.object(server, "RUNTIME_ROOT", runtime_root)

    def _context(self, runtime_root: Path, module: str = SCARLET) -> RuntimeContext:
        return RuntimeContext.local(
            module, project_root=PROJECT_ROOT, runtime_root=runtime_root
        )

    def test_state_data_carries_the_settled_scene_and_the_world_identity(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            expected = self._context(runtime_root).world_store.load()["current_scene"]["name"]
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                self._open(runtime_root),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect(f"/ws?module={SCARLET}") as ws:
                        world_id = self._bootstrap(ws)
                        state = self._state(ws)

        self.assertEqual(f"local-{SCARLET}", world_id)
        self.assertEqual(world_id, state["world_id"])
        self.assertEqual(expected, state["scene"]["name"])
        # 顶栏只要一个地名：完整场景（描述、出口、在场人物）不得随状态出境。
        self.assertEqual(["name"], list(state["scene"]))

    def test_a_committed_move_updates_the_label_and_a_repeat_query_does_not(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            # 先在临时根下建好世界（含数据库表），再连 WebSocket 会话。
            context = self._context(runtime_root)
            opening_name = context.world_store.load()["current_scene"]["name"]
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                self._open(runtime_root),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect(f"/ws?module={SCARLET}") as ws:
                        self._bootstrap(ws)
                        before = self._state(ws)
                        # 没有提交任何状态变更时重复查询：位置不动。
                        again = self._state(ws)

                        # 提交一次移动：按引擎提交移动的写法直接改写权威世界状态。
                        catalog = context.world_store.load()["scene_catalog"]
                        with context.world_store.transaction() as state:
                            state["current_scene"] = {
                                **catalog["wright_office"],
                                "npcs_present": [],
                            }
                        after = self._state(ws)

        self.assertEqual({"name": opening_name}, before["scene"])
        self.assertEqual(before["scene"], again["scene"])
        self.assertEqual({"name": "莱特的办公室"}, after["scene"])
        # 场景描述属于幕后文本，不能跟着地名一起出境。
        self.assertNotIn("熔化的裂镜", json.dumps(after, ensure_ascii=False))

    def test_unknown_scene_is_reported_as_unknown_not_as_a_stale_name(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            context = self._context(runtime_root)
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                self._open(runtime_root),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect(f"/ws?module={SCARLET}") as ws:
                        self._bootstrap(ws)
                        with context.world_store.transaction() as state:
                            state["current_scene"] = {
                                "id": "witch_altar",
                                "name": "witch_altar",
                            }
                        state = self._state(ws)

        self.assertIsNone(state["scene"])
        self.assertNotIn("witch_altar", json.dumps(state, ensure_ascii=False))

    def test_switching_worlds_resends_the_authoritative_scene(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            self._context(runtime_root)
            other = self._context(runtime_root, "mansion_of_madness")
            expected = other.world_store.load()["current_scene"]["name"]
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                self._open(runtime_root),
                # 换模组会写进程级 _active_context；这里用临时根，不能把它留给
                # 之后的用例（默认运行时隔离由 test_test_runtime_isolation 把关）。
                patch.object(server, "_set_active_context", lambda _context: None),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect(f"/ws?module={SCARLET}") as ws:
                        first_world = self._bootstrap(ws)
                        ws.send_json({"type": "switch_module", "module": "mansion_of_madness"})
                        context_payload = self._receive_until(ws, "world_context", 8)

        self.assertNotEqual(first_world, context_payload["world_id"])
        self.assertEqual("local-mansion_of_madness", context_payload["world_id"])
        self.assertEqual(expected, context_payload["scene"]["name"])
        self.assertNotEqual(server._active_context.runtime_root, runtime_root)


# ---- 云端多人：房间状态查询与重连镜像 ----


class _OneStateSocket:
    """只发一次请求，随后结束房间消息循环。"""

    def __init__(self, request: dict | None = None):
        self.reads = 0
        self.messages: list[dict] = []
        self._request = request or {"type": "state"}

    async def receive_text(self) -> str:
        self.reads += 1
        if self.reads == 1:
            return json.dumps(self._request)
        raise RuntimeError("test complete")

    async def send_json(self, payload) -> None:
        self.messages.append(dict(payload))


def _room_world() -> dict:
    return {
        "current_scene": {
            "id": "miskatonic_medical",
            "name": "密斯卡托尼克大学医学院",
            "description": "医学院地下深处冰冷的停尸房。",
            "npcs_present": ["john_whitcroft"],
        },
        "pc": {"name": "Alice"},
        "clues_found": {},
    }


def _room(world_store) -> GameRoom:
    context = SimpleNamespace(world_id="world-scene", world_store=world_store)
    return GameRoom(
        "world-scene",
        SimpleNamespace(context=context),
        RoomEventHub("world-scene"),
        "owner",
        current_actor_user_id="owner",
        status="playing",
    )


def test_room_state_request_carries_the_scene_projection():
    from src.multiplayer.messages import run_room_message_loop

    room = _room(SimpleNamespace(load=_room_world))
    socket = _OneStateSocket()
    controller = SimpleNamespace(
        deps=SimpleNamespace(
            database_url=lambda: "sqlite://",
            enrich_clues=lambda clues, _state, _context: clues,
        ),
        authoritative_investigator_id=lambda *_args: None,
    )

    async def scenario():
        with (
            patch("src.multiplayer.messages.websocket_user", return_value=object()),
            patch("src.multiplayer.messages.authorize_world", return_value="owner"),
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
    payload = next(item for item in socket.messages if item["type"] == "state_data")
    assert payload["world_id"] == "world-scene"
    assert payload["scene"] == {"name": "密斯卡托尼克大学医学院"}
    assert "停尸房" not in json.dumps(payload, ensure_ascii=False)


def test_room_full_state_carries_the_scene_projection_and_world_id(tmp_path: Path):
    from src.multiplayer.recovery import recovery_messages

    room = _room(SimpleNamespace(load=_room_world))
    room.engine.context.world_dir = tmp_path
    full = recovery_messages(room, "owner", lambda clues, _s, _c: clues, 0, [])[0]
    assert full["type"] == "room_full_state"
    assert full["world_id"] == "world-scene"
    assert full["scene"] == {"name": "密斯卡托尼克大学医学院"}


def test_room_recovery_reports_unknown_scene_without_a_scene_name(tmp_path: Path):
    from src.multiplayer.recovery import recovery_messages

    def broken() -> dict:
        raise RuntimeError("world store unavailable")

    room = _room(SimpleNamespace(load=broken))
    room.engine.context.world_dir = tmp_path
    full = recovery_messages(room, "owner", lambda c, _s, _c: c, 0, [])[0]
    assert full["scene"] is None
    assert full["world_id"] == "world-scene"


# ---- 真实模组：猩红文档开局与停尸房都给出玩家可见名 ----


def test_scarlet_module_scenes_project_a_player_visible_name():
    state = json.loads(
        (PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text(encoding="utf-8")
    )
    opening = player_scene_view(state)
    assert opening is not None
    assert opening["name"] == state["current_scene"]["name"]

    # 停尸房的场景描述是幕后文本，投影里不能带上它。
    medical = {
        **state,
        "current_scene": {**state["scene_catalog"]["miskatonic_medical"], "npcs_present": []},
    }
    view = player_scene_view(medical)
    assert view is not None
    assert view["name"] == medical["current_scene"]["name"]
    assert "冷柜" not in view["name"]
    assert set(view) == {"name"}
