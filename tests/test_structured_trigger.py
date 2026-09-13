"""M3 触发接线：帧类型语义、BYOK 不可用降级、房间 agent 广播含发起方。

- 仅 action_request / check_response 提交成功后调度守秘人运行；
  free_roll_request 与 command_request 不触发（主规格：普通骰不触发剧情）。
- agent 模式端到端：玩家请求 → 调度 → 决策命令提交 → 事件按连接投递；
  模型路由不可用（BYOK 缺失）时绑定 agent 控制并置触发请求 paused。
- 房间场景 agent 事件经 broadcast_all 投递：不排除发起连接（发起玩家也
  必须看到 agent 产生的场景/消息事件），且仍按各连接 principal 过滤。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from test_structured_commands import make_structured_world
from test_structured_room import _FakeController, _FakeHub, _FakeRoom, _FakeUser, _FakeWs

from src.storage.database import PlayerRequest, World, WorldState, session_scope
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal, take_control
from src.structured.service import StructuredPlayService


def _action_frame(
    request_id: str = "req-1", investigator_id: str = "inv-alice", revision: int = 0
) -> dict:
    return {
        "type": "action_request",
        "protocol_version": 1,
        "request_id": request_id,
        "world_id": "sp-world",
        "expected_revision": revision,
        "investigator_id": investigator_id,
        "action": {"kind": "freeform", "text": "我环顾四周"},
    }


def _revision(db_url: str) -> int:
    with session_scope(db_url) as session:
        return int(session.get(WorldState, "sp-world").revision)


def _deliver(box: list):
    async def deliver(envelope: dict) -> None:
        box.append(envelope)

    return deliver


class TriggerSemanticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        self.gateway = StructuredGateway(self.db_url)

    def tearDown(self):
        self._temp.cleanup()

    async def test_action_request_schedules_keeper_agent(self):
        box: list[dict] = []
        with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
            await self.gateway.handle_frame(
                world_id="sp-world",
                user_id="u-alice",
                frame=_action_frame(revision=_revision(self.db_url)),
                deliver=_deliver(box),
            )
        schedule.assert_called_once()
        kwargs = schedule.call_args.kwargs
        self.assertEqual("req-1", kwargs["trigger_request_id"])
        self.assertEqual("sp-world", kwargs["world_id"])
        # 本地场景（无 broadcast）：deliver 透传
        self.assertIsNone(kwargs["broadcast"])
        self.assertIsNotNone(kwargs["deliver"])

    async def test_free_roll_and_command_do_not_trigger(self):
        box: list[dict] = []
        with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
            await self.gateway.handle_frame(
                world_id="sp-world",
                user_id="u-bob",
                frame={
                    "type": "free_roll_request",
                    "protocol_version": 1,
                    "request_id": "req-free-1",
                    "world_id": "sp-world",
                    "investigator_id": "inv-bob",
                    "spec": "1d100",
                },
                deliver=_deliver(box),
            )
            await self.gateway.handle_frame(
                world_id="sp-world",
                user_id="u-keeper",
                frame={
                    "type": "command_request",
                    "protocol_version": 1,
                    "command_id": "cmd-no-trigger",
                    "world_id": "sp-world",
                    "expected_revision": _revision(self.db_url),
                    "kind": "record_fact",
                    "payload": {"text": "主持记录"},
                },
                deliver=_deliver(box),
            )
        schedule.assert_not_called()

    async def test_check_response_triggers(self):
        service = StructuredPlayService(self.db_url)
        check = service.execute_command(
            world_id="sp-world",
            principal=Principal(kind="keeper", user_id="u-keeper"),
            kind="request_check",
            payload={
                "investigator_id": "inv-alice",
                "skill": "侦查",
                "attempt": "搜查书桌",
            },
            command_id="cmd-trigger-check",
            expected_revision=None,
        )
        check_id = check["result"]["check_request_id"]
        box: list[dict] = []
        with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent") as schedule:
            await self.gateway.handle_frame(
                world_id="sp-world",
                user_id="u-alice",
                frame={
                    "type": "check_response",
                    "protocol_version": 1,
                    "request_id": "req-roll-9",
                    "world_id": "sp-world",
                    "check_request_id": check_id,
                    "decision": "roll",
                },
                deliver=_deliver(box),
            )
        schedule.assert_called_once()
        self.assertEqual("req-roll-9", schedule.call_args.kwargs["trigger_request_id"])


class _ScriptedCaller:
    def __init__(self, script):
        self._script = list(script)

    async def __call__(self, system: str, user: str) -> str:
        if not self._script:
            raise RuntimeError("脚本耗尽")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class AgentTriggerE2ETests(unittest.IsolatedAsyncioTestCase):
    """agent 模式：真实调度链路（fake caller），BYOK 路由打桩。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        with session_scope(self.db_url) as session:
            world = session.get(World, "sp-world")
            world.metadata_json = {**(world.metadata_json or {}), "keeper_mode": "agent"}
        self.gateway = StructuredGateway(self.db_url)
        self._patches = [
            patch(
                "src.storage.model_config_store.room_route_resolver",
                lambda *args: lambda: None,
            ),
            patch("src.ai.model.route_service.resolve_routes", lambda *args: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._temp.cleanup()

    async def _drain(self, world_id: str = "sp-world"):
        from src.structured.agent_runtime import _active_runs

        task = _active_runs.get(world_id)
        if task is not None:
            await task

    async def test_agent_mode_runs_and_resolves_request(self):
        decision = json.dumps(
            {
                "assessment": "自由叙述，直接回应",
                "commands": [
                    {
                        "kind": "resolve_intent",
                        "payload": {"request_id": "req-1", "resolution": "completed"},
                    }
                ],
                "narration": "你注意到壁炉后的暗格。",
                "wait_for_player": True,
            },
            ensure_ascii=False,
        )
        with patch(
            "src.structured.agent_runtime.build_byok_caller",
            lambda *args: _ScriptedCaller([decision]),
        ):
            box: list[dict] = []
            await self.gateway.handle_frame(
                world_id="sp-world",
                user_id="u-alice",
                frame=_action_frame(revision=_revision(self.db_url)),
                deliver=_deliver(box),
            )
            await self._drain()
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == "req-1",
                )
            ).scalar_one()
            self.assertEqual("completed", row.status)
        types = [e["type"] for e in box]
        self.assertIn("message_completed", types)
        self.assertIn("action_status", types)

    async def test_agent_mode_byok_missing_pauses_request(self):
        # BYOK 未配置：预检抛错 → 绑定 agent 控制 + 触发请求 paused
        for p in self._patches:
            p.stop()
        self._patches = [
            patch(
                "src.storage.model_config_store.room_route_resolver",
                side_effect=RuntimeError("未配置模型服务"),
            )
        ]
        self._patches[0].start()
        box: list[dict] = []
        await self.gateway.handle_frame(
            world_id="sp-world",
            user_id="u-alice",
            frame=_action_frame(revision=_revision(self.db_url)),
            deliver=_deliver(box),
        )
        await self._drain()
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == "req-1",
                )
            ).scalar_one()
            self.assertEqual("paused", row.status)
            self.assertIn("BYOK", row.detail)

    async def test_human_in_control_skips_unavailable_pause(self):
        with session_scope(self.db_url) as session:
            take_control(session, "sp-world", Principal(kind="keeper", user_id="u-keeper"))
        for p in self._patches:
            p.stop()
        self._patches = [
            patch(
                "src.storage.model_config_store.room_route_resolver",
                side_effect=RuntimeError("未配置模型服务"),
            )
        ]
        self._patches[0].start()
        box: list[dict] = []
        await self.gateway.handle_frame(
            world_id="sp-world",
            user_id="u-alice",
            frame=_action_frame(revision=_revision(self.db_url)),
            deliver=_deliver(box),
        )
        await self._drain()
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == "req-1",
                )
            ).scalar_one()
            # 人类在控：请求保持 queued 由人类处理，不被 agent 抢占置 paused
            self.assertEqual("queued", row.status)


