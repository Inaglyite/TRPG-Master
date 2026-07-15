import json
import unittest

from src.config import PROJECT_ROOT
from src.module_format import ModuleDefinitionV2, parse_module
from src.module_migrations import migrate_v1_to_v2

TEMPLATE = PROJECT_ROOT / "examples" / "module-template"


class ModuleMigrationTests(unittest.TestCase):
    def test_v1_to_v2_is_non_mutating_and_inserts_required_fallbacks(self):
        manifest = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
        module = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        before_manifest = json.loads(json.dumps(manifest))
        before_module = json.loads(json.dumps(module))

        result = migrate_v1_to_v2(
            manifest,
            module,
            essential_clue_ids=["well_paper_fragment"],
        )

        self.assertEqual(before_manifest, manifest)
        self.assertEqual(before_module, module)
        self.assertIsInstance(parse_module(result.module), ModuleDefinitionV2)
        self.assertEqual(["well_paper_fragment"], result.report["essential_clue_ids"])
        self.assertEqual(2, len(result.report["inserted_fallbacks"]))

    def test_migration_rejects_mainline_without_discovery_path(self):
        manifest = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
        module = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        module["clues"]["commission_missing_manuscript"]["initially_known"] = False
        module["initial_state"]["known_clue_ids"] = []

        with self.assertRaisesRegex(ValueError, "没有 discovery_rules"):
            migrate_v1_to_v2(
                manifest,
                module,
                essential_clue_ids=["commission_missing_manuscript"],
            )


if __name__ == "__main__":
    unittest.main()
