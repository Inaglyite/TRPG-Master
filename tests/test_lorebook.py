import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from src.config import PROJECT_ROOT
from src.engine import GameEngine
from src.lorebook import (
    LorebookEnvelope,
    record_lore_usage,
    select_lore,
    validate_lorebook_references,
)
from src.world_migrations import migrate_world_state
from src.world_store import WorldStore

TEMPLATE = PROJECT_ROOT / "examples" / "module-template"


def load_book() -> LorebookEnvelope:
    return LorebookEnvelope.model_validate_json(
        (TEMPLATE / "lorebook.json").read_text(encoding="utf-8")
    )


def world(scene_id: str = "archive_study") -> dict:
    return {
        "module": "example.whispering-archive",
        "current_scene": {
            "id": scene_id,
            "npcs_present": ["archivist_lin"],
        },
        "flags": {"well_opened": False, "manuscript_recovered": False},
        "clues_found": {"investigation": [], "event": [], "task": [], "npc": []},
        "narrative_memory": {"turn_sequence": 0, "recent_lore": []},
    }


class LorebookModelTests(unittest.TestCase):
    def test_v3_template_parses_and_rejects_ungated_private_entry(self):
        book = load_book()
        self.assertEqual(book.spec, "lorebook_v3")
        self.assertEqual(len(book.data.entries), 5)

        raw = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        extension = raw["data"]["entries"][0]["extensions"]["trpg_master"]
        extension["visibility"] = "gated"
        with self.assertRaises(ValidationError):
            LorebookEnvelope.model_validate(raw)

    def test_reference_validation_reports_unknown_scene(self):
        raw = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        raw["data"]["entries"][0]["extensions"]["trpg_master"]["scene_ids"] = [
            "missing_scene"
        ]
        book = LorebookEnvelope.model_validate(raw)

        errors = validate_lorebook_references(
            book,
            scene_ids={"archive_study", "archive_courtyard"},
            npc_ids={"archivist_lin"},
            clue_ids={"well_paper_fragment"},
            flag_ids={"well_opened", "manuscript_recovered"},
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("missing_scene", errors[0])

    def test_old_or_malformed_save_gets_narrative_memory_defaults(self):
        migrated, changed = migrate_world_state({
            "schema_version": 1,
            "revision": 0,
            "narrative_memory": "broken",
        })

        self.assertTrue(changed)
        self.assertEqual(migrated["narrative_memory"], {
            "turn_sequence": 0,
            "recent_lore": [],
        })


class LorebookRetrievalTests(unittest.TestCase):
    def test_scene_group_rotates_with_persisted_cooldown(self):
        book = load_book()
        state = world()

        first = select_lore(book, state, [], "环顾书房")
        self.assertEqual(len(set(first.entry_ids) & {"study-paper", "study-sound"}), 1)
        record_lore_usage(state, first.entry_ids)

        second = select_lore(book, state, [], "继续查看书房")
        first_palette = set(first.entry_ids) & {"study-paper", "study-sound"}
        second_palette = set(second.entry_ids) & {"study-paper", "study-sound"}
        self.assertEqual(len(second_palette), 1)
        self.assertNotEqual(first_palette, second_palette)
        record_lore_usage(state, second.entry_ids)

        self.assertEqual(state["narrative_memory"]["turn_sequence"], 2)
        self.assertTrue(state["narrative_memory"]["recent_lore"])

    def test_keyword_cannot_unlock_gated_fact_before_clue_discovery(self):
        book = load_book()
        state = world()

        hidden = select_lore(book, state, [], "我检查井边的纸片和残片")
        self.assertNotIn("fragment-after-discovery", hidden.entry_ids)

        state["clues_found"]["investigation"].append({
            "id": "well_paper_fragment",
            "text": "井边的残片",
        })
        revealed = select_lore(book, state, [], "我再检查这张纸片")
        self.assertIn("fragment-after-discovery", revealed.entry_ids)

        hidden_trace = {
            item.entry_id: item.reason for item in hidden.trace
        }
        revealed_trace = {
            item.entry_id: item.reason for item in revealed.trace
        }
        self.assertEqual(
            "required_clue_gate",
            hidden_trace["fragment-after-discovery"],
        )
        self.assertEqual("selected", revealed_trace["fragment-after-discovery"])
        self.assertEqual(
            len(book.data.entries),
            sum(hidden.diagnostics["reason_counts"].values()),
        )

    def test_injected_authority_and_lore_are_not_scanned_recursively(self):
        raw = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        raw["data"]["entries"].append({
            "id": "must-not-chain",
            "keys": ["绝密触发词"],
            "content": "不应由上一轮注入内容触发。",
            "extensions": {"trpg_master": {"kind": "style"}},
            "enabled": True,
            "insertion_order": 99,
            "use_regex": False,
            "constant": False,
            "priority": 999,
        })
        book = LorebookEnvelope.model_validate(raw)
        messages = [{
            "role": "user",
            "content": (
                "[玩家行动] 我看看桌面\n\n"
                "[引擎权威状态｜仅供守秘人，不得复述]\n绝密触发词"
            ),
        }]

        selection = select_lore(book, world(), messages, "我继续观察")

        self.assertNotIn("must-not-chain", selection.entry_ids)

    def test_regex_entries_are_preserved_but_not_executed(self):
        raw = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        entry = copy.deepcopy(raw["data"]["entries"][3])
        entry.update({"id": "regex-entry", "keys": ["询.*问"], "use_regex": True})
        raw["data"]["entries"] = [entry]
        book = LorebookEnvelope.model_validate(raw)

        selection = select_lore(book, world(), [], "我询问管理员")

        self.assertEqual(selection.entry_ids, ())

    def test_zero_scan_depth_only_allows_constant_entries(self):
        raw = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        raw["data"]["scan_depth"] = 0
        raw["data"]["entries"] = [raw["data"]["entries"][3]]
        book = LorebookEnvelope.model_validate(raw)

        selection = select_lore(book, world(), [], "我询问林管理员")

        self.assertEqual(selection.entry_ids, ())

    def test_token_budget_skips_oversized_entry(self):
        raw = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        raw["data"]["token_budget"] = 1
        raw["data"]["entries"] = [raw["data"]["entries"][0]]
        book = LorebookEnvelope.model_validate(raw)

        selection = select_lore(book, world(), [], "")

        self.assertEqual(selection.entries, ())
        self.assertEqual(selection.token_estimate, 0)
        self.assertEqual("token_budget", selection.trace[0].reason)

    def test_engine_retrieval_and_cooldown_memory_use_world_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(world())
            engine = GameEngine.__new__(GameEngine)
            engine.context = SimpleNamespace(world_store=store)
            engine.messages = []
            engine._lorebook = load_book()

            selection = engine._retrieve_lore_context("环顾书房")
            self.assertIsNotNone(selection)
            assert selection is not None
            engine._record_lore_usage(selection.entry_ids)

            saved = store.load()
            self.assertEqual(saved["narrative_memory"]["turn_sequence"], 1)
            self.assertEqual(
                {item["id"] for item in saved["narrative_memory"]["recent_lore"]},
                set(selection.entry_ids),
            )


if __name__ == "__main__":
    unittest.main()
