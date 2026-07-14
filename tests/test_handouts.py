import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.config import PROJECT_ROOT
from src.engine import EngineCallbacks, GameEngine
from src.handouts import (
    matching_handouts,
    refresh_static_handout_config,
    resolve_handout_asset,
)
from src.module_compiler import compile_module
from src.module_format import ModuleDefinition, ModuleManifest
from src.module_registry import ModuleRegistry, build_package
from src.persistence import save_game
from src.runtime import RuntimeContext


TEMPLATE = PROJECT_ROOT / "examples" / "module-template"


class HandoutContractTests(unittest.TestCase):
    def test_compiler_generates_exact_triggers_and_preserves_fallback(self):
        manifest = ModuleManifest.model_validate_json(
            (TEMPLATE / "manifest.json").read_text(encoding="utf-8")
        )
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["assets"]["npcs"]["lin_portrait"] = {
            "file": "assets/lin.png",
            "label": "林馆长",
        }
        raw["npcs"]["archivist_lin"]["asset_id"] = "lin_portrait"
        raw["assets"]["clues"]["fragment_photo"] = {
            "file": "assets/fragment.png",
            "label": "井边纸片",
            "reveal_on": [{
                "event": "clue_discovered",
                "match_all": ["纸片", "纤维"],
            }],
        }
        raw["clues"]["well_paper_fragment"]["asset_id"] = "fragment_photo"
        module = ModuleDefinition.model_validate(raw)

        world = compile_module(manifest, module).world_state

        self.assertIn({
            "event": "npc_revealed",
            "entity_id": "archivist_lin",
            "match_all": [],
            "match_any": [],
        }, world["asset_map"]["npcs"]["lin_portrait"]["reveal_on"])
        clue_triggers = world["asset_map"]["clues"]["fragment_photo"]["reveal_on"]
        self.assertIn({
            "event": "clue_discovered",
            "entity_id": "well_paper_fragment",
            "match_all": [],
            "match_any": [],
        }, clue_triggers)
        self.assertIn({
            "event": "clue_discovered",
            "entity_id": None,
            "match_all": ["纸片", "纤维"],
            "match_any": [],
        }, clue_triggers)

    def test_trigger_rejects_unknown_entity_reference(self):
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["assets"]["clues"]["fragment_photo"] = {
            "file": "assets/fragment.png",
            "reveal_on": [{
                "event": "scene_entered",
                "entity_id": "missing_scene",
            }],
        }

        with self.assertRaises(ValidationError) as raised:
            ModuleDefinition.model_validate(raw)

        self.assertIn("触发实体不存在", str(raised.exception))

    def test_compiler_warns_when_asset_has_no_reveal_path(self):
        manifest = ModuleManifest.model_validate_json(
            (TEMPLATE / "manifest.json").read_text(encoding="utf-8")
        )
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["assets"]["clues"]["orphan_photo"] = {
            "file": "assets/orphan.png",
        }
        module = ModuleDefinition.model_validate(raw)

        result = compile_module(manifest, module)

        self.assertIn(
            "asset_without_reveal_path",
            {diagnostic.code for diagnostic in result.diagnostics},
        )

    def test_entity_id_resolves_differently_named_asset(self):
        state = {
            "asset_map": {
                "npcs": {
                    "portrait_001": {
                        "file": "lin.png",
                        "reveal_on": [{
                            "event": "npc_revealed",
                            "entity_id": "archivist_lin",
                        }],
                    }
                }
            }
        }

        asset_id, asset = resolve_handout_asset(state, "npc", "archivist_lin")

        self.assertEqual(asset_id, "portrait_001")
        self.assertEqual(asset["file"], "lin.png")

    def test_reconciliation_repairs_assetless_clue_from_updated_template(self):
        state = {
            "clues_found": {
                "investigation": [{
                    "id": "clue_005",
                    "text": "莱特眼球内部有暗红色网状破裂纹路。",
                    "asset": None,
                }]
            },
            "asset_map": {"npcs": {}, "scenes": {}, "clues": {}},
        }
        template = {
            "asset_map": {
                "npcs": {},
                "scenes": {},
                "clues": {
                    "wright_body": {
                        "file": "莱特教授的尸体.png",
                        "label": "莱特教授尸体",
                        "reveal_on": [{
                            "event": "clue_discovered",
                            "match_all": ["莱特", "眼"],
                        }],
                    }
                },
            }
        }

        repaired = refresh_static_handout_config(state, template)

        clue = state["clues_found"]["investigation"][0]
        self.assertEqual(clue["asset"]["id"], "wright_body")
        self.assertEqual(repaired[0]["entity_id"], "clue_005")

    def test_seen_asset_is_not_matched_twice(self):
        state = {
            "asset_map": {
                "npcs": {},
                "scenes": {},
                "clues": {
                    "wright_body": {
                        "file": "body.png",
                        "reveal_on": [{
                            "event": "sanity_triggered",
                            "match_any": ["莱特教授的遗体"],
                        }],
                    }
                },
            },
            "seen_handout_assets": {"clues": ["wright_body"]},
        }

        matches = matching_handouts(
            state,
            "sanity_triggered",
            text="调查员亲眼看见莱特教授的遗体。",
        )

        self.assertEqual(matches, [])


