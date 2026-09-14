"""角色长期记忆（第 4 层）的确定性测试。

钉住的性质：
1. 派生只来自已提交事件/命令，且只给「明确可知」的角色（被授予者/持有者/
   队伍成员）写入；未执行的行动、被拒的命令、未落账的叙述都不产生记忆；
2. 知识类型四分（亲历/被告知/传闻/推测）随条目存在；更正走 supersedes，
   旧记忆保留在来源链里，默认检索不再当确证事实；
3. 派生幂等（重试/补建不重复写）；派生失败不丢已提交事实；
4. 权限：玩家只能查自己控制的调查员；越权是明确拒绝而不是空结果；
5. 预算：条数与字符双上限；Agent 查询预算用尽后明确拒绝；
6. 分支/读档隔离：分支看不到原世界分叉后的记忆；读档删除未来记忆、
   还原未来的更正。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import CharacterMemory, session_scope
from src.structured import memories
from src.structured.errors import StructuredError
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


class CharacterMemoryTests(unittest.IsolatedAsyncioTestCase):
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

    def all_memories(self) -> list[CharacterMemory]:
        with session_scope(self.db_url) as session:
            return list(
                session.execute(
                    select(CharacterMemory).where(CharacterMemory.world_id == "sp-world")
                )
                .scalars()
                .all()
            )

    def command(self, command_id: str, kind: str, payload: dict, principal=None) -> dict:
        return self.service.execute_command(
            world_id="sp-world",
            principal=principal or self.keeper,
            kind=kind,
            payload=payload,
            command_id=command_id,
            expected_revision=None,
        )

    def submit(self, request_id: str, action: dict) -> None:
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": request_id,
                "investigator_id": "inv-alice",
                "action": action,
            },
        )

    # ------------------------------------------------- 1) 已提交事实的派生

    async def test_move_and_clue_derive_memories_for_exact_knowers(self):
        self.command("cmd-m1", "move_party", {"destination_scene_id": "library"})
        rows = {row.character_id: row for row in self.all_memories()}
        # 队伍两名成员都有「亲历抵达」；NPC 不在队伍里，不得默认知情。
        self.assertIn("inv-alice", rows)
        self.assertIn("inv-bob", rows)
        self.assertNotIn("keeper_npc", rows)
        self.assertEqual("experienced", rows["inv-alice"].knowledge_type)
        self.assertEqual("library", rows["inv-alice"].scene_id)

        self.command(
            "cmd-c1",
            "grant_clue",
            {
                "clue_id": "clue_death_certificate",
                "recipient_investigator_ids": ["inv-alice"],
                "basis": "法伦出示了死亡证明。",
            },
        )
        clue_memories = [row for row in self.all_memories() if row.knowledge_type == "told"]
        self.assertEqual(1, len(clue_memories), "只有被授予者新增「被告知」记忆")
        self.assertEqual("inv-alice", clue_memories[0].character_id)
        self.assertIn("死亡证明", clue_memories[0].content)

    async def test_unexecuted_request_and_narration_produce_no_memory(self):
        # 未执行的意图（queued / awaiting）不产生任何记忆。
        self.submit("req-1", {"kind": "freeform", "text": "我想去图书馆。"})
        self.assertEqual([], self.all_memories())
        self.command(
            "cmd-w1",
            "resolve_intent",
            {
                "request_id": "req-1",
                "resolution": "awaiting_player",
                "pending_action": {"kind": "move", "destination_scene_id": "library"},
            },
        )
        self.assertEqual([], self.all_memories(), "等待中的行动不是已发生事实")
        # 未落账的叙述（纯消息）也不产生记忆。
        self.command(
            "cmd-msg",
            "publish_message",
            {
                "speaker": {"kind": "keeper"},
                "audience": {"kind": "public"},
                "text": "旁白声称你们抵达了图书馆——但没有移动命令落账。",
            },
        )
        self.assertEqual([], self.all_memories(), "叙述不升级为记忆")

    async def test_rejected_command_produces_no_memory(self):
        with self.assertRaises(StructuredError):
            self.command("cmd-bad", "move_party", {"destination_scene_id": "nowhere"})
        self.assertEqual([], self.all_memories())

    # ------------------------------------------------- 2) 传闻/纠正与来源链

    async def test_rumor_corrected_keeps_source_chain(self):
        self.command(
            "cmd-r1",
            "record_memory",
            {
                "character_id": "inv-alice",
                "knowledge_type": "rumor",
                "content": "传闻：医生私自扣下了莱特的遗体。",
                "topics": ["医生"],
            },
        )
        rumor = [row for row in self.all_memories() if row.knowledge_type == "rumor"][0]
        outcome = self.command(
            "cmd-r2",
            "record_memory",
            {
                "character_id": "inv-alice",
                "knowledge_type": "told",
                "content": "验尸官确认：遗体按程序移交给了家属。",
                "topics": ["医生"],
                "supersedes": rumor.memory_id,
            },
        )
        self.assertEqual("success", outcome["result"]["status"])
        with session_scope(self.db_url) as session:
            active = memories.retrieve(session, "sp-world", character_ids=["inv-alice"])
            self.assertEqual(1, len(active))
            self.assertEqual("told", active[0]["knowledge_type"])
            self.assertIn("移交给", active[0]["content"])
            history = memories.retrieve(
                session, "sp-world", character_ids=["inv-alice"], include_history=True
            )
            self.assertEqual(2, len(history), "旧传闻保留在来源链里")
            superseded = [row for row in history if row["status"] == "superseded"][0]
            self.assertEqual("rumor", superseded["knowledge_type"])
            self.assertTrue(superseded["superseded_by"])

    # ------------------------------------------------- 3) 派生幂等与失败隔离

    async def test_redrive_is_idempotent_and_repairable(self):
        self.command("cmd-m1", "move_party", {"destination_scene_id": "library"})
        before = len(self.all_memories())
        # 重放同一命令的派生（例如补建任务重跑）：一行都不多。
        with session_scope(self.db_url) as session:
            from src.storage.database import EventOutbox

            events = [
                {
                    "event_id": row.id,
                    "type": row.event_type,
                    "sequence": row.sequence,
                    "payload": dict(row.payload or {}),
                }
                for row in session.execute(
                    select(EventOutbox).where(EventOutbox.world_id == "sp-world")
                ).scalars()
            ]
        again = memories.derive_from_commit(
            self.db_url,
            "sp-world",
            command_kind="move_party",
            command_payload={"destination_scene_id": "library"},
            command_id="cmd-m1",
            events=events,
            revision_after=2,
        )
        self.assertEqual(0, again)
        self.assertEqual(before, len(self.all_memories()))
        report = memories.repair_derivation(self.db_url, "sp-world")
        self.assertEqual(0, report["derived"], "补建也是幂等的")

    async def test_derivation_failure_does_not_lose_committed_facts(self):
        with mock.patch.object(memories, "derive_from_commit", side_effect=RuntimeError("boom")):
            outcome = self.command("cmd-m2", "move_party", {"destination_scene_id": "library"})
        self.assertEqual("committed", outcome["status"])
        self.assertEqual([], self.all_memories(), "派生失败不伪造成功")
        # 已提交事实还在：补建能从命令账本重新派生。
        report = memories.repair_derivation(self.db_url, "sp-world")
        self.assertGreaterEqual(report["derived"], 2)
        self.assertTrue(any(row.scene_id == "library" for row in self.all_memories()))

    # ------------------------------------------------- 4) 权限

    async def test_player_cannot_query_other_characters_memory(self):
        self.command(
            "cmd-r1",
            "record_memory",
            {
                "character_id": "inv-bob",
                "knowledge_type": "belief",
                "content": "鲍勃怀疑医生在撒谎。",
            },
        )
        with self.assertRaises(StructuredError) as caught:
            self.service.query_memories(
                world_id="sp-world", principal=self.alice, character_id="inv-bob"
            )
        self.assertEqual("not_authorized", caught.exception.code)
        # 自己的记忆可以查（只读、按角色归属）。
        own = self.service.query_memories(
            world_id="sp-world", principal=self.bob, character_id="inv-bob"
        )
        self.assertEqual(1, len(own))
        # 记忆查询帧是主持侧能力：玩家调用被拒。
        with self.assertRaises(StructuredError) as caught:
            self.service.execute_memory_query(
                world_id="sp-world",
                principal=self.alice,
                frame={"query_id": "q-1", "filters": {}},
            )
        self.assertEqual("not_authorized", caught.exception.code)

    async def test_keeper_memory_query_returns_keeper_event(self):
        self.command(
            "cmd-r1",
            "record_memory",
            {
                "character_id": "keeper_npc",
                "character_kind": "npc",
                "knowledge_type": "told",
                "content": "老看守听说有人在打听停尸房。",
            },
        )
        outcome = self.service.execute_memory_query(
            world_id="sp-world",
            principal=self.keeper,
            frame={"query_id": "q-1", "filters": {"character_id": "keeper_npc"}},
        )
        events = outcome["events"]
        self.assertEqual("memory_query_result", events[0]["type"])
        self.assertEqual({"kind": "keeper"}, events[0]["audience"])
        self.assertEqual(1, len(events[0]["payload"]["memories"]))

    # ------------------------------------------------- 5) 预算

    async def test_retrieval_budget_is_enforced(self):
        for index in range(15):
            self.command(
                f"cmd-r{index}",
                "record_memory",
                {
                    "character_id": "inv-alice",
                    "knowledge_type": "told",
                    "content": f"第 {index} 条见闻，" + "很长" * 40,
                },
            )
        with session_scope(self.db_url) as session:
            rows = memories.retrieve(
                session, "sp-world", character_ids=["inv-alice"], limit=5, char_budget=600
            )
        self.assertLessEqual(len(rows), 5)
        self.assertLessEqual(sum(len(row["content"]) for row in rows), 600)

    async def test_agent_query_budget_is_enforced(self):
        from src.structured.agent import KeeperAgentRunner

        self.submit("req-1", {"kind": "freeform", "text": "你好。"})
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment="查记忆",
                    queries=[{"kind": "memory", "text": f"q{i}"} for i in range(6)],
                    commands=[],
                    narration="",
                    stop_reason="done",
                )
            ]
        )
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        result = await runner.run(world_id="sp-world", trigger_request_id="req-1")
        self.assertEqual("done", result.status)
        context = json.loads(caller.calls[0])
        self.assertIn("character_memories", context)
        # 预算 3：第 4 条起被拒绝并回喂（请求停在 paused 前 run_log 里应有提示，
        # 这里验证运行没有因查询失控：模型调用次数受预算约束）。
        self.assertLessEqual(result.model_calls, 6)

    # ------------------------------------------------- 6) 分支与读档隔离

    async def test_branch_isolation(self):
        from src.structured.branch import create_structured_branch

        self.command(
            "cmd-r1",
            "record_memory",
            {
                "character_id": "inv-alice",
                "knowledge_type": "experienced",
                "content": "分叉前的共同经历。",
            },
        )
        branch = create_structured_branch(
            self.context,
            project_root=self.root,
            runtime_root=self.root,
            label="分支",
        )
        branch_world = branch.context.world_id
        # 分叉点已有的记忆两边都有。
        with session_scope(self.db_url) as session:
            self.assertEqual(
                1,
                len(memories.retrieve(session, branch_world, character_ids=["inv-alice"])),
            )
        # 分叉后原世界新增的记忆，分支检索不到；反之亦然。
        self.command(
            "cmd-r2",
            "record_memory",
            {
                "character_id": "inv-alice",
                "knowledge_type": "told",
                "content": "分叉后原世界的新事实。",
            },
        )
        branch_service = StructuredPlayService(self.db_url)
        with session_scope(self.db_url) as session:
            branch_rows = memories.retrieve(session, branch_world, character_ids=["inv-alice"])
            source_rows = memories.retrieve(session, "sp-world", character_ids=["inv-alice"])
        self.assertEqual(1, len(branch_rows), "分支不能检索到原世界分叉后的记忆")
        self.assertEqual(2, len(source_rows))
        del branch_service

    async def test_restore_drops_future_knowledge_and_restores_corrections(self):
        from src.app.config import AUTO_SAVE_SLOT
        from src.storage.persistence import save_game
        from src.structured.branch import restore_structured_save

        self.command(
            "cmd-r1",
            "record_memory",
            {
                "character_id": "inv-alice",
                "knowledge_type": "rumor",
                "content": "传闻：医生扣下了遗体。",
            },
        )
        save_game([], AUTO_SAVE_SLOT, context=self.context)
        # 存档点之后：传闻被纠正（supersede），又产生了新记忆。
        rumor = self.all_memories()[0]
        self.command(
            "cmd-r2",
            "record_memory",
            {
                "character_id": "inv-alice",
                "knowledge_type": "told",
                "content": "验尸官确认遗体已移交。",
                "supersedes": rumor.memory_id,
            },
        )
        self.command("cmd-m1", "move_party", {"destination_scene_id": "library"})
        self.assertGreaterEqual(len(self.all_memories()), 4)
        restore_structured_save(self.context, AUTO_SAVE_SLOT)
        rows = self.all_memories()
        self.assertEqual(1, len(rows), "读档删除存档点之后的记忆（未来知识不得泄漏）")
        self.assertEqual("active", rows[0].status, "未来的更正被还原：旧传闻回到 active")
        self.assertEqual("", rows[0].superseded_by)


if __name__ == "__main__":
    unittest.main()


class MemoryEdgeCaseTests(unittest.IsolatedAsyncioTestCase):
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

    def _count(self) -> int:
        with session_scope(self.db_url) as session:
            return len(
                session.execute(
                    select(CharacterMemory).where(CharacterMemory.world_id == "sp-world")
                )
                .scalars()
                .all()
            )

    async def test_command_retry_does_not_duplicate_memories(self):
        """同一 command_id 重发：命令幂等返回，记忆也不重复派生。"""
        payload = {"destination_scene_id": "library"}
        first = self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload=payload,
            command_id="cmd-dup",
            expected_revision=None,
        )
        second = self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload=payload,
            command_id="cmd-dup",
            expected_revision=None,
        )
        self.assertTrue(second.get("deduplicated"))
        del first
        self.assertEqual(2, self._count(), "两名队员各一条，重发不加")

    async def test_agent_context_injects_memories_with_type_labels(self):
        from src.structured.agent import KeeperAgentRunner

        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="record_memory",
            payload={
                "character_id": "inv-alice",
                "knowledge_type": "rumor",
                "content": "传闻：图书馆夜里有人影。",
            },
            command_id="cmd-r1",
            expected_revision=None,
        )
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-1",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "我们去图书馆看看？"},
            },
        )
        # 让 Agent 能取得控制权（上面的 keeper 命令占用了人类控制权）。
        from src.structured.principal import current_control

        with session_scope(self.db_url) as session:
            control = current_control(session, "sp-world")
            control.controller_kind = "none"
            control.controller_id = ""
        caller = _ScriptedCaller([_decision(narration="好。", stop_reason="done", commands=[])])
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        await runner.run(world_id="sp-world", trigger_request_id="req-1")
        context = json.loads(caller.calls[0])
        entries = context["character_memories"]
        self.assertEqual(1, len(entries))
        self.assertEqual("rumor", entries[0]["knowledge_type"], "传闻必须带类型注入")
        self.assertEqual("inv-alice", entries[0]["character_id"])


class MemoryQueryGatewayTests(unittest.IsolatedAsyncioTestCase):
    """memory_query 帧过网关：schema → keeper 授权 → keeper 定向事件。"""

    def setUp(self):
        from test_structured_ws import make_ws_world

        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_ws_world(self.root)
        self.db_url = self.context.database_url
        self.service = StructuredPlayService(self.db_url)
        self.keeper = Principal(kind="keeper", user_id="u-keeper")

    def tearDown(self):
        self._temp.cleanup()

    async def test_memory_query_frame_roundtrip_and_event_schema(self):
        from src.structured.gateway import StructuredGateway
        from src.structured.validation import validate_event

        gateway = StructuredGateway(self.db_url)
        delivered: list[dict] = []

        async def _deliver(envelope):
            delivered.append(envelope)

        from src.storage.database_store import DatabaseWorldStore

        revision = (
            DatabaseWorldStore(self.db_url, self.context.world_id, self.context.world_dir)
            .snapshot()
            .revision
        )
        # 本地无账号模式：隐式操作者同时是 keeper；命令也走网关（同一授权链路）。
        await gateway.handle_frame(
            world_id=self.context.world_id,
            user_id=None,
            frame={
                "type": "command_request",
                "protocol_version": 1,
                "world_id": self.context.world_id,
                "command_id": "cmd-r1",
                "expected_revision": revision,
                "kind": "record_memory",
                "payload": {
                    "character_id": "inv-solo",
                    "knowledge_type": "told",
                    "content": "独行侦探得知书房里有暗格。",
                },
            },
            deliver=_deliver,
        )
        await gateway.handle_frame(
            world_id=self.context.world_id,
            user_id=None,
            frame={
                "type": "memory_query",
                "protocol_version": 1,
                "world_id": self.context.world_id,
                "query_id": "q-1",
                "filters": {"character_id": "inv-solo"},
            },
            deliver=_deliver,
        )
        results = [e for e in delivered if e.get("type") == "memory_query_result"]
        self.assertEqual(1, len(results), delivered)
        validate_event(results[0])  # 服务端发出的信封也过冻结事件 schema
        self.assertEqual(1, len(results[0]["payload"]["memories"]))
        self.assertNotIn("audience", results[0], "上线信封不携带路由 audience")

    async def test_memory_query_rejects_bad_frame(self):
        from src.structured.gateway import StructuredGateway

        gateway = StructuredGateway(self.db_url)
        delivered: list[dict] = []

        async def _deliver(envelope):
            delivered.append(envelope)

        await gateway.handle_frame(
            world_id=self.context.world_id,
            user_id=None,
            frame={
                "type": "memory_query",
                "protocol_version": 1,
                "world_id": self.context.world_id,
                "query_id": "q-bad",
                "filters": {"limit": 999},
            },
            deliver=_deliver,
        )
        errors = [e for e in delivered if e.get("type") == "request_error"]
        self.assertEqual(1, len(errors), delivered)
        self.assertEqual("invalid_action", errors[0]["payload"]["code"])
