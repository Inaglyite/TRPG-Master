"""当前交互线程（第 2 层上下文）的确定性测试。

钉住的性质：
1. awaiting_player 自动开线程；请求终态后线程仍可保留（S3 根因修复）；
2. 追问/坚持通过显式 thread 操作关联回同一交互；取消/改目的地替换旧线程；
3. 抵达目的地自动收尾对应移动线程（记录收尾，不是线程触发移动）；
4. 快照/上下文投影按身份过滤；候选绑定是状态匹配，不做文本推断；
5. 线程独立于消息历史存活（历史压缩不丢当前交互）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import InteractionThread, session_scope
from src.storage.database_store import DatabaseWorldStore
from src.structured.agent import KeeperAgentRunner
from src.structured.errors import StructuredError
from src.structured.interactions import list_open_threads
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService


def _decision(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


class _ScriptedCaller:
    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append(user)
        if not self._script:
            raise RuntimeError("脚本耗尽")
        return self._script.pop(0)


class InteractionContextTests(unittest.IsolatedAsyncioTestCase):
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

    # ---------------------------------------------------------------- 工具

    def state(self) -> dict:
        return DatabaseWorldStore(self.db_url, "sp-world", self.context.world_dir).snapshot().state

    def scene_id(self) -> str:
        return str((self.state().get("current_scene") or {}).get("id") or "")

    def submit(self, request_id: str, action: dict, principal: Principal | None = None) -> None:
        principal = principal or self.alice
        self.service.submit_action_request(
            world_id="sp-world",
            principal=principal,
            request={
                "request_id": request_id,
                "investigator_id": principal.investigator_ids[0],
                "action": action,
            },
        )

    def resolve(self, command_id: str, request_id: str, resolution: str, **extra) -> dict:
        return self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={"request_id": request_id, "resolution": resolution, **extra},
            command_id=command_id,
            expected_revision=None,
        )

    def open_threads(self) -> list[InteractionThread]:
        with session_scope(self.db_url) as session:
            return list(list_open_threads(session, "sp-world"))

    def thread(self, thread_id: str) -> InteractionThread | None:
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(InteractionThread).where(
                    InteractionThread.world_id == "sp-world",
                    InteractionThread.thread_id == thread_id,
                )
            ).scalar_one_or_none()
            return row

    def release_control(self) -> None:
        """把控制权交还「无人掌控」，让 Agent runner 能取得（测试 setup 里
        的主持命令会自动占用人类控制权）。"""
        from src.structured.principal import current_control

        with session_scope(self.db_url) as session:
            control = current_control(session, "sp-world")
            control.controller_kind = "none"
            control.controller_id = ""

    # ------------------------------------------------- 1) 挂起自动开线程，终态不丢

    async def test_awaiting_opens_thread_and_terminal_resolution_keeps_it(self):
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看莱特教授的尸体。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={
                "kind": "move",
                "destination_scene_id": "library",
                "note": "尚未出发前往图书馆",
            },
            disclosed=["图书馆周末闭馆"],
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        self.assertTrue(thread_id)
        threads = self.open_threads()
        self.assertEqual(1, len(threads))
        self.assertEqual("library", threads[0].pending_action["destination_scene_id"])
        self.assertEqual("req-1", threads[0].origin_request_id)

        # S3 根因：追问请求被收尾（completed/declined）后，「已讨论的目标」不能丢。
        self.submit("req-2", {"kind": "freeform", "text": "那位医生和我们熟吗？"})
        self.resolve("cmd-2", "req-2", "completed", outcome="success")
        threads = self.open_threads()
        self.assertEqual(1, len(threads), "追问收尾后线程仍应存在")
        self.assertEqual(thread_id, threads[0].thread_id)

    # ------------------------------------------------- 2) 追问显式延续同一交互

    async def test_follow_up_continues_same_thread(self):
        self.submit("req-1", {"kind": "freeform", "text": "我想去看看图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        self.submit("req-2", {"kind": "freeform", "text": "那就过去吧。"})
        outcome = self.resolve(
            "cmd-2",
            "req-2",
            "completed",
            outcome="success",
            thread={"action": "continue", "thread_id": thread_id},
        )
        thread = outcome["result"]["thread"]
        self.assertEqual(thread_id, thread["thread_id"])
        row = self.thread(thread_id)
        self.assertEqual("req-2", row.last_request_id)
        self.assertEqual(["req-1", "req-2"], list(row.request_ids))

    # ------------------------------------------------- 3) 改主意替换；旧线程不执行

    async def test_change_of_plan_replaces_thread_and_never_executes(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        old_thread = outcome["result"]["awaiting"]["thread_id"]
        self.submit("req-2", {"kind": "freeform", "text": "算了，先不去图书馆。"})
        outcome = self.resolve(
            "cmd-2",
            "req-2",
            "completed",
            outcome="not_executed",
            thread={
                "action": "replace",
                "thread_id": old_thread,
                "pending_action": {"kind": "freeform", "note": "留在书房继续问"},
            },
        )
        old = self.thread(old_thread)
        self.assertEqual("superseded", old.status)
        new_thread = outcome["result"]["thread"]
        self.assertEqual("open", new_thread["status"])
        self.assertNotEqual(old_thread, new_thread["thread_id"])
        # 结构性保证：线程更替不移动任何人。
        self.assertEqual("study", self.scene_id())

    async def test_player_cancel_cancels_linked_thread(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        # 等待中的请求已被主持接管，玩家不能直接 cancel（协议既有规则）；
        # 这里验证 queued 请求的取消会联动取消其关联线程。
        self.submit("req-2", {"kind": "freeform", "text": "再想想。"})
        self.resolve(
            "cmd-3",
            "req-2",
            "awaiting_player",
            pending_action={"kind": "other", "note": "考虑中"},
        )
        # req-2 现在 awaiting（接管后），用 queued 的 req-2 取消路径不可行；
        # 直接验证 cancel_threads_for_request 的领域语义：origin/last 命中即取消。
        from src.structured.interactions import cancel_threads_for_request

        with session_scope(self.db_url) as session:
            events = cancel_threads_for_request(session, "sp-world", request_id="req-2", revision=1)
        self.assertTrue(events)
        statuses = {t.thread_id: t.status for t in self.open_threads()}
        self.assertIn(thread_id, statuses, "无关线程不受影响")
        self.assertEqual(0, len([t for t in self.open_threads() if t.thread_id != thread_id]))

    # ------------------------------------------------- 4) 抵达收尾移动线程

    async def test_arrival_completes_matching_move_thread_only(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload={"destination_scene_id": "library"},
            command_id="cmd-move-1",
            expected_revision=None,
        )
        row = self.thread(thread_id)
        self.assertEqual("completed", row.status)
        self.assertEqual("library", self.scene_id())

    async def test_move_to_other_scene_does_not_close_thread(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        # 先去图书馆再回书房：回程移动不得把「前往图书馆」的线程再碰一次。
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload={"destination_scene_id": "library"},
            command_id="cmd-move-1",
            expected_revision=None,
        )
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload={"destination_scene_id": "study"},
            command_id="cmd-move-2",
            expected_revision=None,
        )
        self.assertEqual("completed", self.thread(thread_id).status)

    # ------------------------------------------------- 5) 投影与可见性

    async def test_snapshot_interactions_visible_to_owner_and_keeper_only(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
            disclosed=["图书馆周末闭馆"],
        )
        keeper_view = self.service.session_snapshot(world_id="sp-world", principal=self.keeper)
        alice_view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        bob_view = self.service.session_snapshot(world_id="sp-world", principal=self.bob)
        self.assertEqual(1, len(keeper_view["interactions"]))
        self.assertEqual(1, len(alice_view["interactions"]))
        self.assertEqual("图书馆周末闭馆", alice_view["interactions"][0]["disclosed"][0])
        self.assertEqual([], bob_view["interactions"], "其他玩家看不到别人的交互线程")

    async def test_interaction_updated_event_audience(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        interaction_events = [e for e in outcome["events"] if e["type"] == "interaction_updated"]
        self.assertEqual(1, len(interaction_events))
        self.assertEqual(
            {"kind": "investigators", "investigator_ids": ["inv-alice"]},
            interaction_events[0]["audience"],
        )

    # ------------------------------------------------- 6) 上下文候选绑定（状态匹配）

    async def test_agent_context_carries_threads_and_explicit_candidates(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        self.submit("req-2", {"kind": "freeform", "text": "那就过去吧。"})
        self.release_control()
        caller = _ScriptedCaller([_decision(narration="好。", stop_reason="done", commands=[])])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        await runner.run(world_id="sp-world", trigger_request_id="req-2")
        context = json.loads(caller.calls[0])
        self.assertEqual("req-2", context["trigger_context"]["request_id"])
        self.assertEqual([thread_id], context["trigger_context"]["candidate_thread_ids"])
        self.assertTrue(context["trigger_context"]["has_open_thread"])
        thread_block = context["open_threads"][0]
        self.assertEqual("library", thread_block["pending_action"]["destination_scene_id"])
        self.assertTrue(thread_block["candidate_for_trigger"])

    async def test_agent_context_explicit_no_thread(self):
        self.submit("req-9", {"kind": "freeform", "text": "你好。"})
        self.release_control()
        caller = _ScriptedCaller([_decision(narration="嗯。", stop_reason="done", commands=[])])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        await runner.run(world_id="sp-world", trigger_request_id="req-9")
        context = json.loads(caller.calls[0])
        self.assertEqual([], context["trigger_context"]["candidate_thread_ids"])
        self.assertFalse(context["trigger_context"]["has_open_thread"])
        self.assertEqual([], context["open_threads"])

    # ------------------------------------------------- 7) 历史长度不挤掉当前交互

    async def test_threads_survive_history_growth(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        outcome = self.resolve(
            "cmd-1",
            "req-1",
            "awaiting_player",
            pending_action={"kind": "move", "destination_scene_id": "library"},
        )
        thread_id = outcome["result"]["awaiting"]["thread_id"]
        for index in range(12):
            self.service.execute_command(
                world_id="sp-world",
                principal=self.keeper,
                kind="publish_message",
                payload={
                    "speaker": {"kind": "keeper"},
                    "audience": {"kind": "public"},
                    "text": f"第 {index} 条与交互无关的闲聊。",
                },
                command_id=f"cmd-chat-{index}",
                expected_revision=None,
            )
        self.submit("req-2", {"kind": "freeform", "text": "那就过去。"})
        self.release_control()
        caller = _ScriptedCaller([_decision(narration="走。", stop_reason="done", commands=[])])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        await runner.run(world_id="sp-world", trigger_request_id="req-2")
        context = json.loads(caller.calls[0])
        self.assertEqual(8, len(context["recent_public_messages"]), "近期对话有截断上限")
        self.assertEqual(
            [thread_id],
            context["trigger_context"]["candidate_thread_ids"],
            "对话刷屏后当前交互仍在且候选绑定不丢",
        )

    # ------------------------------------------------- 8) 线程操作校验

    async def test_thread_action_validation(self):
        self.submit("req-1", {"kind": "freeform", "text": "去图书馆。"})
        with self.assertRaises(StructuredError) as caught:
            self.resolve(
                "cmd-bad-1",
                "req-1",
                "completed",
                thread={"action": "continue"},  # 缺 thread_id
            )
        self.assertEqual("invalid_action", caught.exception.code)
        with self.assertRaises(StructuredError) as caught:
            self.resolve(
                "cmd-bad-2",
                "req-1",
                "completed",
                thread={"action": "close", "thread_id": "thr_missing"},
            )
        self.assertEqual("thread_not_found", caught.exception.code)
        with self.assertRaises(StructuredError) as caught:
            self.resolve(
                "cmd-bad-3",
                "req-1",
                "completed",
                thread={"action": "open"},  # 缺 pending_action
            )
        self.assertEqual("invalid_action", caught.exception.code)


if __name__ == "__main__":
    unittest.main()


class InteractionThreadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """线程在分支/读档下的契约（与普通刷新区分开）。"""

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

    def _park(self) -> str:
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-1",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "去图书馆。"},
            },
        )
        outcome = self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={
                "request_id": "req-1",
                "resolution": "awaiting_player",
                "pending_action": {"kind": "move", "destination_scene_id": "library"},
            },
            command_id="cmd-1",
            expected_revision=None,
        )
        return outcome["result"]["awaiting"]["thread_id"]

    def _open_threads(self, world_id: str):
        with session_scope(self.db_url) as session:
            return list(list_open_threads(session, world_id))

    async def test_branch_carries_open_threads(self):
        from src.structured.branch import create_structured_branch

        thread_id = self._park()
        branch = create_structured_branch(
            self.context,
            project_root=self.root,
            runtime_root=self.root,
            label="分支",
        )
        branch_threads = self._open_threads(branch.context.world_id)
        self.assertEqual(1, len(branch_threads))
        self.assertEqual(thread_id, branch_threads[0].thread_id)
        self.assertEqual("library", branch_threads[0].pending_action["destination_scene_id"])

    async def test_restore_cancels_open_threads_and_drops_future_threads(self):
        from src.app.config import AUTO_SAVE_SLOT
        from src.storage.database import InteractionThread
        from src.storage.persistence import save_game
        from src.structured.branch import restore_structured_save

        thread_id = self._park()
        save_game([], AUTO_SAVE_SLOT, context=self.context)
        # 存档点之后又开了一条线程（属于被回滚的未来）。
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-2",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "再想想。"},
            },
        )
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={
                "request_id": "req-2",
                "resolution": "awaiting_player",
                "pending_action": {"kind": "other", "note": "考虑中"},
            },
            command_id="cmd-2",
            expected_revision=None,
        )
        self.assertEqual(2, len(self._open_threads("sp-world")))
        restore_structured_save(self.context, AUTO_SAVE_SLOT)
        with session_scope(self.db_url) as session:
            rows = {
                row.thread_id: row
                for row in session.execute(
                    select(InteractionThread).where(InteractionThread.world_id == "sp-world")
                )
                .scalars()
                .all()
            }
        self.assertEqual(1, len(rows), "存档点之后创建的线程被删除")
        self.assertIn(thread_id, rows)
        self.assertEqual("cancelled", rows[thread_id].status, "读档 fail-closed：开放线程一律作废")
