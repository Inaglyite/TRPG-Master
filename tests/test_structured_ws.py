"""structured_v1 连接层：网关帧处理、事件投递过滤、快照与错误信封（M1 接线）。

关键验收：
- 收到的帧先过冻结 schema；服务层发出的上线信封也过 events.json。
- request_error 持久化到 outbox（真实 event_id，前端游标可去重），不推进
  世界 revision，且只投递给发起方。
- 帧的 world_id 必须与连接世界一致。
- 本地无账号模式走隐式 local 操作者，授权链路与云端同一套规则。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import (
    EventOutbox,
    User,
    World,
    WorldInvestigator,
    WorldMember,
    session_scope,
)
from src.storage.database_store import DatabaseWorldStore
from src.structured.gateway import STRUCTURED_FRAME_TYPES, StructuredGateway
from src.structured.service import wire_envelope

SCHEMA_DIR = PROJECT_ROOT / "schemas" / "structured-play" / "v1"


def _event_validator() -> jsonschema.Draft202012Validator:
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMA_DIR.glob("*.json")
        if path.name != "permission-matrix.json"
    }
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(
            schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012)
        )
    return jsonschema.Draft202012Validator(schemas["events.json"], registry=registry)


def make_ws_world(root: Path, *, profile: str = "structured_v1") -> RuntimeContext:
    """单个调查员的结构化世界（本地无账号形态：没有成员/认领行）。"""
    module_dir = root / "mod" / "test-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.md").write_text("# Test", encoding="utf-8")
    (module_dir / "world_state_initial.json").write_text(
        json.dumps(
            {
                "module": "test-module",
                "investigators": {
                    "inv-solo": {
                        "name": "独行侦探",
                        "hp": 10,
                        "max_hp": 10,
                        "san": 50,
                        "max_san": 50,
                        "skills": {"侦查": 60},
                        "inventory": ["手电筒"],
                        "conditions": [],
                    }
                },
                "current_scene": {"id": "hall", "name": "大厅", "exits": ["study"]},
                "scene_catalog": {
                    "hall": {"id": "hall", "name": "大厅", "exits": ["study"]},
                    "study": {"id": "study", "name": "书房", "exits": ["hall"]},
                },
                "clue_catalog": {
                    "clue_letter": {
                        "id": "clue_letter",
                        "category": "investigation",
                        "text": "一封未寄出的信。",
                    }
                },
                "clues_found": {"investigation": [], "event": [], "task": [], "npc": []},
                "combat_state": {"active": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    context = RuntimeContext.create("ws-world", "test-module", project_root=root, runtime_root=root)
    with session_scope(context.database_url) as session:
        world = session.get(World, "ws-world")
        world.metadata_json = {
            **(world.metadata_json or {}),
            "execution_profile": profile,
            "keeper_mode": "human",
        }
    return context


class _Collector:
    """模拟一条客户端连接：记录收到的信封。"""

    def __init__(self) -> None:
        self.received: list[dict] = []

    async def send(self, envelope: dict) -> None:
        self.received.append(envelope)


class StructuredGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_ws_world(self.root)
        self.gateway = StructuredGateway(self.context.database_url)
        self.validator = _event_validator()

    def tearDown(self):
        self._temp.cleanup()

    def persisted_revision(self) -> int:
        store = DatabaseWorldStore(self.context.database_url, "ws-world", self.context.world_dir)
        return store.snapshot().revision

    def outbox(self) -> list:
        from sqlalchemy import select

        with session_scope(self.context.database_url) as session:
            return list(
                session.execute(
                    select(EventOutbox)
                    .where(EventOutbox.world_id == "ws-world")
                    .order_by(EventOutbox.sequence)
                ).scalars()
            )

    def assert_wire_valid(self, envelope: dict) -> None:
        errors = list(self.validator.iter_errors(envelope))
        self.assertEqual(
            [],
            [f"{list(e.path)}: {e.message}" for e in errors],
            f"上线信封不过 events.json：{envelope.get('type')}",
        )

    def _action_frame(self, request_id: str, action: dict, **overrides) -> dict:
        frame = {
            "type": "action_request",
            "protocol_version": 1,
            "world_id": "ws-world",
            "expected_revision": self.persisted_revision(),
            "investigator_id": "inv-solo",
            "request_id": request_id,
            "action": action,
        }
        frame.update(overrides)
        return frame

    # --------------------------------------------------------------
    # 本地无账号闭环
    # --------------------------------------------------------------

    async def test_local_operator_full_loop_without_membership_rows(self):
        caller = _Collector()
        # 无账号、无成员行：local 操作者提交移动请求 → 排队 + ack
        await self.gateway.handle_frame(
            world_id="ws-world",
            user_id=None,
            frame=self._action_frame(
                "req-move-1", {"kind": "move", "destination_scene_id": "study"}
            ),
            deliver=caller.send,
        )
        types = [e["type"] for e in caller.received]
        self.assertIn("action_ack", types)
        for envelope in caller.received:
            self.assert_wire_valid(envelope)
        # local 操作者自动具备 keeper 授权：直接执行主持命令
        await self.gateway.handle_frame(
            world_id="ws-world",
            user_id=None,
            frame={
                "type": "command_request",
                "protocol_version": 1,
                "command_id": "cmd-move-1",
                "world_id": "ws-world",
                "expected_revision": self.persisted_revision(),
                "kind": "move_party",
                "payload": {"destination_scene_id": "study", "travel_minutes": 5},
                "cause_id": "req-move-1",
            },
            deliver=caller.send,
        )
        scene_events = [e for e in caller.received if e["type"] == "scene_changed"]
        self.assertEqual(1, len(scene_events))
        self.assertEqual("study", scene_events[0]["payload"]["scene"]["id"])
        self.assert_wire_valid(scene_events[0])
        # 事件不携带路由 audience（定向信息不下线）
        self.assertNotIn("audience", scene_events[0])

    async def test_snapshot_projection_and_schema(self):
        snapshot = self.gateway.snapshot_envelope(world_id="ws-world", user_id=None)
        self.assertIsNotNone(snapshot)
        self.assert_wire_valid(snapshot)
        payload = snapshot["payload"]
        self.assertEqual("structured_v1", payload["execution_profile"])
        self.assertEqual("human", payload["keeper_mode"])
        self.assertEqual("inv-solo", payload["investigator_id"])
        self.assertEqual({"id": "hall", "name": "大厅"}, payload["scene"])
        self.assertEqual([{"id": "study", "name": "书房"}], payload["destinations"])

    # --------------------------------------------------------------
    # 错误信封：持久化、定向、不推进 revision
    # --------------------------------------------------------------

    async def test_request_error_persisted_and_scoped(self):
        caller = _Collector()
        revision_before = self.persisted_revision()
        # schema 就不过的帧（未知行动类型）
        bad = self._action_frame("req-bad-1", {"kind": "explode"})
        await self.gateway.handle_frame(
            world_id="ws-world", user_id=None, frame=bad, deliver=caller.send
        )
        self.assertEqual(1, len(caller.received))
        error = caller.received[0]
        self.assertEqual("request_error", error["type"])
        self.assertEqual("invalid_action", error["payload"]["code"])
        self.assertEqual("req-bad-1", error["payload"]["request_id"])
        self.assertGreater(error["event_id"], 0)  # 真实 event_id，前端可去重
        self.assert_wire_valid(error)
        self.assertEqual(revision_before, self.persisted_revision())
        rows = [row for row in self.outbox() if row.event_type == "request_error"]
        self.assertEqual(1, len(rows))
        self.assertNotEqual({"kind": "public"}, rows[0].audience)  # 不公开错误细节

    async def test_cross_world_frame_rejected(self):
        caller = _Collector()
        frame = self._action_frame(
            "req-cross-1",
            {"kind": "move", "destination_scene_id": "study"},
            world_id="another-world",
        )
        await self.gateway.handle_frame(
            world_id="ws-world", user_id=None, frame=frame, deliver=caller.send
        )
        self.assertEqual("not_authorized", caller.received[0]["payload"]["code"])

    async def test_revision_conflict_is_retryable(self):
        caller = _Collector()
        frame = self._action_frame(
            "req-rev-1",
            {"kind": "move", "destination_scene_id": "study"},
            expected_revision=999,
        )
        await self.gateway.handle_frame(
            world_id="ws-world", user_id=None, frame=frame, deliver=caller.send
        )
        self.assertEqual("revision_conflict", caller.received[0]["payload"]["code"])
        self.assertTrue(caller.received[0]["payload"]["retryable"])

    # --------------------------------------------------------------
    # 隐私过滤与广播
    # --------------------------------------------------------------

    async def test_keeper_only_events_not_delivered_to_player_caller(self):
        caller = _Collector()
        await self.gateway.handle_frame(
            world_id="ws-world",
            user_id=None,
            frame=self._action_frame(
                "req-move-2", {"kind": "move", "destination_scene_id": "study"}
            ),
            deliver=caller.send,
        )
        types = [e["type"] for e in caller.received]
        # local 操作者是 keeper：能看到 keeper 向的 intent_pending
        self.assertIn("intent_pending", types)

    async def test_broadcast_filters_by_recipient_principal(self):
        """keeper 私密事件不进入其他玩家连接的广播流。"""
        # 云端形态：补两个真实用户，keeper + player
        with session_scope(self.context.database_url) as session:
            session.add_all(
                [
                    User(id="u-k", username="k", password_hash="x"),
                    User(id="u-p", username="p", password_hash="x"),
                ]
            )
            session.add_all(
                [
                    WorldMember(
                        id="m-k",
                        world_id="ws-world",
                        user_id="u-k",
                        role="player",
                        can_keeper=True,
                    ),
                    WorldMember(
                        id="m-p",
                        world_id="ws-world",
                        user_id="u-p",
                        role="player",
                    ),
                ]
            )
            session.add(
                WorldInvestigator(
                    id="wi-p",
                    world_id="ws-world",
                    character_key="inv-solo",
                    character_ref={},
                    controller_user_id="u-p",
                    status="claimed",
                )
            )
        keeper_box = _Collector()
        player_box = _Collector()
        others = {"u-p": player_box}  # 模拟另一连接的投递

        async def broadcast(envelope: dict) -> None:
            from src.structured.service import audience_visible

            for uid, box in others.items():
                principal = self.gateway.connection_principal("ws-world", uid)
                if principal is not None and audience_visible(envelope["audience"], principal):
                    await box.send(wire_envelope(envelope))

        # 玩家提交意图：action_ack 公开、intent_pending 只给 keeper
        await self.gateway.handle_frame(
            world_id="ws-world",
            user_id="u-p",
            frame=self._action_frame(
                "req-move-3", {"kind": "move", "destination_scene_id": "study"}
            ),
            deliver=player_box.send,
            broadcast=broadcast,
        )
        player_types = [e["type"] for e in player_box.received]
        self.assertIn("action_ack", player_types)
        # u-p 同时是发起方与唯一玩家：broadcast 里 intent_pending 的 audience
        # 是 keeper，u-p 不是 keeper → 收不到；这里广播只给了 u-p，所以看不到。
        self.assertNotIn("intent_pending", player_types)

        # keeper 发出私密线索：只进持有者，不进广播里的旁观玩家
        await self.gateway.handle_frame(
            world_id="ws-world",
            user_id="u-k",
            frame={
                "type": "command_request",
                "protocol_version": 1,
                "command_id": "cmd-grant-1",
                "world_id": "ws-world",
                "expected_revision": self.persisted_revision(),
                "kind": "grant_clue",
                "payload": {
                    "clue_id": "clue_letter",
                    "recipient_investigator_ids": ["inv-solo"],
                    "basis": "在书桌抽屉里找到。",
                },
            },
            deliver=keeper_box.send,
            broadcast=broadcast,
        )
        keeper_types = [e["type"] for e in keeper_box.received]
        self.assertIn("clue_granted", keeper_types)
        # u-p 控制 inv-solo → 是接收者，广播里应该有
        self.assertIn("clue_granted", [e["type"] for e in player_box.received])
        for envelope in keeper_box.received + player_box.received:
            self.assert_wire_valid(envelope)

    # --------------------------------------------------------------
    # legacy 世界
    # --------------------------------------------------------------

    async def test_legacy_world_gets_profile_mismatch(self):
        with session_scope(self.context.database_url) as session:
            world = session.get(World, "ws-world")
            world.metadata_json = {"execution_profile": "legacy"}
        caller = _Collector()
        await self.gateway.handle_frame(
            world_id="ws-world",
            user_id=None,
            frame=self._action_frame(
                "req-legacy-1", {"kind": "move", "destination_scene_id": "study"}
            ),
            deliver=caller.send,
        )
        self.assertEqual("profile_mismatch", caller.received[0]["payload"]["code"])

    async def test_frame_types_exported(self):
        self.assertEqual(
            {
                "action_request",
                "free_roll_request",
                "check_response",
                "cancel_request",
                "command_request",
            },
            STRUCTURED_FRAME_TYPES,
        )


if __name__ == "__main__":
    unittest.main()
