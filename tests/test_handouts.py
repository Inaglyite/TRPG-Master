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
    def test_compiler_generates_exact_triggers_and_preserves_legacy_metadata(self):
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

        self.assertEqual(matching_handouts(
            world,
            "clue_discovered",
            text="纸片与手稿使用同一种纤维。",
            entity_type="clue",
        ), [])

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

    def test_compiler_warns_that_text_trigger_cannot_reveal_asset(self):
        manifest = ModuleManifest.model_validate_json(
            (TEMPLATE / "manifest.json").read_text(encoding="utf-8")
        )
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["assets"]["clues"]["unsafe_photo"] = {
            "file": "assets/unsafe.png",
            "reveal_on": [{
                "event": "clue_discovered",
                "match_any": ["尸体", "遗体"],
            }],
        }
        module = ModuleDefinition.model_validate(raw)

        result = compile_module(manifest, module)
        codes = {diagnostic.code for diagnostic in result.diagnostics}

        self.assertIn("text_handout_trigger_ignored", codes)
        self.assertIn("asset_without_reveal_path", codes)

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

    def test_legacy_direct_npc_asset_has_implicit_reveal_trigger(self):
        state = {
            "asset_map": {
                "npcs": {
                    "john_whitcroft": {
                        "file": "doctor.png",
                        "label": "惠特克罗夫特医生",
                    },
                },
                "scenes": {},
                "clues": {},
            },
        }

        matches = matching_handouts(
            state,
            "npc_revealed",
            entity_id="john_whitcroft",
        )

        self.assertEqual(matches, [{
            "entity_type": "npc",
            "entity_id": "john_whitcroft",
            "asset_id": "john_whitcroft",
        }])

    def test_reconciliation_repairs_assetless_clue_from_updated_template(self):
        state = {
            "clues_found": {
                "investigation": [{
                    "id": "clue_005",
                    "catalog_id": "wright_body_evidence",
                    "text": "莱特眼球内部有暗红色网状破裂纹路。",
                    "asset": None,
                }, {
                    "id": "clue_006",
                    "text": "莱特眼球内部有暗红色网状破裂纹路。",
                    "asset": None,
                }],
            },
            "asset_map": {"npcs": {}, "scenes": {}, "clues": {}},
        }
        template = {
            "clue_catalog": {
                "wright_body_evidence": {
                    "id": "wright_body_evidence",
                    "asset": {
                        "id": "wright_body",
                        "file": "莱特教授的尸体.png",
                        "label": "莱特教授尸体",
                    },
                },
            },
            "asset_map": {
                "npcs": {},
                "scenes": {},
                "clues": {
                    "wright_body": {
                        "file": "莱特教授的尸体.png",
                        "label": "莱特教授尸体",
                        "reveal_on": [{
                            "event": "clue_discovered",
                            "entity_id": "wright_body_evidence",
                        }],
                    }
                },
            }
        }

        repaired = refresh_static_handout_config(state, template)

        clue = state["clues_found"]["investigation"][0]
        self.assertEqual(clue["asset"]["id"], "wright_body")
        self.assertEqual(repaired[0]["entity_id"], "clue_005")
        self.assertIsNone(state["clues_found"]["investigation"][1]["asset"])

    def test_text_only_trigger_cannot_authorize_clue_handout(self):
        state = {
            "asset_map": {
                "npcs": {},
                "scenes": {},
                "clues": {
                    "wright_body": {
                        "file": "body.png",
                        "reveal_on": [{
                            "event": "clue_discovered",
                            "match_all": ["莱特"],
                            "match_any": ["尸体", "遗体"],
                        }],
                    },
                },
            },
        }

        matches = matching_handouts(
            state,
            "clue_discovered",
            text="医生说莱特的遗体仍在停尸间。",
            entity_type="clue",
        )

        self.assertEqual(matches, [])

    def test_seen_asset_is_not_matched_twice(self):
        state = {
            "asset_map": {
                "npcs": {},
                "scenes": {},
                "clues": {
                    "wright_body": {
                        "file": "body.png",
                        "reveal_on": [{
                            "event": "clue_discovered",
                            "entity_id": "wright_body_evidence",
                        }],
                    }
                },
            },
            "seen_handout_assets": {"clues": ["wright_body"]},
        }

        matches = matching_handouts(
            state,
            "clue_discovered",
            entity_id="wright_body_evidence",
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

    def test_sanity_advice_cannot_discover_or_show_body(self):
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
            self.assertEqual(body_events, [])
            clue_ids = {
                clue.get("catalog_id") or clue.get("id")
                for clues in engine.context.world_store.load()["clues_found"].values()
                for clue in clues
            }
            self.assertNotIn("wright_body_evidence", clue_ids)
            self.assertFalse(
                engine.context.world_store.load()["flags"]["body_examined"]
            )

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

            blocked = json.loads(engine._execute_tool("show_handout", args))
            self.assertFalse(blocked["found"])
            self.assertEqual(blocked["reason"], "clue_not_discovered")

            engine._execute_tool("state_add_clue", {
                "text": "",
                "category": "investigation",
                "clue_id": "wright_body_evidence",
            })
            engine._execute_tool("show_handout", args)
            engine._execute_tool("show_handout", args)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["asset_id"], "wright_body")

    def test_asset_name_as_free_clue_id_cannot_bypass_handout_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            engine._execute_tool("state_add_clue", {
                "text": "一条与尸检证据无关的普通记录。",
                "category": "task",
                "clue_id": "wright_body",
            })
            result = json.loads(engine._execute_tool("show_handout", {
                "entity_type": "clue",
                "entity_id": "wright_body",
            }))

            self.assertFalse(result["found"])
            self.assertEqual(result["reason"], "clue_not_discovered")
            self.assertEqual(events, [])

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

    def test_offscene_npc_references_do_not_reveal_portraits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            engine._dispatch_narrative_handouts(
                "法伦建议你之后去找惠特克罗夫特医生、艾米莉亚·考特和哈兰德·洛奇。"
            )

            self.assertEqual(
                {event.get("asset_id") for event in events},
                {"bryce_fallon"},
            )

    def test_present_npc_can_reveal_after_scene_transition(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))
            engine._execute_tool("state_set", {
                "path": "current_scene.id",
                "value": json.dumps("miskatonic_medical"),
            })

            engine._dispatch_narrative_handouts(
                "惠特克罗夫特医生站在医学院走廊尽头等你，法伦并不在这里。"
            )

            self.assertIn(
                "john_whitcroft",
                {event.get("asset_id") for event in events},
            )
            self.assertNotIn(
                "bryce_fallon",
                {event.get("asset_id") for event in events},
            )

    def test_free_text_clue_cannot_guess_an_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            output = engine._execute_tool("state_add_clue", {
                "text": "莱特眼球内部有暗红色网状破裂纹路。",
                "category": "investigation",
            })

            clue = json.loads(output)["clue"]
            self.assertIsNone(clue["asset"])
            self.assertEqual(events, [])
            self.assertFalse(
                engine.context.world_store.load()["flags"]["body_examined"]
            )

    def test_morgue_location_clue_does_not_reveal_body_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            output = engine._execute_tool("state_add_clue", {
                "text": (
                    "莱特的死亡证明由约翰·惠特克罗夫特医生签署；"
                    "遗体目前仍在密斯卡托尼克大学医学院停尸间。"
                ),
                "category": "task",
            })

            clue = json.loads(output)["clue"]
            world = engine.context.world_store.load()
            known_ids = {
                known.get("catalog_id") or known.get("id")
                for clues in world["clues_found"].values()
                for known in clues
            }
            self.assertIsNone(clue["asset"])
            self.assertEqual(events, [])
            self.assertNotIn("wright_body_evidence", known_ids)
            self.assertFalse(world["flags"]["body_examined"])
            self.assertNotIn(
                "wright_body",
                world.get("seen_handout_assets", {}).get("clues", []),
            )

    def test_model_cannot_bypass_authored_body_discovery_rule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine, events = self._engine(Path(temp_dir))

            for authored_reference in (
                {"clue_id": "wright_body_evidence"},
                {"asset_id": "wright_body"},
            ):
                with self.subTest(authored_reference=authored_reference):
                    result = json.loads(engine._execute_model_tool(
                        "state_add_clue",
                        {
                            "text": "法伦说遗体目前仍在停尸间。",
                            "category": "task",
                            **authored_reference,
                        },
                        player_action="我留在办公室继续追问法伦。",
                    ))
                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        result["error"],
                        "catalog_clue_not_authorized",
                    )

            world = engine.context.world_store.load()
            self.assertEqual(events, [])
            self.assertFalse(world["flags"]["body_examined"])

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
                    "catalog_id": "wright_body_evidence",
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
