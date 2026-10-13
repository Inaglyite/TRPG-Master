"""Knowledge survives long context windows, restarts, forks and restore."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.modules.module_registry import ModuleRegistry
from src.storage.database import EventOutbox, World, WorldState, session_scope
from src.structured.agent import KeeperAgentRunner
from src.structured.agent_knowledge import AgentKnowledge
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService


class AgentKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = make_structured_world(self.root)
        self.db = self.context.database_url
        self.service = StructuredPlayService(self.db)
        self.keeper = Principal(kind="keeper", user_id="u-keeper")
        self.player = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.resolver = ModuleRegistry(self.root, self.root).resolve
        self.knowledge = AgentKnowledge(self.db, module_resolver=self.resolver)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, key, kind, payload, world="sp-world"):
        return self.service.execute_command(
            world_id=world,
            principal=self.keeper,
            kind=kind,
            payload=payload,
            command_id=key,
            expected_revision=None,
        )

    def note(self, key="promise", revision=0, **kwargs):
        return {
            "note_id": key,
            "expected_note_revision": revision,
            "title": "未完成的承诺",
            "summary": "法伦承诺明日陪同，尚未执行",
            "content": "## 承诺\n法伦说会明日陪同，尚未移动。",
            "category": "promise",
            **kwargs,
        }

    def read_all(self, kind, source, world="sp-world", budget=256):
        chunks = []
        query = {"kind": kind, "source_id": source}
        while True:
            result = self.knowledge.query(world, self.keeper, query, char_budget=budget)
            chunks.append(result["content"])
            if result["complete"]:
                return "".join(chunks)
            query.update(offset=result["next_offset"], sha256=result["sha256"])

    def test_private_notes_cas_full_receipt_and_restart(self):
        body = "待办" * 3000 + "最后一句必须能读取"
        outcome = self.command("save-note", "record_keeper_note", self.note(content=body))
        with self.assertRaisesRegex(StructuredError, "主持笔记已被修改"):
            self.command("late-note", "record_keeper_note", self.note(content="过时内容"))
        note = json.loads(self.read_all("notes", "promise"))
        self.assertEqual(body, note["content"])
        self.knowledge = AgentKnowledge(self.db, module_resolver=self.resolver)
        receipt = json.loads(self.read_all("receipt", "save-note"))
        self.assertEqual(outcome["result"], receipt["result"])
        self.assertEqual(body, receipt["result"]["note"]["content"])
        snapshot = self.service.session_snapshot(world_id="sp-world", principal=self.player)
        self.assertNotIn("最后一句", json.dumps(snapshot, ensure_ascii=False))
        for principal in (
            self.player,
            Principal(kind="viewer"),
            Principal(kind="keeper", user_id="u-alice"),
            Principal(kind="agent", run_id="stale"),
        ):
            with self.assertRaises(StructuredError):
                self.knowledge.query("sp-world", principal, {"kind": "notes"})

    def test_module_full_pages_search_version_and_path(self):
        path = self.root / "mod/test-module/module.md"
        text = "# 模组\n" + "正文" * 4000 + "\n法伦的秘密遗体信息\n最后一句"
        path.write_text(text)
        hits = self.knowledge.query("sp-world", self.keeper, {"kind": "module", "text": "遗体"})
        self.assertEqual("module", hits["items"][0]["source_id"])
        self.assertEqual(text, self.read_all("module", "module"))
        with self.assertRaises(StructuredError):
            self.knowledge.query(
                "sp-world", self.keeper, {"kind": "module", "source_id": "../../.env.json"}
            )
        result = self.knowledge.query(
            "sp-world", self.keeper, {"kind": "module", "source_id": "module"}, char_budget=256
        )
        path.write_text("已变更")
        with self.assertRaisesRegex(StructuredError, "资料已变化"):
            self.knowledge.query(
                "sp-world",
                self.keeper,
                {
                    "kind": "module",
                    "source_id": "module",
                    "offset": result["next_offset"],
                    "sha256": result["sha256"],
                },
            )
        with session_scope(self.db) as session:
            session.get(World, "sp-world").module_version = "another-version"
        with self.assertRaisesRegex(StructuredError, "版本不同"):
            self.knowledge.query("sp-world", self.keeper, {"kind": "module"})

    def test_long_history_compacts_without_erasing_early_evidence(self):
        for i in range(180):
            text = (
                "开团时约定口令是蓝铜钟，切勿忘记。"
                if i == 0
                else f"普通对白{i}：" + "背景谈话" * 100
            )
            self.command(
                f"talk-{i}",
                "publish_message",
                {
                    "speaker": {"kind": "keeper"},
                    "text": text,
                    "audience": {"kind": "keeper" if i == 0 else "public"},
                },
            )
        context = self.knowledge.context("sp-world", tail_chars=2400)
        self.assertGreater(context["memory_checkpoint"]["archived_messages"], 170)
        self.assertLess(len(json.dumps(context, ensure_ascii=False)), 26000)
        self.assertNotIn("蓝铜钟", json.dumps(context, ensure_ascii=False))
        hits = self.knowledge.query("sp-world", self.keeper, {"kind": "history", "text": "蓝铜钟"})
        self.assertGreater(hits["total_matches"], 0)
        full = self.read_all("history", hits["items"][0]["source_id"])
        self.assertIn("开团时约定口令", full)
        with session_scope(self.db) as session:
            checkpoint = session.get(WorldState, "sp-world").state["harness_checkpoint"]
            self.assertEqual(context["memory_checkpoint"], checkpoint)

    def test_disabled_module_source_keeps_status_on_full_read_and_document_quota(self):
        path = self.root / "mod/test-module/lorebook.json"
        path.write_text(
            json.dumps(
                {"data": {"entries": [{"enabled": False, "content": "弃用暗号"}]}},
                ensure_ascii=False,
            )
        )
        hits = self.knowledge.query("sp-world", self.keeper, {"kind": "module", "text": "弃用暗号"})
        self.assertFalse(hits["items"][0]["enabled"])
        body = self.knowledge.query(
            "sp-world", self.keeper, {"kind": "module", "source_id": hits["items"][0]["source_id"]}
        )
        self.assertFalse(body["enabled"])
        self.assertEqual("弃用暗号", body["content"])
        path.write_text(json.dumps({"data": {"entries": [{"content": "参考"}] * 200}}))
        with self.assertRaisesRegex(StructuredError, "200份上限"):
            self.knowledge.query("sp-world", self.keeper, {"kind": "module"})

    def test_background_receipts_are_bounded_pointers_but_full_originals_remain_readable(self):
        for i in range(20):
            self.command(
                f"large-talk-{i}",
                "publish_message",
                {
                    "speaker": {"kind": "keeper"},
                    "audience": {"kind": "public"},
                    "text": "日常背景" * 200 + f"最后原文{i}",
                },
            )
        context = self.knowledge.context("sp-world")
        self.assertLess(len(json.dumps(context["execution_receipts"], ensure_ascii=False)), 4000)
        self.assertTrue(all(item["requires_read"] for item in context["execution_receipts"]))
        body = json.loads(self.read_all("receipt", "large-talk-19"))
        self.assertIn("最后原文19", body["payload"]["text"])
        self.assertTrue(body["emitted_events_complete"])

    def test_multi_keyword_history_search_finds_raw_evidence_without_exact_phrase(self):
        self.command(
            "promise-source",
            "publish_message",
            {
                "speaker": {"kind": "keeper"},
                "audience": {"kind": "public"},
                "text": "老医生答应周三见面，口令纸鸢。",
            },
        )
        result = self.knowledge.query(
            "sp-world", self.keeper, {"kind": "history", "text": "老医生 约定 见面 口令"}
        )
        self.assertGreater(result["total_matches"], 0)
        self.assertTrue(result["items"][0]["requires_read"])
        body = self.read_all("history", result["items"][0]["source_id"])
        self.assertIn("口令纸鸢", body)

    def test_fork_receipts_notes_and_event_sources_are_frozen(self):
        from src.structured.branch import create_structured_branch

        self.command(
            "before-talk",
            "publish_message",
            {
                "speaker": {"kind": "keeper"},
                "text": "分叉前法伦的承诺",
                "audience": {"kind": "keeper"},
            },
        )
        with session_scope(self.db) as session:
            seq = session.scalar(
                select(EventOutbox.sequence).where(
                    EventOutbox.world_id == "sp-world",
                    EventOutbox.event_type == "message_completed",
                )
            )
        saved = self.command(
            "before-note",
            "record_keeper_note",
            self.note(sources=[f"event:{seq}", "receipt:before-talk"]),
        )
        frozen_source = saved["result"]["note"]["sources"][0]
        branch = create_structured_branch(
            self.context, project_root=self.root, runtime_root=self.root, label="笔记分支"
        )
        child = branch.context.world_id
        self.command(
            "future-note", "record_keeper_note", self.note(revision=1, content="原世界未来")
        )
        self.command(
            "child-talk",
            "publish_message",
            {"speaker": {"kind": "keeper"}, "text": "新时间线对白", "audience": {"kind": "public"}},
            child,
        )
        self.assertIn("分叉前法伦的承诺", self.read_all("history", frozen_source, child))
        self.assertNotIn("原世界未来", self.read_all("notes", "promise", child))
        self.assertIn("before-talk", self.read_all("receipt", "before-talk", child))
        with self.assertRaises(StructuredError):
            self.read_all("receipt", "future-note", child)

    def test_restore_removes_future_receipts_even_after_revision_catches_up(self):
        from src.storage.persistence import save_game
        from src.structured.branch import restore_structured_save

        self.command("before-note", "record_keeper_note", self.note())
        save_game([], "slot_000", context=self.context)
        self.command("discarded", "record_keeper_note", self.note(revision=1, content="未来秘密"))
        restore_structured_save(self.context, "slot_000")
        self.command(
            "new-timeline", "record_keeper_note", self.note(revision=1, content="另一条时间线")
        )
        with self.assertRaises(StructuredError):
            self.read_all("receipt", "discarded")
        self.assertNotIn("未来秘密", self.read_all("notes", "promise"))
        replay = self.command(
            "discarded", "record_keeper_note", self.note(revision=1, content="未来秘密")
        )
        self.assertTrue(replay["timeline_discarded"])
        self.assertTrue(replay["deduplicated"])
        self.assertNotIn("未来秘密", self.read_all("notes", "promise"))

    def test_switching_saved_timelines_restores_exact_receipts(self):
        from src.storage.persistence import list_saves, save_game
        from src.structured.branch import restore_structured_save

        self.command("base-note", "record_keeper_note", self.note())
        save_game([], "slot_001", context=self.context)
        self.command(
            "original-future", "record_keeper_note", self.note(revision=1, content="原时间线的约定")
        )
        save_game([], "slot_002", context=self.context)
        restore_structured_save(self.context, "slot_001")
        self.command(
            "alternate-future",
            "record_keeper_note",
            self.note(revision=1, content="另一时间线的约定"),
        )
        save_game([], "slot_003", context=self.context)
        restore_structured_save(self.context, "slot_002")
        self.assertIn("原时间线的约定", self.read_all("receipt", "original-future"))
        with self.assertRaises(StructuredError):
            self.read_all("receipt", "alternate-future")
        restore_structured_save(self.context, "slot_003")
        self.assertIn("另一时间线的约定", self.read_all("receipt", "alternate-future"))
        with self.assertRaises(StructuredError):
            self.read_all("receipt", "original-future")
        self.assertNotIn("structured_receipt_cursor", json.dumps(list_saves(context=self.context)))

    def test_forward_restore_does_not_leak_reused_event_sequences(self):
        from src.storage.persistence import save_game
        from src.structured.branch import restore_structured_save

        def talk(key, text):
            self.command(
                key,
                "publish_message",
                {"speaker": {"kind": "keeper"}, "audience": {"kind": "keeper"}, "text": text},
            )

        talk("common-talk", "共同起点")
        save_game([], "slot_001", context=self.context)
        talk("original-talk", "蓝铜钟时间线")
        save_game([], "slot_002", context=self.context)
        restore_structured_save(self.context, "slot_001")
        talk("alternate-talk", "银海螺时间线")
        restore_structured_save(self.context, "slot_002")
        self.assertEqual(
            0,
            self.knowledge.query("sp-world", self.keeper, {"kind": "history", "text": "银海螺"})[
                "total_matches"
            ],
        )
        found = self.knowledge.query("sp-world", self.keeper, {"kind": "history", "text": "蓝铜钟"})
        self.assertEqual(1, found["total_matches"])
        self.assertIn("蓝铜钟", self.read_all("history", found["items"][0]["source_id"]))

    async def test_queries_cannot_execute_or_publish_before_results(self):
        calls = []

        async def caller(system, user):
            calls.append(json.loads(user))
            if len(calls) == 1:
                return json.dumps(
                    {
                        "queries": [{"kind": "module", "text": "Test"}],
                        "commands": [
                            {"kind": "move_party", "payload": {"destination_scene_id": "library"}}
                        ],
                        "narration": "未经查询就移动",
                        "stop_reason": "done",
                    }
                )
            self.assertIn("module 查询结果", str(calls[-1]["run_log"]))
            return json.dumps({"commands": [], "stop_reason": "done"})

        runner = KeeperAgentRunner(self.db, caller=caller, module_resolver=self.resolver)
        result = await runner.run(world_id="sp-world")
        self.assertEqual(2, result.model_calls)
        self.assertEqual(0, result.commands_committed)
        with session_scope(self.db) as session:
            self.assertEqual(
                "study", session.get(WorldState, "sp-world").state["current_scene"]["id"]
            )

    async def test_assisted_queries_are_readonly_until_human_approval(self):
        calls = []

        async def caller(system, user):
            calls.append(json.loads(user))
            if len(calls) == 1:
                return json.dumps({"queries": [{"kind": "module", "text": "Test"}], "commands": []})
            return json.dumps(
                {
                    "commands": [{"kind": "record_keeper_note", "payload": self.note()}],
                    "narration": "",
                }
            )

        runner = KeeperAgentRunner(self.db, caller=caller, module_resolver=self.resolver)
        result = await runner.run_assisted(world_id="sp-world")
        self.assertEqual("draft_ready", result.stop_reason)
        self.assertEqual(2, result.model_calls)
        with session_scope(self.db) as session:
            self.assertNotIn("keeper_notebook", session.get(WorldState, "sp-world").state)
        self.assertEqual(
            [], self.knowledge.query("sp-world", self.keeper, {"kind": "notes"})["items"]
        )

    async def test_failed_commands_do_not_publish_success_narration(self):
        replies = iter(
            [
                json.dumps(
                    {
                        "commands": [
                            {"kind": "move_party", "payload": {"destination_scene_id": "unknown"}}
                        ],
                        "narration": "已经抵达不存在的地点",
                        "stop_reason": "done",
                    }
                ),
                json.dumps({"commands": [], "stop_reason": "done"}),
            ]
        )

        async def caller(system, user):
            return next(replies)

        runner = KeeperAgentRunner(self.db, caller=caller, module_resolver=self.resolver)
        await runner.run(world_id="sp-world")
        with session_scope(self.db) as session:
            texts = [
                e.payload.get("text", "")
                for e in session.scalars(
                    select(EventOutbox).where(
                        EventOutbox.world_id == "sp-world",
                        EventOutbox.event_type == "message_completed",
                    )
                )
            ]
            self.assertNotIn("已经抵达不存在的地点", texts)

    async def test_resolved_request_stops_without_extra_model_calls(self):
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.player,
            request={
                "request_id": "question",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "你好"},
            },
        )
        calls = 0

        async def caller(system, user):
            nonlocal calls
            calls += 1
            self.assertEqual(1, calls)
            return json.dumps(
                {
                    "commands": [
                        {
                            "kind": "resolve_intent",
                            "payload": {
                                "request_id": "question",
                                "resolution": "completed",
                                "outcome": "success",
                            },
                        }
                    ],
                    "narration": "你好",
                }
            )

        runner = KeeperAgentRunner(self.db, caller=caller)
        result = await runner.run(world_id="sp-world", trigger_request_id="question")
        self.assertEqual("requests_resolved", result.stop_reason)
        self.assertEqual(1, result.model_calls)
