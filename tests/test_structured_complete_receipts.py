"""Durable narration, exact timeline isolation, no replay and atomic event receipts."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session
from test_structured_commands import make_structured_world

from src.storage.database import (
    CommandEventReceipt,
    EventOutbox,
    GameCommand,
    World,
    WorldState,
    get_engine,
    initialize_database,
    session_scope,
)
from src.storage.persistence import save_game
from src.structured.agent_knowledge import AgentKnowledge
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.receipt_compaction import ReceiptCompactor
from src.structured.service import StructuredPlayService


class CompleteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = make_structured_world(self.root)
        self.db = self.context.database_url
        self.keeper = Principal(kind="keeper", user_id="u-keeper")
        self.player = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.service = StructuredPlayService(self.db)
        self.knowledge = AgentKnowledge(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, key, kind, payload):
        return self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            command_id=key,
            kind=kind,
            payload=payload,
            expected_revision=None,
        )

    def draft(self, text, key):
        proposed = self.service.create_keeper_draft(
            world_id="sp-world", summary="约定", proposed_commands=[], narration=text
        )
        draft_id = proposed["events"][0]["payload"]["draft_id"]
        return self.command(key, "resolve_draft", {"draft_id": draft_id, "decision": "approved"})

    def read(self, query, world="sp-world", budget=256):
        chunks = []
        while True:
            result = self.knowledge.query(world, self.keeper, query, char_budget=budget)
            chunks.append(result["content"])
            if result["complete"]:
                return json.loads("".join(chunks))
            query = {**query, "offset": result["next_offset"], "sha256": result["sha256"]}

    def test_full_receipt_contains_original_private_and_public_events_after_restart(self):
        outcome = self.draft("老医生答应后日来礼堂，见面还未发生。", "approve")
        self.knowledge = AgentKnowledge(self.db)
        receipt = self.read({"kind": "receipt", "source_id": "approve"})
        self.assertTrue(receipt["emitted_events_complete"])
        self.assertEqual(outcome["events"], receipt["emitted_events"])
        audiences = [e["audience"]["kind"] for e in receipt["emitted_events"]]
        self.assertIn("public", audiences)
        self.assertIn("keeper", audiences)
        with self.assertRaises(StructuredError):
            self.knowledge.query(
                "sp-world", self.player, {"kind": "receipt", "source_id": "approve"}
            )
        self.assertEqual(
            1,
            self.knowledge.query(
                "sp-world", self.keeper, {"kind": "receipt", "text": "后日来礼堂"}
            )["total_matches"],
        )

    def test_switching_saved_timelines_recovers_draft_narration_without_outbox_replay(self):
        save_game([], "slot_001", context=self.context)
        self.draft("蓝铜钟原时间线的承诺", "original")
        save_game([], "slot_002", context=self.context)
        restore_structured_save(self.context, "slot_001")
        self.draft("银海螺另一时间线的承诺", "alternate")
        restore_structured_save(self.context, "slot_002")
        with session_scope(self.db) as session:
            self.assertFalse(
                any(
                    "蓝铜钟" in json.dumps(e.payload, ensure_ascii=False)
                    for e in session.scalars(select(EventOutbox)).all()
                )
            )
        found = self.knowledge.query("sp-world", self.keeper, {"kind": "history", "text": "蓝铜钟"})
        self.assertEqual(1, found["total_matches"])
        self.assertTrue(found["items"][0]["source_id"].startswith("receipt-event:"))
        body = self.read({"kind": "history", "source_id": found["items"][0]["source_id"]})
        self.assertEqual("original", body["command_id"])
        self.assertIn("蓝铜钟", body["payload"]["text"])
        self.assertEqual(
            0,
            self.knowledge.query("sp-world", self.keeper, {"kind": "history", "text": "银海螺"})[
                "total_matches"
            ],
        )

    def test_live_outbox_and_immutable_history_do_not_duplicate_publication(self):
        self.draft("只出现一次的礼堂约定", "approved-once")
        hits = self.knowledge.query(
            "sp-world", self.keeper, {"kind": "history", "text": "礼堂约定"}
        )
        published = [hit for hit in hits["items"] if hit["event_type"] == "message_completed"]
        self.assertEqual(1, len(published))
        self.assertTrue(published[0]["source_id"].startswith("event:"))
        proposals = [hit for hit in hits["items"] if hit["source_is_proposal"]]
        self.assertEqual(1, len(proposals))  # The unexecuted draft is explicitly separate.

    def test_cached_command_returns_no_events_and_never_republishes_narration(self):
        original = self.draft("幂等发言", "approved-once")
        replay = self.command(
            "approved-once",
            "resolve_draft",
            {"draft_id": original["result"]["draft_id"], "decision": "approved"},
        )
        self.assertTrue(replay["deduplicated"])
        self.assertEqual([], replay["events"])
        with session_scope(self.db) as session:
            command = session.scalar(
                select(GameCommand).where(GameCommand.command_id == "approved-once")
            )
            self.assertEqual(len(original["events"]), len(command.event_receipts))

    def test_fork_preserves_full_receipts_but_never_parent_future_narration(self):
        original = self.draft("共同历史的纸鸢约定", "before-fork")
        branch = create_structured_branch(
            self.context, project_root=self.root, runtime_root=self.root, label="完整回执"
        )
        self.draft("原世界未来信息", "future")
        body = self.read({"kind": "receipt", "source_id": "before-fork"}, branch.context.world_id)
        self.assertEqual(original["events"], body["emitted_events"])
        with self.assertRaises(StructuredError):
            self.read({"kind": "receipt", "source_id": "future"}, branch.context.world_id)

    def test_phase_summary_input_includes_approved_draft_narration(self):
        self.draft("原始草稿中的医生承诺", "source-draft")
        prepared = ReceiptCompactor(self.db).prepare(
            "sp-world", self.keeper, tail_receipts=0, minimum_old=1
        )
        self.assertIn("医生承诺", json.dumps(prepared["inputs"], ensure_ascii=False))
        self.assertTrue(prepared["sources"][0]["complete"])

    def test_old_receipts_explicitly_report_missing_immutable_events(self):
        self.draft("旧记录", "old")
        with session_scope(self.db) as session:
            command = session.scalar(select(GameCommand).where(GameCommand.command_id == "old"))
            command.event_receipts.clear()
        receipt = self.read({"kind": "receipt", "source_id": "old"})
        self.assertFalse(receipt["emitted_events_complete"])
        self.assertEqual([], receipt["emitted_events"])

    def test_commit_failure_rolls_back_command_events_audit_and_world(self):
        with session_scope(self.db) as session:
            before = copy.deepcopy(session.get(WorldState, "sp-world").state)

        def fail(session):
            session.flush()
            raise RuntimeError("commit rejected")

        event.listen(Session, "before_commit", fail)
        try:
            with self.assertRaisesRegex(RuntimeError, "commit rejected"):
                self.command("atomic", "move_party", {"destination_scene_id": "library"})
        finally:
            event.remove(Session, "before_commit", fail)
        with session_scope(self.db) as session:
            self.assertEqual(before, session.get(WorldState, "sp-world").state)
            self.assertEqual(0, session.scalar(select(func.count()).select_from(GameCommand)))
            self.assertEqual(
                0, session.scalar(select(func.count()).select_from(CommandEventReceipt))
            )
            self.assertEqual(0, session.scalar(select(func.count()).select_from(EventOutbox)))


def test_old_database_bootstrap_adds_receipt_table_without_altering_command_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "TRPG_DATABASE_URL": url}

    def alembic(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    alembic("upgrade", "20261004_0019")
    engine = get_engine(url)
    old_columns = inspect(engine).get_columns("game_commands")
    with engine.begin() as connection:
        connection.execute(
            World.__table__.insert().values(
                id="old-world",
                module_name="test",
                module_version="1",
                metadata_json={"mode": "local"},
            )
        )
        connection.execute(
            GameCommand.__table__.insert().values(
                id="old-cmd",
                world_id="old-world",
                command_id="legacy-message",
                kind="publish_message",
                payload_digest="0" * 64,
                payload={"text": "旧承诺"},
                result={"message_id": "old-message"},
                principal={},
            )
        )
    initialize_database(url)  # Desktop startup's create_all adds the new table.
    assert [(c["name"], str(c["type"])) for c in inspect(engine).get_columns("game_commands")] == [
        (c["name"], str(c["type"])) for c in old_columns
    ]
    alembic("upgrade", "head")  # Migration safely adopts the existing new table.
    alembic("check")
    with session_scope(url) as session:
        command = session.get(GameCommand, "old-cmd")
        assert command.payload == {"text": "旧承诺"}
        assert command.result == {"message_id": "old-message"}
        assert command.event_receipts == []
