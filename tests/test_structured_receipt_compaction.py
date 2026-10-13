"""Semantic memory keeps source pairs, permissions, timelines and live tasks."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.modules.module_registry import ModuleRegistry
from src.storage.database import GameCommand, World, WorldState, session_scope
from src.storage.persistence import save_game
from src.structured.agent import AgentBudget, KeeperAgentRunner
from src.structured.agent_knowledge import AgentKnowledge
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal, bind_agent_control, take_control
from src.structured.receipt_compaction import COMPACTION_PROMPT, ReceiptCompactor, readable_note
from src.structured.service import StructuredPlayService


class ReceiptCompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = make_structured_world(self.root)
        self.db = self.context.database_url
        self.keeper = Principal(kind="keeper", user_id="u-keeper")
        self.player = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.service = StructuredPlayService(self.db)
        self.compactor = ReceiptCompactor(self.db)
        self.knowledge = AgentKnowledge(self.db)
        self.resolver = ModuleRegistry(self.root, self.root).resolve
        with session_scope(self.db) as session:
            world = session.get(World, "sp-world")
            world.metadata_json = {**world.metadata_json, "keeper_mode": "agent"}

    def tearDown(self):
        self.temp.cleanup()

    def command(self, key, kind, payload, *, cause=""):
        return self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            command_id=key,
            kind=kind,
            payload=payload,
            expected_revision=None,
            cause_id=cause or key,
        )

    def history(self, count=30):
        for i in range(count):
            self.command(
                f"talk-{i}",
                "publish_message",
                {
                    "speaker": {"kind": "keeper"},
                    "audience": {"kind": "public"},
                    "text": "法伦答应明日同行，尚未执行。" if i == 0 else f"日常对白{i}。",
                },
            )

    def prepared(self, **kwargs):
        return self.compactor.prepare(
            "sp-world", self.keeper, tail_receipts=0, minimum_old=1, **kwargs
        )

    @staticmethod
    def summary(prepared):
        key = next(s["command_id"] for s in prepared["sources"] if s["complete"])
        return {
            "title": "法伦的同行约定",
            "summary": "法伦答应明日同行，计划尚未落实。",
            "keywords": ["法伦", "同行", "明日"],
            "entries": [
                {"category": "promise", "text": "同行是承诺，尚未移动。", "sources": [key]}
            ],
        }

    def test_pairs_are_complete_and_live_requests_never_compacted(self):
        self.history(30)
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.player,
            request={
                "request_id": "open",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "计划去图书馆"},
            },
        )
        self.command("live", "advance_time", {"minutes": 0, "reason": "等待"}, cause="open")
        prepared = self.prepared()
        self.assertNotIn("live", [s["command_id"] for s in prepared["sources"]])
        for source in prepared["inputs"]:
            self.assertTrue(source["complete"])
            self.assertIn("payload", source["command"])
            self.assertIn("result", source["command"])
            self.assertEqual("committed", source["command"]["status"])
        self.assertLessEqual(len(prepared["inputs"]), 16)

    async def test_automatic_semantic_memory_survives_restart_and_is_queryable(self):
        self.history(30)
        before = self.service.session_snapshot(world_id="sp-world", principal=self.keeper)[
            "revision"
        ]
        self.command("release", "control_keeper", {"action": "release"})
        calls = []

        async def caller(system, user):
            value = json.loads(user)
            calls.append((system, value))
            if system == COMPACTION_PROMPT:
                source = next(i["command"]["command_id"] for i in value["sources"] if i["complete"])
                return json.dumps(
                    {
                        "title": "法伦的同行约定",
                        "summary": "法伦承诺明日同行，未移动。",
                        "keywords": ["法伦", "同行"],
                        "entries": [
                            {
                                "category": "promise",
                                "text": "明日同行尚未执行。",
                                "sources": [source],
                            }
                        ],
                    }
                )
            if len(calls) == 2:
                self.assertEqual("法伦的同行约定", value["chapter_index"]["items"][0]["title"])
                return json.dumps({"queries": [{"kind": "chapters", "text": "同行"}]})
            self.assertIn("法伦承诺明日同行", str(value["run_log"]))
            return json.dumps({"commands": [], "stop_reason": "done"})

        runner = KeeperAgentRunner(
            self.db,
            caller=caller,
            module_resolver=self.resolver,
            budget=AgentBudget(compaction_minimum_receipts=1),
        )
        result = await runner.run(world_id="sp-world")
        self.assertEqual(3, result.model_calls)
        self.assertEqual(0, result.commands_committed)
        knowledge = AgentKnowledge(self.db)
        chapters = knowledge.query("sp-world", self.keeper, {"kind": "chapters"})
        body = knowledge.query(
            "sp-world",
            self.keeper,
            {"kind": "chapters", "source_id": chapters["items"][0]["source_id"]},
        )
        self.assertIn("尚未执行", body["content"])
        self.assertIn("talk-0", body["content"])
        with session_scope(self.db) as session:
            state = session.get(WorldState, "sp-world")
            self.assertEqual(before, state.revision)  # Compaction and control change no rule facts.
            self.assertEqual("study", state.state["current_scene"]["id"])
            self.assertEqual(31, len(session.scalars(select(GameCommand)).all()))

    def test_invalid_or_incomplete_sources_cannot_support_claims(self):
        self.history(2)
        prepared = self.prepared()
        invalid = self.summary(prepared)
        invalid["entries"][0]["sources"] = ["invented"]
        with self.assertRaises(StructuredError):
            self.compactor.finish(prepared, invalid)
        incomplete = copy.deepcopy(prepared)
        for source in incomplete["sources"]:
            source["complete"] = False
        with self.assertRaises(StructuredError):
            self.compactor.finish(incomplete, self.summary(prepared))
        with self.assertRaises(StructuredError):
            self.compactor.finish(prepared, {**self.summary(prepared), "commands": []})

    def test_source_budget_uses_explicit_unread_pointer_not_fake_summary(self):
        self.command(
            "long",
            "record_keeper_note",
            {
                "note_id": "long",
                "expected_note_revision": 0,
                "title": "长回执",
                "summary": "需要回读",
                "content": "正文" * 6000,
                "category": "plan",
            },
        )
        prepared = self.prepared(source_chars=500)
        self.assertFalse(prepared["sources"][0]["complete"])
        self.assertLess(len(json.dumps(prepared["inputs"], ensure_ascii=False)), 500)
        chapter = self.compactor.finish(
            prepared,
            {"title": "待回读", "summary": "需要读完整回执", "keywords": [], "entries": []},
        )
        self.assertEqual(["long"], chapter["unread_sources"])

    def test_saved_chapters_never_become_input_for_recursive_summary(self):
        self.history(2)
        prepared = self.prepared()
        chapter = self.compactor.finish(prepared, self.summary(prepared))
        chapter["summary"] = "ONLY_OLD_SUMMARY_SENTINEL"
        memory_command = self.compactor.note_command(chapter)
        self.command("save-old-summary", memory_command["kind"], memory_command["payload"])
        next_chunk = self.prepared()
        raw = json.dumps(next_chunk["inputs"], ensure_ascii=False)
        self.assertNotIn("ONLY_OLD_SUMMARY_SENTINEL", raw)
        self.assertIn("memory_summary_omitted", raw)
        original = self.knowledge.query(
            "sp-world",
            self.keeper,
            {"kind": "receipt", "source_id": "save-old-summary"},
            char_budget=16000,
        )
        self.assertIn("ONLY_OLD_SUMMARY_SENTINEL", original["content"])

    def test_chapter_note_is_readable_and_human_corrections_are_not_recompressed(self):
        self.history(2)
        prepared = self.prepared()
        chapter = self.compactor.finish(prepared, self.summary(prepared))
        memory_command = self.compactor.note_command(chapter)
        formatted = readable_note(memory_command["payload"]["content"])
        self.assertIn("承诺：同行是承诺", formatted)
        self.assertNotIn("sha256", formatted)
        self.command("summary", memory_command["kind"], memory_command["payload"])
        revised = {
            **memory_command["payload"],
            "expected_note_revision": 1,
            "content": "## 主持更正\n改为后日同行。",
        }
        self.command("human-correction", "record_keeper_note", revised)
        self.assertEqual(revised["content"], readable_note(revised["content"]))
        next_chunk = self.prepared()
        self.assertNotIn("talk-0", [s["command_id"] for s in next_chunk["sources"]])

    def test_fork_copies_chapter_and_original_receipts_without_parent_future(self):
        self.history(2)
        prepared = self.prepared()
        chapter = self.compactor.finish(prepared, self.summary(prepared))
        self.compactor.persist("sp-world", self.keeper, chapter)
        branch = create_structured_branch(
            self.context, project_root=self.root, runtime_root=self.root, label="章节分支"
        )
        self.history(3)  # cached 0/1, new receipt 2 only
        hits = self.knowledge.query(branch.context.world_id, self.keeper, {"kind": "chapters"})
        self.assertEqual(chapter["chapter_id"], hits["items"][0]["source_id"])
        receipt = self.knowledge.query(
            branch.context.world_id, self.keeper, {"kind": "receipt", "source_id": "talk-0"}
        )
        self.assertIn("明日同行", receipt["content"])

    def test_restore_during_summary_rejects_future_memory(self):
        self.history(2)
        save_game([], "slot_001", context=self.context)
        self.history(4)
        prepared = self.prepared()
        chapter = self.compactor.finish(prepared, self.summary(prepared))
        restore_structured_save(self.context, "slot_001")
        with self.assertRaisesRegex(StructuredError, "时间线已变化"):
            self.compactor.persist("sp-world", self.keeper, chapter)
        self.assertEqual(
            [], self.knowledge.query("sp-world", self.keeper, {"kind": "chapters"})["items"]
        )

    def test_takeover_rejects_old_agent_summary_and_players_cannot_read(self):
        self.history(2)
        prepared = self.prepared()
        chapter = self.compactor.finish(prepared, self.summary(prepared))
        with session_scope(self.db) as session:
            bind_agent_control(session, "sp-world", "old-run")
            take_control(session, "sp-world", self.keeper)
        with self.assertRaises(StructuredError):
            self.compactor.persist("sp-world", Principal(kind="agent", run_id="old-run"), chapter)
        self.compactor.persist("sp-world", self.keeper, chapter)
        with self.assertRaises(StructuredError):
            self.knowledge.query("sp-world", self.player, {"kind": "chapters"})
        snapshot = self.service.session_snapshot(world_id="sp-world", principal=self.player)
        self.assertNotIn("harness_chapters", str(snapshot))

    async def test_assisted_preview_is_readonly_and_persists_only_after_approval(self):
        self.history(30)
        before = None
        with session_scope(self.db) as session:
            before = copy.deepcopy(session.get(WorldState, "sp-world").state)

        async def caller(system, user):
            value = json.loads(user)
            if system == COMPACTION_PROMPT:
                key = value["sources"][0]["command"]["command_id"]
                return json.dumps(
                    {
                        "title": "历史约定",
                        "summary": "明日同行尚未移动。",
                        "keywords": ["同行"],
                        "entries": [
                            {"category": "promise", "text": "明日同行尚未移动。", "sources": [key]}
                        ],
                    }
                )
            self.assertTrue(value["compaction_preview_requires_human_approval"])
            return json.dumps({"commands": [], "assessment": "私有阶段记忆待批准"})

        runner = KeeperAgentRunner(
            self.db,
            caller=caller,
            module_resolver=self.resolver,
            budget=AgentBudget(compaction_minimum_receipts=1),
        )
        delivered = []

        async def deliver(event):
            delivered.append(event)

        result = await runner.run_assisted(world_id="sp-world", deliver=deliver)
        self.assertEqual(2, result.model_calls)
        self.assertEqual("draft_ready", result.stop_reason)
        with session_scope(self.db) as session:
            self.assertEqual(before, session.get(WorldState, "sp-world").state)
        self.assertEqual(
            [], self.knowledge.query("sp-world", self.keeper, {"kind": "chapters"})["items"]
        )
        draft = next(e for e in delivered if e["type"] == "keeper_draft")
        self.command(
            "approve-chapter",
            "resolve_draft",
            {"draft_id": draft["payload"]["draft_id"], "decision": "approved"},
        )
        chapters = self.knowledge.query("sp-world", self.keeper, {"kind": "chapters"})
        self.assertEqual("历史约定", chapters["items"][0]["title"])
        self.assertIsNone(
            self.compactor.prepare("sp-world", self.keeper, tail_receipts=12, minimum_old=16)
        )  # Already summarized receipts excluded.

    async def test_invalid_summary_keeps_raw_history_and_never_executes_its_commands(self):
        self.history(30)
        self.command("release", "control_keeper", {"action": "release"})
        calls = 0

        async def caller(system, user):
            nonlocal calls
            calls += 1
            if system == COMPACTION_PROMPT:
                return json.dumps(
                    {
                        "commands": [
                            {"kind": "move_party", "payload": {"destination_scene_id": "library"}}
                        ]
                    }
                )
            return json.dumps({"commands": [], "stop_reason": "done"})

        runner = KeeperAgentRunner(
            self.db,
            caller=caller,
            module_resolver=self.resolver,
            budget=AgentBudget(compaction_minimum_receipts=1),
        )
        result = await runner.run(world_id="sp-world")
        self.assertEqual(2, calls)
        self.assertEqual(0, result.commands_committed)
        self.assertGreater(
            self.knowledge.query("sp-world", self.keeper, {"kind": "history", "text": "法伦"})[
                "total_matches"
            ],
            0,
        )
        self.assertEqual(
            [], self.knowledge.query("sp-world", self.keeper, {"kind": "chapters"})["items"]
        )
