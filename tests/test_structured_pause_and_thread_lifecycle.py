"""暂停/错误回帧与交互线程生命周期的收口测试（遗留修复）。

钉住的性质：
- 模型/执行失败不会让客户維永久等待：缺 BYOK 的降级路径把 action_status{paused}
  真正投递给连接（此前提交成功但事件被丢弃）；
- 暂停原因（detail）随快照投影，刷新后能看到可操作说明；
- 普通连接错误不依赖不存在世界的 outbox：世界不存在时错误以合成信封回帧，
  不抛未处理异常、不写库；
- 线程生命周期：回答追问不结束原交互；意图落实/明确取消关闭正确线程；
  不误关其他玩家/其他行动；重试幂等。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import (
    InteractionThread,
    PlayerRequest,
    session_scope,
)
from src.structured.gateway import StructuredGateway
from src.structured.interactions import list_open_threads
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService


class PauseAndErrorFrameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        self.service = StructuredPlayService(self.db_url)
        self.alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.keeper = Principal(kind="keeper", user_id="u-keeper")

    def tearDown(self):
        self._temp.cleanup()

    def _submit(self, request_id: str = "req-1") -> None:
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": request_id,
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "我去图书馆。"},
            },
        )

    async def test_missing_byok_pauses_and_delivers_frame(self):
        """agent 世界缺 BYOK：请求置 paused，且 action_status{paused} 回帧给连接。"""
        from src.structured.agent_runtime import _run_keeper_agent

        # 切到 agent 模式（无 BYOK 配置 → resolve_routes fail-closed）。
        with session_scope(self.db_url) as session:
            from src.storage.database import World

            world = session.get(World, "sp-world")
            metadata = dict(world.metadata_json or {})
            metadata["keeper_mode"] = "agent"
            world.metadata_json = metadata
        self._submit()
        delivered: list[dict] = []

        async def _deliver(envelope):
            delivered.append(envelope)

        await _run_keeper_agent(
            database_url=self.db_url,
            world_id="sp-world",
            keeper_mode="agent",
            trigger_request_id="req-1",
            deliver=_deliver,
            broadcast=None,
        )
        paused_frames = [
            e
            for e in delivered
            if e.get("type") == "action_status" and (e.get("payload") or {}).get("status") == "paused"
        ]
        self.assertEqual(1, len(paused_frames), f"暂停必须回帧：{delivered}")
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == "req-1",
                )
            ).scalar_one()
            self.assertEqual("paused", row.status)
            self.assertIn("BYOK", row.detail)
        # 快照投影带 detail：刷新后能看到可操作原因。
        snapshot = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        entry = [r for r in snapshot["requests"] if r["request_id"] == "req-1"][0]
        self.assertEqual("paused", entry["status"])
        self.assertIn("BYOK", entry.get("detail", ""))

    async def test_unknown_world_error_does_not_depend_on_outbox(self):
        """世界不存在：错误以合成信封回帧（event_id=0），不落库、不抛出。"""
        gateway = StructuredGateway(self.db_url)
        delivered: list[dict] = []

        async def _deliver(envelope):
            delivered.append(envelope)

        await gateway.handle_frame(
            world_id="world-missing",
            user_id=None,
            frame={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": "world-missing",
                "request_id": "req-x",
                "investigator_id": "inv-alice",
                "expected_revision": 0,
                "action": {"kind": "freeform", "text": "你好"},
            },
            deliver=_deliver,
        )
        errors = [e for e in delivered if e.get("type") == "request_error"]
        self.assertEqual(1, len(errors), delivered)
        self.assertEqual("unknown_world", errors[0]["payload"]["code"])
        self.assertEqual(0, errors[0]["event_id"], "合成信封不占事件序列")
        with session_scope(self.db_url) as session:
            from src.storage.database import EventOutbox

            count = session.execute(
                select(EventOutbox).where(EventOutbox.world_id == "world-missing")
            ).scalars().all()
            self.assertEqual([], count, "不存在的世界不产生 outbox 行")


class ThreadLifecycleCloseTests(unittest.IsolatedAsyncioTestCase):
    """线程收尾的状态联动：落实/取消关闭正确线程，追问/未落实不动。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        self.service = StructuredPlayService(self.db_url)
        self.alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
        self.keeper = Principal(kind="keeper", user_id="u-keeper")

    def tearDown(self):
        self._temp.cleanup()

    def _submit(self, request_id: str, text: str, principal: Principal | None = None) -> None:
        principal = principal or self.alice
        self.service.submit_action_request(
            world_id="sp-world",
            principal=principal,
            request={
                "request_id": request_id,
                "investigator_id": principal.investigator_ids[0],
                "action": {"kind": "freeform", "text": text},
            },
        )

    def _park(self, request_id: str, destination: str = "library") -> str:
        outcome = self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={
                "request_id": request_id,
                "resolution": "awaiting_player",
                "pending_action": {"kind": "move", "destination_scene_id": destination},
            },
            command_id=f"park-{request_id}",
            expected_revision=None,
        )
        return outcome["result"]["awaiting"]["thread_id"]

    def _resolve(self, command_id: str, request_id: str, resolution: str, **extra) -> dict:
        return self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={"request_id": request_id, "resolution": resolution, **extra},
            command_id=command_id,
            expected_revision=None,
        )

    def _thread(self, thread_id: str) -> InteractionThread:
        with session_scope(self.db_url) as session:
            return session.execute(
                select(InteractionThread).where(
                    InteractionThread.world_id == "sp-world",
                    InteractionThread.thread_id == thread_id,
                )
            ).scalar_one()

    def _open(self):
        with session_scope(self.db_url) as session:
            return list(list_open_threads(session, "sp-world"))

    async def test_fulfilled_origin_closes_thread(self):
        self._submit("req-1", "去图书馆。")
        thread_id = self._park("req-1")
        outcome = self._resolve("cmd-done", "req-1", "completed", outcome="success")
        self.assertEqual("completed", self._thread(thread_id).status)
        events = [e for e in outcome["events"] if e["type"] == "interaction_updated"]
        self.assertEqual(1, len(events))
        self.assertEqual("completed", events[0]["payload"]["status"])
        # 快照/数据库一致：开放列表为空。
        snapshot = self.service.session_snapshot(world_id="sp-world", principal=self.keeper)
        self.assertEqual([], snapshot["interactions"])
        # 重试幂等：同 command_id 重发不重复收尾、不重复事件。
        again = self._resolve("cmd-done", "req-1", "completed", outcome="success")
        self.assertTrue(again.get("deduplicated"))
        self.assertEqual([], again.get("events") or [])
        self.assertEqual("completed", self._thread(thread_id).status)

    async def test_unfulfilled_or_declined_keeps_thread(self):
        self._submit("req-1", "去图书馆。")
        thread_id = self._park("req-1")
        self._resolve("cmd-ne", "req-1", "completed", outcome="not_executed")
        self.assertEqual("open", self._thread(thread_id).status, "未落实不等于交互结束")

        self._submit("req-2", "能现在去吗？")
        thread_2 = self._park("req-2")
        self._resolve("cmd-dec", "req-2", "declined")
        self.assertEqual("open", self._thread(thread_2).status, "拒绝本次不等于取消目标")

    async def test_cancel_origin_closes_thread_cancelled(self):
        self._submit("req-1", "去图书馆。")
        thread_id = self._park("req-1")
        self._resolve("cmd-cancel", "req-1", "cancelled")
        self.assertEqual("cancelled", self._thread(thread_id).status)

    async def test_follow_up_resolution_does_not_close_origin_thread(self):
        self._submit("req-1", "去图书馆。")
        thread_id = self._park("req-1")
        self._submit("req-2", "图书馆现在开门吗？")
        # 追问即使 completed+success，也不是原意图的落实。
        self._resolve("cmd-ans", "req-2", "completed", outcome="success")
        self.assertEqual("open", self._thread(thread_id).status)

    async def test_other_investigators_thread_untouched(self):
        self._submit("req-a", "去图书馆。")
        alice_thread = self._park("req-a")
        self._submit("req-b", "我也去。", principal=self.bob)
        bob_thread = self._park("req-b")
        self._resolve("cmd-a-done", "req-a", "completed", outcome="success")
        self.assertEqual("completed", self._thread(alice_thread).status)
        self.assertEqual("open", self._thread(bob_thread).status, "不误关其他玩家的交互")

    async def test_player_cancel_followup_does_not_cancel_thread(self):
        self._submit("req-1", "去图书馆。")
        thread_id = self._park("req-1")
        self._submit("req-2", "等等我再想想。")
        # 玩家取消的是追问（queued），线程是 req-1 发起的——不得联动取消。
        self.service.cancel_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={"request_id": "cx-1", "target_request_id": "req-2"},
        )
        self.assertEqual("open", self._thread(thread_id).status)


if __name__ == "__main__":
    unittest.main()
