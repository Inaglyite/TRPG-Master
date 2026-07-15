import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import PROJECT_ROOT
from src.combat import combat_action, start_combat
from src.engine import EngineCallbacks, GameEngine
from src.persistence import (
    load_game,
    normalize_tool_message_history,
    restore_snapshot,
    save_game,
)
from src.runtime import RuntimeContext
from src.tools import execute_function
from src.world_store import StaleRevisionError
from src.world_migrations import CURRENT_WORLD_SCHEMA_VERSION


def combat_world() -> dict:
    return {
        "pc": {
            "name": "调查员",
            "hp": 12,
            "max_hp": 12,
            "attributes": {"DEX": 60},
            "skills": {"fighting_brawl": 55, "dodge": 40},
            "inventory": [".38口径左轮手枪（6发）"],
            "conditions": [],
        },
        "npcs": [{
            "id": "cultist",
            "name": "教徒",
            "hp": 9,
            "max_hp": 9,
            "attributes": {"DEX": 70},
            "skills": {"fighting_brawl": 65, "dodge": 35},
            "disposition": "hostile",
            "conditions": [],
        }],
    }


class ReadFileToolSafetyTests(unittest.TestCase):
    def test_read_file_rejects_empty_path_and_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "read-file-safety",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )

            empty = execute_function("read_file", {"path": ""}, context=context)
            directory = execute_function(
                "read_file", {"path": "src"}, context=context
            )

            self.assertIn("路径不能为空", empty)
            self.assertIn("不能读取目录", directory)

    def test_read_file_supports_world_and_module_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "read-file-aliases",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )

            world = json.loads(execute_function(
                "read_file",
                {"path": "modules/mansion_of_madness/world_state.json"},
                context=context,
            ))
            module_text = execute_function(
                "read_file",
                {"path": "modules/mansion_of_madness/module.md"},
                context=context,
            )

            self.assertIn("pc", world)
            self.assertEqual(
                module_text,
                (context.module_dir / "module.md").read_text(encoding="utf-8"),
            )

    def test_tool_history_moves_interrupted_instruction_after_responses(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "one"}},
                    {"id": "call_b", "function": {"name": "two"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "a"},
            {"role": "user", "content": "optional skill"},
            {"role": "tool", "tool_call_id": "call_b", "content": "b"},
        ]

        repaired = normalize_tool_message_history(messages)

        self.assertEqual(
            [message["role"] for message in repaired],
            ["assistant", "tool", "tool", "user"],
        )
        self.assertEqual(repaired[2]["tool_call_id"], "call_b")


class RuntimeIsolationIntegrationTests(unittest.TestCase):
    def _engine(self, context: RuntimeContext) -> GameEngine:
        engine = GameEngine.__new__(GameEngine)
        engine.context = context
        engine.cb = EngineCallbacks()
        engine._preconfirmed_escalation = None
        return engine

    def test_two_engines_alternate_twenty_tool_actions_without_cross_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            context_a = RuntimeContext.create(
                "integration-a",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=runtime_root,
            )
            context_b = RuntimeContext.create(
                "integration-b",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=runtime_root,
            )
            engine_a = self._engine(context_a)
            engine_b = self._engine(context_b)
            combat_npc_id = context_a.world_store.load()["npcs"][0]["id"]

            for action_index in range(10):
                for engine, label, hp, combat_active in (
                    (engine_a, "A", 4, True),
                    (engine_b, "B", 8, False),
                ):
                    kind = action_index % 4
                    if kind == 0:
                        output = engine._execute_tool(
                            "state_set", {"path": "pc.hp", "value": str(hp)}
                        )
                    elif kind == 1:
                        output = engine._execute_tool(
                            "state_add_item", {"item": f"{label}-item-{action_index}"}
                        )
                    elif kind == 2:
                        output = engine._execute_tool(
                            "state_add_clue",
                            {
                                "text": f"{label}-clue-{action_index}",
                                "category": "investigation",
                            },
                        )
                    elif action_index == 3 and label == "A":
                        output = engine._execute_tool(
                            "combat_start",
                            {
                                "participants": [{"id": combat_npc_id}],
                                "reason": "隔离测试",
                            },
                        )
                    elif action_index == 3:
                        output = engine._execute_tool(
                            "state_set",
                            {
                                "path": "combat_state.active",
                                "value": json.dumps(combat_active),
                            },
                        )
                    else:
                        output = engine._execute_tool("combat_status", {})
                    self.assertNotIn("[错误]", output)

            world_a = context_a.world_store.load()
            world_b = context_b.world_store.load()
            items_a = world_a["pc"]["inventory"]
            items_b = world_b["pc"]["inventory"]
            clues_a = json.dumps(world_a["clues_found"], ensure_ascii=False)
            clues_b = json.dumps(world_b["clues_found"], ensure_ascii=False)

            self.assertEqual(world_a["pc"]["hp"], 4)
            self.assertEqual(world_b["pc"]["hp"], 8)
            self.assertTrue(world_a["combat_state"]["active"])
            self.assertFalse(world_b["combat_state"]["active"])
            self.assertTrue(any(str(item).startswith("A-item") for item in items_a))
            self.assertFalse(any(str(item).startswith("B-item") for item in items_a))
            self.assertTrue(any(str(item).startswith("B-item") for item in items_b))
            self.assertFalse(any(str(item).startswith("A-item") for item in items_b))
            self.assertIn("A-clue", clues_a)
            self.assertNotIn("B-clue", clues_a)
            self.assertIn("B-clue", clues_b)
            self.assertNotIn("A-clue", clues_b)


