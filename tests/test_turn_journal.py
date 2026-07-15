import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.engine import GameEngine
from src.runtime import RuntimeContext
from src.turn_journal import ActiveTurnError, TurnJournal


class TurnJournalTests(unittest.TestCase):
    def make_journal(self, root: Path, owner: str = "process-a") -> TurnJournal:
        return TurnJournal(
            root / "worlds" / "test-world",
            world_id="test-world",
            module_name="test-module",
            owner_token=owner,
        )

    def make_engine(self, root: Path) -> GameEngine:
        module_dir = root / "mod" / "test-module"
        module_dir.mkdir(parents=True)
        (module_dir / "module.md").write_text("# Test", encoding="utf-8")
        (module_dir / "world_state_initial.json").write_text(
            json.dumps({
                "module": "test-module",
                "pc": {
                    "name": "调查员",
                    "hp": 10,
                    "max_hp": 10,
                    "san": 50,
                    "max_san": 50,
                    "inventory": [],
                },
                "current_scene": {"id": "study", "name": "书房"},
                "scene_catalog": {},
                "npcs": [],
                "clues_found": {
                    "investigation": [],
                    "event": [],
                    "task": [],
                    "npc": [],
                },
                "combat_state": {"active": False},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        context = RuntimeContext.create(
            "journal-world",
            "test-module",
            project_root=root,
            runtime_root=root,
        )
        with patch("src.engine.OpenAI", return_value=object()):
            engine = GameEngine(context)
        engine.prepare_session()
        return engine

    def test_completed_turn_commits_replay_and_authoritative_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = self.make_journal(Path(temp))
            turn_id = journal.begin(kind="action", player_input="检查书桌")
            journal.append_event(turn_id, {
                "type": "narrative_chunk",
                "text": "你拉开",
                "turn_id": "transient",
                "seq": 3,
            })
            journal.append_event(turn_id, {
                "type": "narrative_chunk",
                "text": "抽屉。",
            })
            journal.append_event(turn_id, {
                "type": "dice_result",
                "summary": "侦查成功",
                "roll_data": {"rolls": [12]},
            })
            journal.append_event(turn_id, {
                "type": "handout",
                "file": "desk.png",
                "asset_data_uri": "data:image/png;base64,large-payload",
            })

            record = journal.complete(
                turn_id,
                messages=[
                    {"role": "system", "content": "keeper"},
                    {"role": "assistant", "content": "你拉开抽屉。"},
                ],
                world_state={"revision": 7, "flags": {"desk_open": True}},
                narrative="你拉开抽屉。",
                choices=[{"label": "查看文件", "isFree": False}],
                executed_tools=[{"name": "skill_check", "output": "private"}],
                lore_entry_ids=["study-dust"],
                diagnostics={
                    "model_calls": [{
                        "model": "story-model",
                        "first_token_ms": 120,
                        "elapsed_ms": 450,
                    }],
                    "lorebook": {
                        "selected": [{"entry_id": "study-dust"}],
                        "token_estimate": 18,
                    },
                },
            )

            self.assertEqual("completed", record["status"])
            self.assertEqual(7, record["world_revision"])
            self.assertEqual("你拉开抽屉。", record["events"][0]["text"])
            self.assertNotIn("turn_id", record["events"][0])
            self.assertNotIn("seq", record["events"][0])
            handout = next(event for event in record["events"] if event["type"] == "handout")
            self.assertNotIn("asset_data_uri", handout)

            messages, snapshot = journal.load_artifacts(turn_id)
            self.assertEqual("你拉开抽屉。", messages[-1]["content"])
            self.assertTrue(snapshot["flags"]["desk_open"])

            index = json.loads(journal.index_path.read_text(encoding="utf-8"))
            self.assertIsNone(index["active_turn_id"])
            self.assertEqual(turn_id, index["latest_completed_turn_id"])
            public = journal.recovery_status(turn_id)["requested"]
            self.assertNotIn("executed_tools", public)
            self.assertNotIn("lore_entry_ids", public)
            diagnostic = journal.diagnostic_report(turn_id)
            self.assertEqual("story-model", diagnostic["model_calls"][0]["model"])
            self.assertEqual(["skill_check"], diagnostic["tool_names"])
            self.assertEqual(18, diagnostic["lorebook"]["token_estimate"])

    def test_new_process_marks_uncommitted_turn_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.make_journal(root, "process-a")
            turn_id = first.begin(kind="opening", player_input=None)

            second = self.make_journal(root, "process-b")
            status = second.recovery_status(turn_id)
            self.assertEqual("interrupted", status["requested"]["status"])
            self.assertIsNone(status["active"])

    def test_same_process_cannot_start_overlapping_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.make_journal(root, "same-process")
            turn_id = first.begin(kind="action", player_input="行动一")
            second = self.make_journal(root, "same-process")

            with self.assertRaises(ActiveTurnError):
                second.begin(kind="action", player_input="行动二")

            cancelled = first.finish_incomplete(
                turn_id,
                status="cancelled",
                error="玩家取消",
            )
            self.assertEqual("cancelled", cancelled["status"])
            next_turn = second.begin(kind="action", player_input="行动三")
            self.assertNotEqual(turn_id, next_turn)

    def test_game_engine_finalizes_turn_record_with_graph_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "书房安静得能听见钟摆。\n\n**你可以——**\n1. 查看书架",
                [],
            )

            engine.handle_action("留在原地观察")

            records = engine.turn_journal.list_completed()
            self.assertEqual(1, len(records))
            self.assertEqual("留在原地观察", records[0]["player_input"])
            messages, snapshot = engine.turn_journal.load_artifacts(records[0]["turn_id"])
            self.assertIn("钟摆", messages[-1]["content"])
            self.assertEqual("书房", snapshot["current_scene"]["name"])

    def test_rewrite_replaces_only_prose_and_preserves_committed_state(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self.make_engine(Path(temp))
            engine._stream_llm = lambda *_args, **_kwargs: (
                "书房安静得能听见钟摆。\n\n**你可以——**\n1. 查看书架",
                [],
            )
            engine.handle_action("留在原地观察")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            state_before = engine.context.world_store.load()
            _messages_before, snapshot_before = engine.turn_journal.load_artifacts(turn_id)
            record_before = engine.turn_journal.read(turn_id)
            captured: dict = {}

            def rewrite_stream(*args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                return (
                    "钟摆把沉默切成均匀的薄片，书房仍旧没有异动。"
                    "\n\n**你可以——**\n1. 改变已经固定的行动",
                    [],
                )

            engine._stream_llm = rewrite_stream
            result = engine.rewrite_turn(turn_id)

            self.assertFalse(captured["kwargs"]["enable_tools"])
            self.assertEqual("rewrite", captured["kwargs"]["prompt_profile"])
            prompt = captured["kwargs"]["messages_override"]
            self.assertEqual(["system", "user"], [item["role"] for item in prompt])
            self.assertEqual(state_before, engine.context.world_store.load())
            _messages_after, snapshot_after = engine.turn_journal.load_artifacts(turn_id)
            self.assertEqual(snapshot_before, snapshot_after)
            self.assertNotIn("你可以", result["narrative"])

            record_after = engine.turn_journal.read(turn_id)
            self.assertEqual(record_before["choices"], record_after["choices"])
            self.assertEqual(record_before["executed_tools"], record_after["executed_tools"])
            self.assertEqual("variant_001", record_after["selected_variant_id"])
            self.assertEqual(2, len(record_after["narrative_variants"]))
            self.assertIn("钟摆把沉默", engine.messages[-1]["content"])
            saved_messages = json.loads(
                (engine.context.saves_dir / "slot_000" / "messages.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(engine.messages[-1]["content"], saved_messages[-1]["content"])

    def test_rewrite_rejects_turn_after_world_has_advanced(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self.make_engine(Path(temp))
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你观察了书房。\n\n**你可以——**\n1. 查看书架",
                [],
            )
            engine.handle_action("观察书房")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            def advance(state: dict) -> None:
                state.setdefault("flags", {})["advanced"] = True

            engine.context.world_store.update(advance)
            with self.assertRaisesRegex(ValueError, "继续推进"):
                engine.rewrite_turn(turn_id)


if __name__ == "__main__":
    unittest.main()
