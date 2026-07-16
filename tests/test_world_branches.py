import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.engine import GameEngine
from src.player_notes import PlayerNotesStore
from src.runtime import RuntimeContext
from src.world_branches import WorldBranchService


class WorldBranchTests(unittest.TestCase):
    def make_engine(self, root: Path) -> GameEngine:
        module_dir = root / "mod" / "branch-module"
        module_dir.mkdir(parents=True)
        (module_dir / "module.md").write_text("# Branch Test", encoding="utf-8")
        (module_dir / "world_state_initial.json").write_text(
            json.dumps({
                "module": "branch-module",
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
            "main-world",
            "branch-module",
            project_root=root,
            runtime_root=root,
        )
        with patch("src.engine.OpenAI", return_value=object()):
            engine = GameEngine(context)
        engine.prepare_session()
        return engine

    def test_branch_clones_lineage_and_keeps_worlds_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            narratives = iter([
                "你检查了书桌。\n\n**你可以——**\n1. 查看书架",
                "你走向书架。\n\n**你可以——**\n1. 翻开旧书",
            ])
            engine._stream_llm = lambda *_args, **_kwargs: (next(narratives), [])
            engine.handle_action("检查书桌")
            first_turn = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(first_turn)
            assert first_turn is not None
            _messages, first_snapshot = engine.turn_journal.load_artifacts(first_turn)

            def advance(state: dict) -> None:
                state.setdefault("flags", {})["bookcase_open"] = True

            engine.context.world_store.update(advance)
            engine.handle_action("走向书架")
            second_turn = engine.turn_journal.latest_completed_id()
            self.assertNotEqual(first_turn, second_turn)
            self.assertEqual(
                first_turn,
                engine.turn_journal.public_history()[-1]["parent_turn_id"],
            )

            service = WorldBranchService(root, root)
            PlayerNotesStore(engine.context.world_dir).save("不要相信书架后的声音。")
            branch = service.create(
                engine.context,
                engine.turn_journal,
                first_turn,
                label="不碰书架",
            )

            self.assertNotEqual(engine.context.world_id, branch.context.world_id)
            self.assertEqual(first_snapshot, branch.context.world_store.load())
            self.assertTrue(engine.context.world_store.load()["flags"]["bookcase_open"])
            history = branch.context.world_dir / "turns" / "index.json"
            self.assertTrue(history.is_file())

            with patch("src.engine.OpenAI", return_value=object()):
                branch_engine = GameEngine(branch.context)
            branch_engine.prepare_session()
            branch_engine.adopt_message_history(branch.messages)
            branch_history = branch_engine.turn_journal.public_history()
            self.assertEqual([first_turn], [item["turn_id"] for item in branch_history])
            self.assertIsNone(branch_history[0]["parent_turn_id"])
            self.assertNotIn("走向书架", json.dumps(branch.messages, ensure_ascii=False))
            self.assertEqual(
                "不要相信书架后的声音。",
                PlayerNotesStore(branch.context.world_dir).load()["text"],
            )

            def alter_branch(state: dict) -> None:
                state.setdefault("flags", {})["left_the_room"] = True

            branch.context.world_store.update(alter_branch)
            self.assertNotIn("left_the_room", engine.context.world_store.load().get("flags", {}))

            metadata = json.loads(branch.context.metadata_file.read_text(encoding="utf-8"))
            self.assertEqual("不碰书架", metadata["display_name"])
            self.assertEqual("main-world", metadata["branch"]["parent_world_id"])
            self.assertEqual(first_turn, metadata["branch"]["source_turn_id"])

            listed = service.list_worlds(
                "branch-module",
                active_world_id=branch.context.world_id,
            )
            self.assertEqual(branch.context.world_id, listed[0]["world_id"])
            self.assertTrue(listed[0]["active"])
            reopened = service.open(branch.context.world_id)
            self.assertEqual(branch.context.world_id, reopened.world_id)

    def test_branch_preserves_nonzero_fork_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)

            def advance(state: dict) -> None:
                state.setdefault("flags", {})["visited"] = True

            engine.context.world_store.update(advance)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None
            source_revision = engine.context.world_store.revision

            branch = WorldBranchService(root, root).create(
                engine.context,
                engine.turn_journal,
                turn_id,
            )
            self.assertEqual(source_revision, branch.context.world_store.revision)
            self.assertEqual(
                source_revision,
                json.loads(
                    (
                        branch.context.turns_dir
                        / turn_id
                        / "record.json"
                    ).read_text(encoding="utf-8")
                )["world_revision"],
            )


if __name__ == "__main__":
    unittest.main()