class SaveRestoreIntegrationTests(unittest.TestCase):
    def test_save_slot_cannot_escape_world_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "safe-slots",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            with self.assertRaises(ValueError):
                save_game([], "../../other-world", context=context)

    def test_engine_load_rejects_concurrent_world_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "stale-load",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            old_snapshot = context.world_store.load()
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = [{"role": "system", "content": "system"}]

            def racing_load(_slot_id, *, context):
                context.world_store.update(
                    lambda world: world["pc"].update({"hp": 1})
                )
                return ([{"role": "system", "content": "old"}], old_snapshot)

            with patch("src.engine.load_game", side_effect=racing_load):
                with self.assertRaises(StaleRevisionError):
                    engine.load("slot_001")

            self.assertEqual(context.world_store.load()["pc"]["hp"], 1)

    def test_save_restore_preserves_pending_combat_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "pending-save",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            world = combat_world()
            start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")
            pending_result = combat_action(
                world,
                actor_id="cultist",
                target_id="pc",
                action_type="melee",
                description="教徒挥拳扑来",
            )
            self.assertTrue(pending_result["requires_decision"])
            decision_id = pending_result["decision"]["id"]
            context.world_store.restore(world)
            messages = [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "战斗仍在继续。"},
            ]
            save_game(messages, "slot_001", context=context)
            saved_revision = context.world_store.revision
            context.world_store.update(
                lambda world: world.update({"combat_state": {"active": False}})
            )

            loaded_messages, snapshot = load_game("slot_001", context=context)
            self.assertTrue(restore_snapshot(snapshot, context=context))
            restored = context.world_store.load()

            self.assertEqual(loaded_messages, messages)
            self.assertEqual(
                restored["combat_state"]["pending_decision"]["id"], decision_id
            )
            self.assertEqual(
                restored["combat_state"]["pending_decision"]["action"]["target_id"],
                "pc",
            )
            self.assertGreater(restored["revision"], saved_revision)

            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = loaded_messages
            engine.cb = EngineCallbacks(on_decision=lambda _decision: "dodge")
            engine._preconfirmed_escalation = None
            engine._resume_pending_combat_decision()

            resumed = context.world_store.load()
            self.assertIsNone(resumed["combat_state"]["pending_decision"])
            self.assertEqual(resumed["combat_state"]["phase"], "awaiting_action")
            self.assertIn("恢复的战斗决定已结算", engine.messages[-1]["content"])

    def test_engine_load_migrates_legacy_snapshot_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "legacy-save",
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            slot = context.saves_dir / "slot_009"
            slot.mkdir(parents=True)
            legacy = context.world_store.load()
            legacy.pop("schema_version", None)
            legacy.pop("revision", None)
            legacy.pop("private_memory", None)
            legacy["pc"].pop("psychological_profile", None)
            (slot / "messages.json").write_text(
                json.dumps([
                    {"role": "system", "content": "old system"},
                    {"role": "assistant", "content": "旧冒险仍在继续。"},
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            (slot / "snapshot.json").write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
            )

            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = [{"role": "system", "content": "new system"}]
            engine._round_count = 99
            engine._player_turn_count = 99
            engine._last_summary_player_turn = 99
            engine._tier_last_injected = 99
            engine._last_turn_high_risk = True
            engine._summary_token_estimate = 999
            engine._loaded_optional_skills = {"old"}
            engine._preconfirmed_escalation = {"old": True}

            count = engine.load("slot_009")
            migrated = context.world_store.load()

            self.assertEqual(count, 1)
            self.assertEqual(engine.messages[0]["content"], "new system")
            self.assertEqual(engine.messages[1]["content"], "旧冒险仍在继续。")
            self.assertEqual(
                migrated["schema_version"],
                CURRENT_WORLD_SCHEMA_VERSION,
            )
            self.assertIn("private_memory", migrated)
            self.assertIn("psychological_profile", migrated["pc"])
            self.assertIsNone(engine._preconfirmed_escalation)


if __name__ == "__main__":
    unittest.main()
