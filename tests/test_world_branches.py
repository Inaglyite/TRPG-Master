import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database import SaveSlot, Turn, World, new_id, session_scope
from src.database_turn_journal import DatabaseTurnJournal
from src.engine import GameEngine
from src.persistence import save_game
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
            self.assertIn("context", engine.turn_journal.read(first_turn))
            self.assertNotIn(
                "context",
                DatabaseTurnJournal(
                    branch.context.world_dir,
                    world_id=branch.context.world_id,
                    module_name=branch.context.module_name,
                ).read(first_turn),
            )
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
            self.assertTrue(listed[0]["resumable"])
            reopened = service.open(branch.context.world_id)
            self.assertEqual(branch.context.world_id, reopened.world_id)

    def test_list_worlds_only_returns_the_current_branch_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            service = WorldBranchService(root, root)
            branch = service.create(engine.context, engine.turn_journal, turn_id)

            # Same module, but it is a wholly separate world and intentionally
            # has no automatic save. It must never appear as a timeline option.
            RuntimeContext.create(
                "unrelated-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )

            listed = service.list_worlds(
                "branch-module",
                active_world_id=branch.context.world_id,
            )
            self.assertEqual(
                {"main-world", branch.context.world_id},
                {item["world_id"] for item in listed},
            )
            self.assertNotIn(
                "unrelated-world", {item["world_id"] for item in listed}
            )

    def test_archive_rejects_main_current_and_non_leaf_then_hides_leaf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            service = WorldBranchService(root, root)
            branch = service.create(engine.context, engine.turn_journal, turn_id)
            with self.assertRaisesRegex(ValueError, "主时间线"):
                service.archive_branch("main-world", active_world_id="main-world")
            with self.assertRaisesRegex(ValueError, "当前正在使用"):
                service.archive_branch(
                    branch.context.world_id,
                    active_world_id=branch.context.world_id,
                )

            child = RuntimeContext.create(
                "child-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )
            with session_scope(child.database_url) as session:
                child_world = session.get(World, child.world_id)
                assert child_world is not None
                child_world.metadata_json = {
                    **dict(child_world.metadata_json or {}),
                    "branch": {"parent_world_id": branch.context.world_id},
                }
            with self.assertRaisesRegex(ValueError, "子分支"):
                service.archive_branch(
                    branch.context.world_id,
                    active_world_id="main-world",
                )

            with session_scope(child.database_url) as session:
                child_world = session.get(World, child.world_id)
                assert child_world is not None
                child_world.status = "archived"

            archived = service.archive_branch(
                branch.context.world_id,
                active_world_id="main-world",
            )
            self.assertEqual(branch.context.world_id, archived["world_id"])
            self.assertEqual("main-world", archived["fallback_world_id"])
            with session_scope(branch.context.database_url) as session:
                world = session.get(World, branch.context.world_id)
                assert world is not None
                self.assertEqual("archived", world.status)
            with self.assertRaises(FileNotFoundError):
                service.open(branch.context.world_id)
            listed = service.list_worlds("branch-module", active_world_id="main-world")
            self.assertNotIn(branch.context.world_id, {item["world_id"] for item in listed})

    def test_archive_rejects_a_durably_active_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None
            service = WorldBranchService(root, root)
            branch = service.create(engine.context, engine.turn_journal, turn_id)

            with session_scope(branch.context.database_url) as session:
                session.add(
                    Turn(
                        pk=new_id("turnrow"),
                        id="still-running",
                        world_id=branch.context.world_id,
                        kind="action",
                        status="active",
                        record={"turn_id": "still-running", "status": "active"},
                    )
                )
            with self.assertRaisesRegex(RuntimeError, "正在处理"):
                service.archive_branch(
                    branch.context.world_id,
                    active_world_id="main-world",
                )

    def test_legacy_auto_slot_is_still_marked_resumable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None
            service = WorldBranchService(root, root)
            branch = service.create(engine.context, engine.turn_journal, turn_id)

            # Simulate a desktop world that still has only compatibility
            # exports.  list_worlds must not hide it before load_game imports
            # that slot on first open.
            with session_scope(branch.context.database_url) as session:
                row = (
                    session.query(SaveSlot)
                    .filter_by(world_id=branch.context.world_id, slot_key="slot_000")
                    .one()
                )
                session.delete(row)
            self.assertTrue(service.has_compatible_auto_save(branch.context.world_id))
            listed = service.list_worlds(
                "branch-module",
                active_world_id="main-world",
            )
            entry = next(
                item for item in listed if item["world_id"] == branch.context.world_id
            )
            self.assertTrue(entry["resumable"])

    def test_list_tree_saves_aggregates_tags_and_excludes_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            service = WorldBranchService(root, root)
            branch = service.create(
                engine.context,
                engine.turn_journal,
                turn_id,
                label="岔路",
            )
            engine.save("slot_001")

            # 同模组的无关世界及其存档永远不得进入当前分支树的存档列表。
            unrelated = RuntimeContext.create(
                "unrelated-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )
            save_game(
                [{"role": "user", "content": "另一场冒险"}],
                "slot_001",
                context=unrelated,
            )

            saves = service.list_tree_saves(
                "branch-module",
                active_world_id="main-world",
            )
            by_key = {(save["world_id"], save["id"]): save for save in saves}

            main_save = by_key[("main-world", "slot_001")]
            self.assertEqual("主时间线", main_save["timeline_label"])
            self.assertTrue(main_save["world_active"])

            branch_save = by_key[(branch.context.world_id, "slot_000")]
            self.assertEqual("岔路", branch_save["timeline_label"])
            self.assertFalse(branch_save["world_active"])

            self.assertNotIn(
                "unrelated-world", {save["world_id"] for save in saves}
            )
            # 创建分支先于主世界手动存档，手动存档更新，应排在前面。
            self.assertEqual("slot_001", saves[0]["id"])

            # 归档后的时间线连同其存档一起从列表消失（数据仍保留在库中）。
            service.archive_branch(
                branch.context.world_id,
                active_world_id="main-world",
            )
            remaining = service.list_tree_saves(
                "branch-module",
                active_world_id="main-world",
            )
            self.assertNotIn(
                branch.context.world_id,
                {save["world_id"] for save in remaining},
            )
            self.assertIn(("main-world", "slot_001"), by_key)

    def test_list_adventures_groups_timelines_under_one_save_container(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            service = WorldBranchService(root, root)
            branch = service.create(
                engine.context,
                engine.turn_journal,
                turn_id,
                label="岔路",
            )
            engine.save("slot_001")

            # 另一次互不相干的游玩：同模组、独立主时间线，应成为另一个存档容器。
            unrelated = RuntimeContext.create(
                "unrelated-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )
            save_game(
                [{"role": "user", "content": "另一场冒险"}],
                "slot_000",
                context=unrelated,
            )

            adventures = service.list_adventures(active_world_id="main-world")
            by_root = {item["root_world_id"]: item for item in adventures}
            self.assertEqual({"main-world", "unrelated-world"}, set(by_root))

            main = by_root["main-world"]
            self.assertTrue(main["active"])
            self.assertEqual(2, main["timeline_count"])
            self.assertEqual("main-world", main["resume_world_id"])
            self.assertEqual("branch-module", main["module_name"])
            # 主时间线在前、分支在后；分支带缩进深度与父指针。
            self.assertEqual("main-world", main["timelines"][0]["world_id"])
            self.assertFalse(main["timelines"][0]["is_branch"])
            self.assertEqual(0, main["timelines"][0]["depth"])
            self.assertEqual(1, main["timelines"][0]["save_count"])
            branch_entry = main["timelines"][1]
            self.assertEqual(branch.context.world_id, branch_entry["world_id"])
            self.assertTrue(branch_entry["is_branch"])
            self.assertEqual(1, branch_entry["depth"])
            self.assertEqual("main-world", branch_entry["parent_world_id"])
            self.assertEqual("岔路", branch_entry["label"])
            self.assertTrue(branch_entry["resumable"])

            other = by_root["unrelated-world"]
            self.assertFalse(other["active"])
            self.assertEqual(1, other["timeline_count"])
            self.assertEqual("unrelated-world", other["resume_world_id"])

            # 账号模式过滤：只保留可见世界的树。
            filtered = service.list_adventures(
                active_world_id="main-world",
                allowed_world_ids={"main-world"},
            )
            self.assertEqual(1, len(filtered))
            self.assertEqual("main-world", filtered[0]["root_world_id"])
            self.assertEqual(1, filtered[0]["timeline_count"])

    def test_rename_branch_updates_display_name_and_compat_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None
            service = WorldBranchService(root, root)
            branch = service.create(engine.context, engine.turn_journal, turn_id)

            renamed = service.rename_branch(branch.context.world_id, "  威胁管家之前  ")
            self.assertEqual("威胁管家之前", renamed["label"])
            with session_scope(branch.context.database_url) as session:
                world = session.get(World, branch.context.world_id)
                assert world is not None
                self.assertEqual(
                    "威胁管家之前", dict(world.metadata_json or {})["display_name"]
                )
            compat = json.loads(branch.context.metadata_file.read_text(encoding="utf-8"))
            self.assertEqual("威胁管家之前", compat["display_name"])

            # 空标签回退默认名；主时间线也可重命名（仅显示名）。
            fallback = service.rename_branch(branch.context.world_id, "   ")
            self.assertEqual("时间线分支", fallback["label"])
            root_renamed = service.rename_branch("main-world", "")
            self.assertEqual("主时间线", root_renamed["label"])

            with self.assertRaises(FileNotFoundError):
                service.rename_branch("missing-world", "x")
            service.archive_branch(
                branch.context.world_id,
                active_world_id="main-world",
            )
            with self.assertRaises(FileNotFoundError):
                service.rename_branch(branch.context.world_id, "x")

    def test_list_adventures_numbers_slots_by_creation_and_hides_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "开场。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")

            service = WorldBranchService(root, root)

            # 从未开始过的树（如 switch_module 打开的默认世界）不是存档位。
            RuntimeContext.create(
                "empty-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )
            self.assertTrue(service.is_tree_untouched("empty-world"))

            # 更早创建的另一存档位：created_at 更早 → 编号在前，与最近游玩无关。
            older = RuntimeContext.create(
                "older-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )
            save_game(
                [{"role": "user", "content": "旧冒险"}],
                "slot_000",
                context=older,
            )
            with session_scope(older.database_url) as session:
                world = session.get(World, "older-world")
                assert world is not None
                metadata = dict(world.metadata_json or {})
                metadata["created_at"] = "2026-01-01T00:00:00"
                world.metadata_json = metadata

            adventures = service.list_adventures(active_world_id="main-world")
            roots = [item["root_world_id"] for item in adventures]
            self.assertEqual(["older-world", "main-world"], roots)
            self.assertEqual([1, 2], [item["slot_index"] for item in adventures])
            self.assertNotIn("empty-world", roots)

            older_entry, main_entry = adventures
            self.assertEqual("2026-01-01T00:00:00", older_entry["created_at"])
            # 主世界完成了一个回合；resume 指向它，turn_count 随之得出。
            self.assertEqual("main-world", main_entry["resume_world_id"])
            self.assertEqual(1, main_entry["turn_count"])
            self.assertFalse(service.is_tree_untouched("main-world"))
            self.assertFalse(service.is_tree_untouched("older-world"))

    def test_list_adventures_filters_by_module_and_exposes_slot_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "开场。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None

            # 第二个模组的一次游玩：不应混入 branch-module 的存档位列表。
            other_module = root / "mod" / "other-module"
            other_module.mkdir(parents=True)
            (other_module / "module.md").write_text("# Other", encoding="utf-8")
            (other_module / "world_state_initial.json").write_text(
                json.dumps(
                    {
                        "module": "other-module",
                        "pc": {"name": "另一位", "hp": 8, "max_hp": 8, "san": 40, "max_san": 40, "inventory": []},
                        "current_scene": {"id": "hall", "name": "大厅"},
                        "scene_catalog": {},
                        "npcs": [],
                        "clues_found": {
                            "investigation": [],
                            "event": [],
                            "task": [],
                            "npc": [],
                        },
                        "combat_state": {"active": False},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            other = RuntimeContext.create(
                "other-module-world",
                "other-module",
                project_root=root,
                runtime_root=root,
            )
            save_game(
                [{"role": "user", "content": "另一模组的冒险"}],
                "slot_000",
                context=other,
            )

            service = WorldBranchService(root, root)
            mine = service.list_adventures(
                active_world_id="main-world", module_name="branch-module"
            )
            self.assertEqual(
                ["main-world"], [item["root_world_id"] for item in mine]
            )
            theirs = service.list_adventures(
                active_world_id="main-world", module_name="other-module"
            )
            self.assertEqual(
                ["other-module-world"], [item["root_world_id"] for item in theirs]
            )
            # 编号在模组内各自顺延。
            self.assertEqual(1, mine[0]["slot_index"])
            self.assertEqual(1, theirs[0]["slot_index"])

            # 槽名默认空；rename_slot 后随列表返回并写入兼容导出。
            self.assertEqual("", mine[0]["slot_name"])
            renamed = service.rename_slot("main-world", " 二周目 ")
            self.assertEqual("二周目", renamed["slot_name"])
            mine = service.list_adventures(
                active_world_id="main-world", module_name="branch-module"
            )
            self.assertEqual("二周目", mine[0]["slot_name"])
            compat = json.loads(
                (root / "worlds" / "main-world" / "world.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("二周目", compat["slot_name"])

            # 空标签清除自定义名（回退模组名）；分支/不存在的根不可改名。
            cleared = service.rename_slot("main-world", "   ")
            self.assertEqual("", cleared["slot_name"])
            branch = service.create(engine.context, engine.turn_journal, turn_id)
            with self.assertRaises(ValueError):
                service.rename_slot(branch.context.world_id, "x")
            with self.assertRaises(FileNotFoundError):
                service.rename_slot("missing-world", "x")

    def test_archive_tree_archives_whole_slot_and_protects_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.make_engine(root)
            engine._stream_llm = lambda *_args, **_kwargs: (
                "你记住了这里。\n\n**你可以——**\n1. 继续",
                [],
            )
            engine.handle_action("环顾四周")
            turn_id = engine.turn_journal.latest_completed_id()
            self.assertIsNotNone(turn_id)
            assert turn_id is not None
            service = WorldBranchService(root, root)
            branch = service.create(
                engine.context,
                engine.turn_journal,
                turn_id,
                label="岔路",
            )

            # 另一存档位，作为“当前正在游玩”的世界。
            other = RuntimeContext.create(
                "other-world",
                "branch-module",
                project_root=root,
                runtime_root=root,
            )
            save_game(
                [{"role": "user", "content": "另一场冒险"}],
                "slot_000",
                context=other,
            )

            # 分支 world_id 不能按存档位删除；当前游玩的存档位不可删除。
            with self.assertRaises(ValueError):
                service.archive_tree(
                    branch.context.world_id,
                    active_world_id="other-world",
                )
            with self.assertRaises(ValueError):
                service.archive_tree("main-world", active_world_id="main-world")

            result = service.archive_tree("main-world", active_world_id="other-world")
            self.assertEqual(2, result["count"])
            self.assertEqual(
                {"main-world", branch.context.world_id},
                set(result["archived_world_ids"]),
            )

            # 树内世界全部不可再打开；另一存档位不受影响，编号保持 1。
            with self.assertRaises(FileNotFoundError):
                service.open("main-world")
            with self.assertRaises(FileNotFoundError):
                service.open(branch.context.world_id)
            service.open("other-world")
            adventures = service.list_adventures(active_world_id="other-world")
            self.assertEqual(
                ["other-world"], [item["root_world_id"] for item in adventures]
            )
            self.assertEqual(1, adventures[0]["slot_index"])

    def test_create_root_makes_independent_untouched_worlds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_engine(root)  # 仅初始化模组目录
            service = WorldBranchService(root, root)
            first = service.create_root("branch-module")
            second = service.create_root("branch-module")
            self.assertNotEqual(first.world_id, second.world_id)
            self.assertTrue(service.is_tree_untouched(first.world_id))
            self.assertTrue(service.is_tree_untouched(second.world_id))
            # 空树不产生任何存档位。
            self.assertEqual(
                [], service.list_adventures(active_world_id=first.world_id)
            )

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