class ScarletLettersHandoutIntegrationTests(unittest.TestCase):
    def _engine(self, runtime_root: Path) -> tuple[GameEngine, list[dict]]:
        context = RuntimeContext.create(
            "handout-test",
            "猩红文档",
            project_root=PROJECT_ROOT,
            runtime_root=runtime_root,
        )
        events: list[dict] = []
        engine = GameEngine.__new__(GameEngine)
        engine.context = context
        engine.cb = EngineCallbacks(on_handout=events.append)
        return engine, events

    def test_body_sanity_event_distributes_image_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))
            args = {
                "description": "打开冷柜，亲眼查看查尔斯·莱特教授的遗体。"
            }

            engine._execute_tool("sanity_trigger", args)
            engine._execute_tool("sanity_trigger", args)

            body_events = [
                event for event in events
                if event.get("asset_id") == "wright_body"
            ]
            self.assertEqual(len(body_events), 1)
            self.assertTrue(events[0]["asset_data_uri"].startswith("data:image/"))
            clue_ids = {
                clue.get("catalog_id") or clue.get("id")
                for clues in engine.context.world_store.load()["clues_found"].values()
                for clue in clues
            }
            self.assertIn("wright_body_evidence", clue_ids)

    def test_combined_sanity_event_resolves_loss_and_handout_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))
            before = engine.context.world_store.load()["pc"]["san"]

            result = json.loads(engine._execute_tool("sanity_event", {
                "description": "打开冷柜，亲眼查看莱特教授异常遗体。",
                "severity": "minor",
                "clue_id": "wright_body_evidence",
                "npc_reveals": [{
                    "npc_id": "john_whitcroft",
                    "tier": 1,
                    "entry_text": "医生面对遗体时明显紧张并回避视线。",
                }],
            }))

            self.assertEqual(result["san_before"], before)
            self.assertEqual(
                engine.context.world_store.load()["pc"]["san"],
                result["san_after"],
            )
            body_events = [
                event for event in events
                if event.get("asset_id") == "wright_body"
            ]
            self.assertEqual(len(body_events), 1)
            self.assertIn(
                "wright_body_evidence",
                result["auto_committed"]["clue_ids"],
            )
            self.assertEqual(
                result["auto_committed"]["flags"],
                {"body_examined": True},
            )
            self.assertEqual(
                result["auto_committed"]["npc_ids"],
                ["john_whitcroft"],
            )
            self.assertTrue(
                engine.context.world_store.load()["flags"]["body_examined"]
            )

    def test_explicit_clue_handout_respects_seen_deduplication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))
            args = {"entity_type": "clue", "entity_id": "wright_body"}

            engine._execute_tool("show_handout", args)
            engine._execute_tool("show_handout", args)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["asset_id"], "wright_body")

    def test_doctor_warning_does_not_reveal_body_handout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            engine._dispatch_narrative_handouts(
                "医生低声说：莱特的尸体不太好看，我建议你不要查看。冷柜仍然关闭。"
            )

            self.assertNotIn("wright_body", {
                event.get("asset_id") for event in events
            })
            clue_ids = {
                clue.get("catalog_id") or clue.get("id")
                for clues in engine.context.world_store.load()["clues_found"].values()
                for clue in clues
            }
            self.assertNotIn("wright_body_evidence", clue_ids)

    def test_assetless_clue_call_is_matched_attached_and_distributed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            output = engine._execute_tool("state_add_clue", {
                "text": "莱特眼球内部有暗红色网状破裂纹路。",
                "category": "investigation",
            })

            clue = json.loads(output)["clue"]
            self.assertEqual(clue["asset"]["id"], "wright_body")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["asset_id"], "wright_body")

    def test_scene_id_write_is_promoted_to_full_catalog_scene(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, _events = self._engine(Path(temp_dir))

            output = engine._execute_tool("state_set", {
                "path": "current_scene.id",
                "value": json.dumps("miskatonic_medical"),
            })

            self.assertTrue(json.loads(output)["ok"])
            scene = engine.context.world_store.load()["current_scene"]
            self.assertEqual(scene["id"], "miskatonic_medical")
            self.assertEqual(scene["name"], "密斯卡托尼克大学医学院")
            self.assertEqual(scene["npcs_present"], ["john_whitcroft"])

    def test_catalog_clue_can_grant_its_physical_item_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, _events = self._engine(Path(temp_dir))

            first = json.loads(engine._execute_tool("state_add_clue", {
                "text": "",
                "category": "investigation",
                "clue_id": "wright_private_diary",
            }))
            second = json.loads(engine._execute_tool("state_add_clue", {
                "text": "",
                "category": "investigation",
                "clue_id": "wright_private_diary",
            }))

            inventory = engine.context.world_store.load()["pc"]["inventory"]
            self.assertTrue(first["item_added"])
            self.assertTrue(second["duplicate"])
            self.assertEqual(inventory.count("莱特的私人日记"), 1)

    def test_duplicate_catalog_clue_repairs_missing_granted_item(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, _events = self._engine(Path(temp_dir))

            def add_legacy_clue(state: dict) -> None:
                state["clues_found"]["investigation"].append({
                    "id": "wright_private_diary",
                    "text": "莱特的私人日记",
                    "asset": None,
                })
                state["pc"]["inventory"] = [
                    item
                    for item in state["pc"]["inventory"]
                    if item != "莱特的私人日记"
                ]

            engine.context.world_store.update(add_legacy_clue)
            result = json.loads(engine._execute_tool("state_add_clue", {
                "text": "",
                "category": "investigation",
                "clue_id": "wright_private_diary",
            }))

            inventory = engine.context.world_store.load()["pc"]["inventory"]
            self.assertTrue(result["duplicate"])
            self.assertTrue(result["item_added"])
            self.assertEqual(inventory.count("莱特的私人日记"), 1)

    def test_loading_old_assetless_clue_repairs_and_distributes_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            engine, events = self._engine(runtime_root)

            def add_old_clue(state: dict) -> None:
                state["clues_found"]["investigation"].append({
                    "id": "clue_005",
                    "text": "莱特眼球内部有暗红色网状破裂纹路。",
                    "asset": None,
                })

            engine.context.world_store.update(add_old_clue)
            save_game(
                [
                    {"role": "system", "content": "old system"},
                    {"role": "assistant", "content": "已经检查了尸体。"},
                ],
                "slot_009",
                context=engine.context,
            )
            engine.messages = [{"role": "system", "content": "new system"}]

            count = engine.load("slot_009")

            self.assertEqual(count, 1)
            self.assertEqual(events[0]["asset_id"], "wright_body")
            repaired = [
                clue
                for clues in engine.context.world_store.load()["clues_found"].values()
                for clue in clues
                if clue.get("id") == "clue_005"
            ]
            self.assertEqual(repaired[0]["asset"]["id"], "wright_body")


class ImportedModuleHandoutIntegrationTests(unittest.TestCase):
    def test_catalog_clue_id_uses_compiled_asset_trigger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(TEMPLATE, source)
            assets = source / "assets"
            assets.mkdir()
            shutil.copy2(
                PROJECT_ROOT / "mod" / "猩红文档" / "assets" / "莱特教授的尸体.png",
                assets / "fragment.png",
            )
            module_path = source / "module.json"
            module = json.loads(module_path.read_text(encoding="utf-8"))
            module["assets"]["clues"]["fragment_photo"] = {
                "file": "assets/fragment.png",
                "label": "井边纸片",
            }
            module["clues"]["well_paper_fragment"]["asset_id"] = "fragment_photo"
            module_path.write_text(
                json.dumps(module, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            package = root / "archive.trpgmod"
            build_package(source, package)
            runtime_root = root / "runtime"
            record, _, _ = ModuleRegistry(PROJECT_ROOT, runtime_root).install(package)
            context = RuntimeContext.create(
                "imported-handout",
                record.key,
                project_root=PROJECT_ROOT,
                runtime_root=runtime_root,
            )
            events: list[dict] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.cb = EngineCallbacks(on_handout=events.append)

            output = engine._execute_tool("state_add_clue", {
                "text": "纸片与手稿使用相同纤维。",
                "category": "investigation",
                "clue_id": "well_paper_fragment",
            })

            clue = json.loads(output)["clue"]
            self.assertEqual(clue["id"], "well_paper_fragment")
            self.assertEqual(clue["asset"]["id"], "fragment_photo")
            self.assertEqual(events[0]["asset_id"], "fragment_photo")


if __name__ == "__main__":
    unittest.main()