class RoomAgentBroadcastTests(unittest.IsolatedAsyncioTestCase):
    """房间场景：agent 事件不排除发起连接，仍按 principal 过滤。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url

    def tearDown(self):
        self._temp.cleanup()

    async def test_agent_broadcast_includes_origin(self):
        from src.structured.room_integration import handle_room_structured_frame

        hub = _FakeHub(
            [
                {"connection_id": "c-alice", "user_id": "u-alice", "role": "player", "last_ack": 0},
                {"connection_id": "c-bob", "user_id": "u-bob", "role": "player", "last_ack": 0},
            ]
        )
        room = _FakeRoom(hub)
        controller = _FakeController(self.db_url)
        captured: dict = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return False  # 不真正调度（本测试只验投递闭包）

        with patch("src.structured.agent_runtime.maybe_schedule_keeper_agent", capture):
            await handle_room_structured_frame(
                controller,
                room,
                _FakeWs(),
                _FakeUser("u-alice"),
                "sp-world",
                "c-alice",
                _action_frame(revision=_revision(self.db_url)),
            )
        self.assertIn("broadcast", captured)
        self.assertIsNone(captured["deliver"])  # 房间场景 agent 不经 deliver
        # agent 广播：发起方 c-alice 也收到公共事件
        await captured["broadcast"](
            {
                "type": "message_completed",
                "payload": {"text": "agent 叙事"},
                "audience": {"kind": "public"},
            }
        )
        received = {cid for cid, _env in hub.direct}
        self.assertEqual({"c-alice", "c-bob"}, received)
        # 定向事件仍按 principal 过滤：keeper 向事件两名玩家都收不到
        hub.direct.clear()
        await captured["broadcast"](
            {
                "type": "keeper_draft",
                "payload": {"draft_id": "d-1"},
                "audience": {"kind": "keeper"},
            }
        )
        self.assertEqual([], hub.direct)


if __name__ == "__main__":
    unittest.main()
