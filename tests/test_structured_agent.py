"""M3 Keeper Agent 运行器与 assisted 草稿（主规格 §7 / 背景 §10 P3）。

验收约束：
- 判断 → 命令/叙事 → 读已提交结果 → 继续/等待；命令逐条短事务提交。
- 模型失败/预算耗尽：触发请求 paused，已提交命令保留（独立连接验证），
  不重掷、不重扣、不重移动。
- 人类接管后旧 agent 运行的下一条命令 controller_epoch_stale，运行停止。
- assisted：同一模型调用只产出 keeper_draft 草稿，不执行命令；
  批准/拒绝由 resolve_draft 收尾。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import (
    EventOutbox,
    PlayerRequest,
    session_scope,
)
from src.storage.database_store import DatabaseWorldStore
from src.structured.agent import AgentBudget, KeeperAgentRunner
from src.structured.errors import StructuredError
from src.structured.principal import Principal, take_control
from src.structured.service import StructuredPlayService


class _ScriptedCaller:
    """按脚本逐次返回模型输出；callable 内可嵌副作用（如人类接管）。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append(user)
        if not self._script:
            raise RuntimeError("脚本耗尽")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item()
        return item


def _decision(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


def _collector(box: list):
    async def deliver(envelope: dict) -> None:
        box.append(envelope)

    return deliver


class KeeperAgentTests(unittest.IsolatedAsyncioTestCase):
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

    # --------------------------------------------------------------
    # 工具
    # --------------------------------------------------------------

    def persisted(self):
        store = DatabaseWorldStore(self.db_url, "sp-world", self.context.world_dir)
        snapshot = store.snapshot()
        return snapshot.state, snapshot.revision

    def request_status(self, request_id: str) -> str:
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == request_id,
                )
            ).scalar_one()
            return str(row.status)

    def submit_action(self, request_id: str = "req-1") -> None:
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": request_id,
                "investigator_id": "inv-alice",
                "action": {"kind": "move", "destination_scene_id": "library"},
            },
        )

    # --------------------------------------------------------------
    # agent 全流程
    # --------------------------------------------------------------

    async def test_agent_full_loop_commits_and_waits(self):
        self.submit_action()
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment="玩家明确前往图书馆",
                    commands=[
                        {
                            "kind": "move_party",
                            "payload": {"destination_scene_id": "library", "travel_minutes": 20},
                        },
                        {
                            "kind": "resolve_intent",
                            "payload": {
                                "request_id": "req-1",
                                "resolution": "completed",
                                "outcome": "success",
                            },
                        },
                    ],
                    narration="你们推开图书馆的门。",
                    wait_for_player=True,
                )
            ]
        )
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        delivered: list[dict] = []
        result = await runner.run(
            world_id="sp-world", trigger_request_id="req-1", deliver=_collector(delivered)
        )
        self.assertEqual("done", result.status)
        self.assertEqual("wait_player", result.stop_reason)
        self.assertEqual(3, result.commands_committed)  # move + resolve + 叙述
        state, _revision = self.persisted()
        self.assertEqual("library", state["current_scene"]["id"])
        self.assertEqual(20, state["world_clock"]["elapsed_minutes"])
        self.assertEqual("completed", self.request_status("req-1"))
        # 事件上线信封不含 audience；scene_changed 与 action_status 都已投递
        types = [e["type"] for e in delivered]
        self.assertIn("scene_changed", types)
        self.assertIn("action_status", types)
        self.assertIn("message_completed", types)
        for envelope in delivered:
            self.assertNotIn("audience", envelope)

    async def test_agent_model_failure_pauses_trigger_and_keeps_commits(self):
        self.submit_action()
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment="先移动",
                    commands=[
                        {
                            "kind": "move_party",
                            "payload": {"destination_scene_id": "library", "travel_minutes": 20},
                        }
                    ],
                ),
                RuntimeError("模型超时"),
            ]
        )
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run(world_id="sp-world", trigger_request_id="req-1")
        self.assertEqual("paused", result.status)
        self.assertTrue(result.stop_reason.startswith("model_error:"))
        # 已提交的移动不回滚（独立连接验证）
        state, _ = self.persisted()
        self.assertEqual("library", state["current_scene"]["id"])
        # 触发请求被置 paused，可恢复，不静默兜底
        self.assertEqual("paused", self.request_status("req-1"))

    async def test_human_takeover_stops_agent_run(self):
        self.submit_action()

        def takeover_then_decide():
            with session_scope(self.db_url) as session:
                take_control(session, "sp-world", self.keeper)
            return _decision(
                assessment="继续",
                commands=[
                    {
                        "kind": "move_party",
                        "payload": {"destination_scene_id": "library", "travel_minutes": 20},
                    }
                ],
            )

        caller = _ScriptedCaller([takeover_then_decide])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run(world_id="sp-world", trigger_request_id="req-1")
        self.assertEqual("takeover_stopped", result.status)
        self.assertEqual("controller_epoch_stale", result.stop_reason)
        self.assertEqual(0, result.commands_committed)
        state, _ = self.persisted()
        self.assertEqual("study", state["current_scene"]["id"])  # 未移动

    async def test_human_in_control_blocks_run_start(self):
        with session_scope(self.db_url) as session:
            take_control(session, "sp-world", self.keeper)
        caller = _ScriptedCaller([])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run(world_id="sp-world")
        self.assertEqual("blocked", result.status)
        self.assertEqual("human_in_control", result.stop_reason)
        self.assertEqual(0, len(caller.calls))

    async def test_budget_exhaustion_pauses_trigger(self):
        self.submit_action()
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment=f"步骤{i}",
                    commands=[{"kind": "record_fact", "payload": {"text": f"fact-{i}"}}],
                )
                for i in range(3)
            ]
        )
        runner = KeeperAgentRunner(
            self.db_url, caller=caller, budget=AgentBudget(max_model_calls=2)
        )
        result = await runner.run(world_id="sp-world", trigger_request_id="req-1")
        self.assertEqual("paused", result.status)
        self.assertEqual("budget_exceeded", result.stop_reason)
        self.assertEqual(2, result.model_calls)
        self.assertEqual("paused", self.request_status("req-1"))

    async def test_no_progress_stops_without_pause(self):
        self.submit_action()
        caller = _ScriptedCaller([_decision(assessment="没有需要处理的事")])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run(world_id="sp-world", trigger_request_id="req-1")
        self.assertEqual("done", result.status)
        self.assertEqual("no_progress", result.stop_reason)
        self.assertEqual("queued", self.request_status("req-1"))

    async def test_unparseable_decision_pauses_after_retries(self):
        self.submit_action()
        caller = _ScriptedCaller(["这不是 JSON", "```json\n{半截\n```"])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run(world_id="sp-world", trigger_request_id="req-1")
        self.assertEqual("paused", result.status)
        self.assertEqual("unparseable_decision", result.stop_reason)
        self.assertEqual(2, result.model_calls)
        self.assertEqual("paused", self.request_status("req-1"))

    # --------------------------------------------------------------
    # assisted 草稿
    # --------------------------------------------------------------

    async def test_assisted_produces_draft_without_executing(self):
        self.submit_action()
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment="建议先移动到图书馆",
                    commands=[
                        {
                            "kind": "move_party",
                            "payload": {"destination_scene_id": "library", "travel_minutes": 20},
                        }
                    ],
                    narration="你们来到图书馆。",
                )
            ]
        )
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        delivered: list[dict] = []
        result = await runner.run_assisted(
            world_id="sp-world", trigger_request_id="req-1", deliver=_collector(delivered)
        )
        self.assertEqual("draft_ready", result.stop_reason)
        self.assertEqual(0, result.commands_committed)
        # 世界状态不变：草稿不执行任何命令
        state, _ = self.persisted()
        self.assertEqual("study", state["current_scene"]["id"])
        # 草稿行落库且排队
        with session_scope(self.db_url) as session:
            draft = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_type == "keeper_draft",
                )
            ).scalar_one()
            self.assertEqual("queued", draft.status)
            self.assertEqual("move_party", draft.payload["draft"]["commands"][0]["kind"])
        # keeper_draft 事件 audience 为 keeper
        with session_scope(self.db_url) as session:
            event = session.execute(
                select(EventOutbox).where(
                    EventOutbox.world_id == "sp-world",
                    EventOutbox.event_type == "keeper_draft",
                )
            ).scalar_one()
            self.assertEqual({"kind": "keeper"}, event.audience)
        self.assertEqual(["keeper_draft"], [e["type"] for e in delivered])

    async def test_assisted_model_failure_pauses_without_draft(self):
        caller = _ScriptedCaller([RuntimeError("不可用")])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run_assisted(world_id="sp-world")
        self.assertEqual("paused", result.status)
        self.assertTrue(result.stop_reason.startswith("draft_unavailable:"))
        with session_scope(self.db_url) as session:
            count = (
                session.execute(
                    select(PlayerRequest).where(
                        PlayerRequest.world_id == "sp-world",
                        PlayerRequest.request_type == "keeper_draft",
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual([], count)

    # --------------------------------------------------------------
    # resolve_draft 收尾
    # --------------------------------------------------------------

    def _make_draft(self) -> str:
        draft = self.service.create_keeper_draft(
            world_id="sp-world",
            summary="建议",
            proposed_commands=[],
            narration="",
        )
        return draft["draft_id"]

    def _resolve_draft(self, draft_id: str, decision: str, note: str = "", attempt: int = 0):
        _state, revision = self.persisted()
        return self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_draft",
            payload={"draft_id": draft_id, "decision": decision, "note": note},
            command_id=f"cmd-resolve-{draft_id}-{attempt}",
            expected_revision=revision,
        )

    def test_resolve_draft_approved_and_rejected(self):
        draft_a = self._make_draft()
        draft_b = self._make_draft()
        result = self._resolve_draft(draft_a, "approved")
        self.assertEqual("success", result["result"]["status"])
        self.assertEqual("completed", self.request_status(draft_a))
        result = self._resolve_draft(draft_b, "rejected", note="不合适")
        self.assertEqual("declined", self.request_status(draft_b))
        # 终态草稿不能重复收尾（用新 command_id，避免幂等重放掩盖）
        with self.assertRaises(StructuredError) as raised:
            self._resolve_draft(draft_a, "approved", attempt=1)
        self.assertEqual("invalid_action", raised.exception.code)
        # 未知草稿
        with self.assertRaises(StructuredError) as raised:
            self._resolve_draft("draft-不存在", "approved")
        self.assertEqual("request_not_found", raised.exception.code)

    def test_resolve_draft_rejects_bad_decision(self):
        draft_id = self._make_draft()
        with self.assertRaises(StructuredError) as raised:
            self._resolve_draft(draft_id, "maybe")
        self.assertEqual("invalid_action", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
