import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from src.config import PROJECT_ROOT
from src.discovery import DiscoveryMatch
from src.engine import GameEngine
from src.module_compiler import compile_payload
from src.module_format import (
    MANIFEST_V2_SCHEMA_URI,
    MODULE_V2_SCHEMA_URI,
    ModuleDefinitionV2,
    module_v2_json_schema,
    parse_module,
)
from src.world_migrations import CURRENT_WORLD_SCHEMA_VERSION


TEMPLATE = PROJECT_ROOT / "examples" / "module-template"


def v2_payloads():
    manifest = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
    module = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
    manifest.update({"$schema": MANIFEST_V2_SCHEMA_URI, "format_version": "2.0"})
    manifest["lorebook"] = None
    module.update({
        "$schema": MODULE_V2_SCHEMA_URI,
        "format_version": "2.0",
        "progression": {"essential_clue_ids": ["well_paper_fragment"]},
    })
    for rule in module["clues"]["well_paper_fragment"]["discovery_rules"]:
        rule["fallback"] = {
            "mode": "grant_clue",
            "narrative": "即使没有立即看清，继续翻找仍发现了受损纸片。",
            "cost_clock": "whispers",
            "cost_amount": 1,
        }
    return manifest, module


class ModuleV2Tests(unittest.TestCase):
    def test_mainline_random_gate_requires_authored_fallback(self):
        _manifest, module = v2_payloads()
        del module["clues"]["well_paper_fragment"]["discovery_rules"][0]["fallback"]

        with self.assertRaisesRegex(ValidationError, "缺少 fallback"):
            parse_module(module)

    def test_v2_compiles_fallback_into_current_runtime_ir(self):
        manifest, module = v2_payloads()

        preview = compile_payload(manifest, module)

        self.assertTrue(preview.ok)
        self.assertIsInstance(parse_module(module), ModuleDefinitionV2)
        world = preview.result.world_state
        self.assertEqual(CURRENT_WORLD_SCHEMA_VERSION, world["schema_version"])
        rule = world["clue_catalog"]["well_paper_fragment"]["discovery_rules"][0]
        self.assertEqual("grant_clue", rule["fallback"]["mode"])
        self.assertEqual(MODULE_V2_SCHEMA_URI, module_v2_json_schema()["$id"])

    def test_manifest_and_module_versions_cannot_be_mixed(self):
        manifest, module = v2_payloads()
        manifest.update({
            "$schema": "https://trpg-master.local/schemas/module-manifest-v1.json",
            "format_version": "1.0",
        })

        preview = compile_payload(manifest, module)

        self.assertFalse(preview.ok)
        self.assertTrue(any(
            diagnostic["code"] == "format_version_mismatch"
            for diagnostic in preview.to_dict(include_outputs=False)["diagnostics"]
        ))

    def test_failed_required_check_uses_grant_fallback_and_cost(self):
        engine = GameEngine.__new__(GameEngine)
        calls = []
        clock_world = {
            "case_clocks": {"danger": 2},
            "clue_catalog": {},
        }
        engine.context = SimpleNamespace(
            world_store=SimpleNamespace(load=lambda: clock_world)
        )
        engine._execute_tool = lambda name, args: calls.append((name, args)) or "{}"
        match = DiscoveryMatch(
            "essential",
            {"text": "关键事实", "category": "task", "type": "obvious"},
            {
                "skill": "spot_hidden",
                "requires_success": True,
                "npc_reveals": [],
                "fallback": {
                    "mode": "grant_clue",
                    "cost_clock": "danger",
                    "cost_amount": 1,
                },
            },
        )

        resolved = engine._resolve_discoveries(
            [match],
            {"success": False, "skill": "spot_hidden"},
        )

        self.assertTrue(resolved[0]["discovered"])
        self.assertEqual("grant_clue", resolved[0]["fallback"]["mode"])
        self.assertIn(
            ("state_set", {"path": "case_clocks.danger", "value": "3"}),
            calls,
        )
        self.assertTrue(any(name == "state_add_clue" for name, _args in calls))


if __name__ == "__main__":
    unittest.main()
