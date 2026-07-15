import json
import multiprocessing
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.engine import GameEngine
from src.runtime import RuntimeContext, default_world_id
from src.world_migrations import (
    CURRENT_WORLD_SCHEMA_VERSION,
    UnsupportedWorldSchemaError,
)
from src.world_store import StaleRevisionError, WorldStore


def increment_world_in_process(world_dir: str, count: int) -> None:
    store = WorldStore(Path(world_dir))
    for _ in range(count):
        store.update(lambda state: state.update({"counter": state["counter"] + 1}))


def base_world(name: str = "调查员") -> dict:
    return {
        "pc": {
            "name": name,
            "hp": 10,
            "max_hp": 10,
            "san": 50,
            "max_san": 50,
            "inventory": [],
        },
        "npcs": [],
        "clues_found": {"investigation": [], "event": [], "task": [], "npc": []},
        "combat_state": {"active": False},
    }


def make_project(root: Path, module: str = "test_module") -> None:
    module_dir = root / "mod" / module
    module_dir.mkdir(parents=True)
    (module_dir / "module.md").write_text("# Test", encoding="utf-8")
    (module_dir / "world_state_initial.json").write_text(
        json.dumps(base_world(), ensure_ascii=False), encoding="utf-8"
    )


class WorldStoreTests(unittest.TestCase):
    def test_schema_migration_and_revision_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            initial = store.initialize(base_world())

            self.assertEqual(initial.revision, 0)
            self.assertEqual(initial.state["schema_version"], CURRENT_WORLD_SCHEMA_VERSION)
            self.assertEqual("aggregate-v2", initial.state["state_meta"]["layout"])

            updated = store.update(
                lambda state: state["pc"].update({"hp": 7}),
                expected_revision=0,
            )
            self.assertEqual(updated.revision, 1)
            self.assertEqual(store.load()["pc"]["hp"], 7)
            with self.assertRaises(StaleRevisionError):
                store.update(lambda state: state["pc"].update({"hp": 1}), expected_revision=0)

    def test_v1_load_creates_immutable_backup_and_migration_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            world_dir = Path(temp_dir) / "world"
            world_dir.mkdir(parents=True)
            original = {**base_world(), "schema_version": 1, "revision": 7}
            (world_dir / "world_state.json").write_text(
                json.dumps(original, ensure_ascii=False),
                encoding="utf-8",
            )
            store = WorldStore(world_dir)

            migrated = store.load()

            backup_path = world_dir / "world_state.v1.migration-backup.json"
            self.assertEqual(original, json.loads(backup_path.read_text(encoding="utf-8")))
            report = json.loads(store.migration_report_path.read_text(encoding="utf-8"))
            self.assertEqual(1, report["from_version"])
            self.assertEqual(CURRENT_WORLD_SCHEMA_VERSION, report["to_version"])
            self.assertEqual(7, report["source_revision"])
            self.assertEqual(CURRENT_WORLD_SCHEMA_VERSION, migrated["schema_version"])
            self.assertEqual("aggregate-v2", migrated["state_meta"]["layout"])

            store.load()
            self.assertEqual(
                original,
                json.loads(backup_path.read_text(encoding="utf-8")),
            )

    def test_future_schema_is_rejected_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            future = base_world()
            future["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION + 1
            with self.assertRaises(UnsupportedWorldSchemaError):
                store.initialize(future)

    def test_concurrent_updates_do_not_get_lost(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            world = base_world()
            world["counter"] = 0
            store.initialize(world)

            def increment_many():
                for _ in range(50):
                    store.update(lambda state: state.update({"counter": state["counter"] + 1}))

            threads = [threading.Thread(target=increment_many) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            snapshot = store.snapshot()
            self.assertEqual(snapshot.state["counter"], 200)
            self.assertEqual(snapshot.revision, 200)

    def test_cross_process_room_lock_prevents_lost_updates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            world_dir = Path(temp_dir) / "world"
            store = WorldStore(world_dir)
            world = base_world()
            world["counter"] = 0
            store.initialize(world)

            process_context = multiprocessing.get_context("spawn")
            processes = [
                process_context.Process(
                    target=increment_world_in_process,
                    args=(str(world_dir), 20),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)

            snapshot = store.snapshot()
            self.assertEqual(snapshot.state["counter"], 40)
            self.assertEqual(snapshot.revision, 40)

    def test_failed_atomic_replace_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(base_world())

            with patch("src.world_store.os.replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    store.update(lambda state: state["pc"].update({"hp": 1}))

            self.assertEqual(store.load()["pc"]["hp"], 10)

    def test_corrupt_primary_recovers_last_valid_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(base_world())
            store.update(lambda state: state["pc"].update({"hp": 8}))
            store.update(lambda state: state["pc"].update({"hp": 6}))
            store.state_path.write_text('{"pc":', encoding="utf-8")

            recovered = store.load()

            self.assertEqual(recovered["pc"]["hp"], 8)
            self.assertEqual(recovered["revision"], 3)
            self.assertEqual(json.loads(store.state_path.read_text(encoding="utf-8"))["pc"]["hp"], 8)


class RuntimeContextTests(unittest.TestCase):
    def test_two_worlds_from_same_module_are_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_project(root)
            first = RuntimeContext.create(
                "room-a", "test_module", project_root=root, runtime_root=root
            )
            second = RuntimeContext.create(
                "room-b", "test_module", project_root=root, runtime_root=root
            )

            def customize_first(state):
                state["pc"].update({"name": "甲", "hp": 3, "inventory": ["钥匙"]})
                state["clues_found"]["investigation"].append({"text": "甲的线索"})
                state["combat_state"].update({"active": True})

            first.world_store.update(customize_first)

            world_a = first.world_store.load()
            world_b = second.world_store.load()
            self.assertEqual(world_a["pc"]["name"], "甲")
            self.assertEqual(world_a["pc"]["inventory"], ["钥匙"])
            self.assertTrue(world_a["combat_state"]["active"])
            self.assertEqual(world_b["pc"]["name"], "调查员")
            self.assertEqual(world_b["pc"]["inventory"], [])
            self.assertFalse(world_b["combat_state"]["active"])
            self.assertEqual(world_b["clues_found"]["investigation"], [])

    def test_local_world_migrates_legacy_state_and_saves_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_project(root)
            module_dir = root / "mod" / "test_module"
            legacy = base_world("旧角色")
            legacy["pc"]["hp"] = 4
            (module_dir / "world_state.json").write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
            )
            legacy_slot = root / "saves" / "test_module" / "slot_000"
            legacy_slot.mkdir(parents=True)
            (legacy_slot / "messages.json").write_text(
                json.dumps([
                    {"role": "system", "content": "旧 system"},
                    {"role": "assistant", "content": "旧存档可以继续。"},
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            legacy_snapshot = json.loads(json.dumps(legacy, ensure_ascii=False))
            (legacy_slot / "snapshot.json").write_text(
                json.dumps(legacy_snapshot, ensure_ascii=False), encoding="utf-8"
            )

            context = RuntimeContext.local(
                "test_module", project_root=root, runtime_root=root
            )

            self.assertEqual(context.world_id, default_world_id("test_module"))
            self.assertEqual(context.world_store.load()["pc"]["name"], "旧角色")
            self.assertTrue((context.saves_dir / "slot_000" / "messages.json").exists())

            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = [{"role": "system", "content": "新 system"}]
            self.assertEqual(engine.load("slot_000"), 1)
            self.assertEqual(engine.messages[-1]["content"], "旧存档可以继续。")
            self.assertEqual(context.world_store.load()["pc"]["hp"], 4)

            # 后续打开只读 worlds/，不再重新覆盖为 legacy 文件。
            context.world_store.update(lambda state: state["pc"].update({"hp": 2}))
            shutil.rmtree(context.saves_dir / "slot_000")
            (module_dir / "world_state.json").write_text(
                json.dumps(base_world("被忽略"), ensure_ascii=False), encoding="utf-8"
            )
            reopened = RuntimeContext.local(
                "test_module", project_root=root, runtime_root=root
            )
            self.assertEqual(reopened.world_store.load()["pc"]["hp"], 2)
            self.assertFalse((reopened.saves_dir / "slot_000").exists())

    def test_reset_never_modifies_module_state_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_project(root)
            module_dir = root / "mod" / "test_module"
            legacy_path = module_dir / "world_state.json"
            legacy_path.write_text(json.dumps(base_world("legacy")), encoding="utf-8")
            initial_before = (module_dir / "world_state_initial.json").read_bytes()
            legacy_before = legacy_path.read_bytes()
            context = RuntimeContext.create(
                "new-game", "test_module", project_root=root, runtime_root=root
            )
            context.world_store.update(lambda state: state["pc"].update({"hp": 1}))

            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.reset()

            self.assertEqual((module_dir / "world_state_initial.json").read_bytes(), initial_before)
            self.assertEqual(legacy_path.read_bytes(), legacy_before)
            self.assertEqual(context.world_store.load()["pc"]["hp"], 10)


if __name__ == "__main__":
    unittest.main()
