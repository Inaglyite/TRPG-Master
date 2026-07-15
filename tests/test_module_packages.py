import json
import shutil
import stat
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.config import PROJECT_ROOT
from src.lorebook import lorebook_json_schema
from src.module_compiler import (
    compile_module,
    compile_payload,
    compile_world_state,
    render_keeper_prompt,
)
from src.module_format import (
    ModuleDefinition,
    ModuleManifest,
    compile_world_state as legacy_compile_world_state,
    manifest_json_schema,
    module_json_schema,
)
from src.module_registry import (
    ModulePackageError,
    ModuleRegistry,
    build_package,
    inspect_package,
)
from src.runtime import RuntimeContext


TEMPLATE = PROJECT_ROOT / "examples" / "module-template"


def load_template():
    manifest = ModuleManifest.model_validate_json(
        (TEMPLATE / "manifest.json").read_text(encoding="utf-8")
    )
    module = ModuleDefinition.model_validate_json(
        (TEMPLATE / "module.json").read_text(encoding="utf-8")
    )
    return manifest, module


def add_zip_entry(package: Path, name: str, payload: bytes, *, mode: int = 0o100644):
    with zipfile.ZipFile(package, "a") as archive:
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16
        archive.writestr(info, payload)


class ModuleFormatTests(unittest.TestCase):
    def test_compile_separates_catalog_from_initially_known_clues(self):
        manifest, module = load_template()
        world = compile_world_state(manifest, module)

        found = [
            clue
            for clues in world["clues_found"].values()
            for clue in clues
        ]
        self.assertEqual([clue["id"] for clue in found], ["commission_missing_manuscript"])
        self.assertEqual(set(world["clue_catalog"]), {
            "commission_missing_manuscript",
            "well_paper_fragment",
        })
        self.assertEqual(world["current_scene"]["id"], "archive_study")
        self.assertEqual(world["module_version"], "1.0.0")
        self.assertEqual(world["module_meta"]["era"], "1920s")
        self.assertEqual(
            world["endings"][0]["required_flags"],
            {"well_opened": True, "manuscript_recovered": True},
        )
        self.assertEqual(world["pc"]["name"], "")

    def test_reference_validation_rejects_missing_scene(self):
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["scenes"]["archive_study"]["exits"] = ["missing_scene"]
        with self.assertRaises(ValidationError) as raised:
            ModuleDefinition.model_validate(raw)
        self.assertIn("不存在的出口", str(raised.exception))

    def test_ending_rejects_unknown_required_flag(self):
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["endings"]["manuscript_recovered"]["required_flags"] = {
            "unknown_flag": True,
        }

        with self.assertRaises(ValidationError) as raised:
            ModuleDefinition.model_validate(raw)

        self.assertIn("required_flags 不存在", str(raised.exception))

    def test_clue_rejects_unknown_flag_effect(self):
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["clues"]["well_paper_fragment"]["flag_effects"] = {
            "unknown_flag": True,
        }

        with self.assertRaises(ValidationError) as raised:
            ModuleDefinition.model_validate(raw)

        self.assertIn("flag_effects 不存在", str(raised.exception))

    def test_discovery_rule_is_compiled_into_runtime_catalog(self):
        manifest, module = load_template()

        world = compile_world_state(manifest, module)

        rules = world["clue_catalog"]["well_paper_fragment"]["discovery_rules"]
        self.assertEqual(rules[0]["intent"], "search")
        self.assertIn("旧井边", rules[0]["approach_text"])
        self.assertEqual(rules[0]["skill"], "spot_hidden")
        self.assertTrue(rules[0]["requires_success"])

    def test_discovery_rule_rejects_unknown_npc_reveal(self):
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        raw["clues"]["well_paper_fragment"]["discovery_rules"][0][
            "npc_reveals"
        ] = [{
            "npc_id": "missing_npc",
            "tier": 1,
            "entry_text": "不存在的人物揭示。",
        }]

        with self.assertRaises(ValidationError) as raised:
            ModuleDefinition.model_validate(raw)

        self.assertIn("发现规则引用了不存在的 NPC", str(raised.exception))

    def test_discovery_rule_requires_skill_for_success_gate(self):
        raw = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        del raw["clues"]["well_paper_fragment"]["discovery_rules"][0]["skill"]

        with self.assertRaises(ValidationError) as raised:
            ModuleDefinition.model_validate(raw)

        self.assertIn("必须指定 skill", str(raised.exception))

    def test_manifest_rejects_wrong_schema_and_reserved_package_id(self):
        raw = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
        raw["$schema"] = "https://example.invalid/schema.json"
        with self.assertRaises(ValidationError):
            ModuleManifest.model_validate(raw)

        raw["$schema"] = "https://trpg-master.local/schemas/module-manifest-v1.json"
        raw["id"] = "con"
        with self.assertRaises(ValidationError):
            ModuleManifest.model_validate(raw)

    def test_generated_schemas_declare_draft_and_stable_ids(self):
        manifest_schema = manifest_json_schema()
        module_schema = module_json_schema()
        lorebook_schema = lorebook_json_schema()
        self.assertEqual(
            manifest_schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertTrue(manifest_schema["$id"].endswith("module-manifest-v1.json"))
        self.assertTrue(module_schema["$id"].endswith("module-v1.json"))
        self.assertTrue(lorebook_schema["$id"].endswith("lorebook-v3.json"))
        self.assertEqual(
            manifest_schema,
            json.loads(
                (PROJECT_ROOT / "schemas/trpgmod/module-manifest-v1.schema.json")
                .read_text(encoding="utf-8")
            ),
        )
        self.assertEqual(
            module_schema,
            json.loads(
                (PROJECT_ROOT / "schemas/trpgmod/module-v1.schema.json")
                .read_text(encoding="utf-8")
            ),
        )
        self.assertEqual(
            lorebook_schema,
            json.loads(
                (PROJECT_ROOT / "schemas/trpgmod/lorebook-v3.schema.json")
                .read_text(encoding="utf-8")
            ),
        )

    def test_generated_keeper_frontmatter_escapes_manifest_text(self):
        manifest, module = load_template()
        manifest = manifest.model_copy(
            update={"title": "档案: 第一卷\n不要截断", "description": "---"}
        )
        prompt = render_keeper_prompt(manifest, module)
        frontmatter = prompt.split("---\n", 1)[1].split("\n---", 1)[0]

        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(parsed["title"], manifest.title)
        self.assertEqual(parsed["description"], manifest.description)

    def test_compiler_result_contains_diagnostics_outputs_and_trace(self):
        manifest, module = load_template()
        keeper_notes = (TEMPLATE / "keeper.md").read_text(encoding="utf-8")

        result = compile_module(manifest, module, keeper_notes)

        self.assertTrue(result.ok)
        self.assertEqual(result.world_state["current_scene"]["id"], "archive_study")
        self.assertIn("# 守秘人正文", result.keeper_prompt)
        self.assertIn(keeper_notes.strip(), result.keeper_prompt)
        trace = {(entry.output_path, entry.source_path) for entry in result.trace}
        self.assertIn(
            ("world_state.current_scene", "module.scenes.archive_study"),
            trace,
        )
        self.assertIn(
            (
                "world_state.clues_found.task[0]",
                "module.clues.commission_missing_manuscript",
            ),
            trace,
        )

    def test_compile_payload_returns_located_validation_diagnostics(self):
        manifest = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
        module = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        module["scenes"]["archive_study"]["description"] = ""

        lorebook = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        preview = compile_payload(manifest, module, lorebook_payload=lorebook)

        self.assertFalse(preview.ok)
        self.assertIsNone(preview.result)
        diagnostics = preview.to_dict()["diagnostics"]
        self.assertTrue(any(
            diagnostic["path"] == "module.scenes.archive_study.description"
            and diagnostic["level"] == "error"
            for diagnostic in diagnostics
        ))
        self.assertIsNone(preview.to_dict()["outputs"])

    def test_blocking_compile_diagnostic_hides_preview_outputs(self):
        manifest = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
        module = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        manifest["min_engine_version"] = "99.0.0"

        lorebook = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        preview = compile_payload(manifest, module, lorebook_payload=lorebook)

        self.assertFalse(preview.ok)
        self.assertIsNotNone(preview.result)
        report = preview.to_dict()
        self.assertIsNone(report["outputs"])
        self.assertTrue(any(
            diagnostic["code"] == "engine_too_old"
            for diagnostic in report["diagnostics"]
        ))

    def test_compile_payload_warns_for_preserved_unsupported_lore_features(self):
        manifest = json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))
        module = json.loads((TEMPLATE / "module.json").read_text(encoding="utf-8"))
        lorebook = json.loads((TEMPLATE / "lorebook.json").read_text(encoding="utf-8"))
        lorebook["data"]["recursive_scanning"] = True
        lorebook["data"]["entries"][0]["use_regex"] = True

        preview = compile_payload(
            manifest,
            module,
            lorebook_payload=lorebook,
        )

        self.assertTrue(preview.ok)
        codes = {item.code for item in preview.diagnostics}
        self.assertIn("recursive_scanning_unsupported", codes)
        self.assertIn("regex_matching_unsupported", codes)

    def test_module_format_compiler_entry_remains_backward_compatible(self):
        manifest, module = load_template()
        self.assertEqual(
            legacy_compile_world_state(manifest, module),
            compile_world_state(manifest, module),
        )


class ModulePackageTests(unittest.TestCase):
    def test_pack_inspect_install_and_runtime_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "example.trpgmod"
            inspection = build_package(TEMPLATE, package)
            self.assertEqual(inspection.module_key, "example.whispering-archive@1.0.0")
            self.assertTrue(inspection.package_sha256)
            self.assertIn("module.json", inspection.files)
            self.assertTrue(inspection.summary()["has_lorebook"])

            runtime_root = root / "runtime"
            registry = ModuleRegistry(PROJECT_ROOT, runtime_root)
            record, installed_inspection, already = registry.install(package)
            self.assertFalse(already)
            self.assertEqual(record.key, inspection.module_key)
            self.assertEqual(installed_inspection.package_sha256, inspection.package_sha256)
            self.assertEqual(registry.resolve(record.key).path, record.path)

            repeated, _, already = registry.install(package)
            self.assertTrue(already)
            self.assertEqual(repeated.path, record.path)

            context = RuntimeContext.local(
                record.key,
                project_root=PROJECT_ROOT,
                runtime_root=runtime_root,
            )
            world = context.world_store.load()
            self.assertEqual(context.module_dir, record.path)
            self.assertEqual(world["module"], "example.whispering-archive")
            self.assertEqual(world["module_version"], "1.0.0")
            self.assertTrue((record.path / "module.md").exists())
            self.assertTrue((record.path / "world_state_initial.json").exists())
            self.assertTrue((record.path / "lorebook.json").exists())
            install_metadata = json.loads(
                (record.path / "install.json").read_text(encoding="utf-8")
            )
            self.assertEqual(install_metadata["compiler_version"], "1.0.0")
            with zipfile.ZipFile(package) as archive:
                self.assertEqual(
                    (record.path / "manifest.json").read_bytes(),
                    archive.read("manifest.json"),
                )
                self.assertEqual(
                    (record.path / "module.json").read_bytes(),
                    archive.read("module.json"),
                )

            metadata = json.loads(context.metadata_file.read_text(encoding="utf-8"))
            metadata.pop("module_id")
            metadata.pop("module_version")
            context.metadata_file.write_text(json.dumps(metadata), encoding="utf-8")
            context.ensure_initialized()
            migrated = json.loads(context.metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(migrated["module_id"], record.package_id)
            self.assertEqual(migrated["module_version"], record.version)

    def test_pack_replaces_stale_workspace_checksums(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(TEMPLATE, source)
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            manifest["checksums"] = {"keeper.md": "0" * 64}
            (source / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            first = root / "rebuilt.trpgmod"
            second = root / "rebuilt-again.trpgmod"
            inspection = build_package(source, first)
            build_package(source, second)
            self.assertNotEqual(inspection.manifest.checksums["keeper.md"], "0" * 64)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_two_versions_install_side_by_side(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_v1 = root / "source-v1"
            source_v2 = root / "source-v2"
            shutil.copytree(TEMPLATE, source_v1)
            shutil.copytree(TEMPLATE, source_v2)
            manifest_v2 = json.loads((source_v2 / "manifest.json").read_text(encoding="utf-8"))
            manifest_v2["version"] = "1.1.0"
            (source_v2 / "manifest.json").write_text(
                json.dumps(manifest_v2, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            package_v1 = root / "v1.trpgmod"
            package_v2 = root / "v2.trpgmod"
            build_package(source_v1, package_v1)
            build_package(source_v2, package_v2)

            registry = ModuleRegistry(PROJECT_ROOT, root / "runtime")
            record_v1, _, _ = registry.install(package_v1)
            record_v2, _, _ = registry.install(package_v2)

            self.assertNotEqual(record_v1.key, record_v2.key)
            self.assertTrue(record_v1.path.exists())
            self.assertTrue(record_v2.path.exists())
            self.assertEqual(
                {record_v1.key, record_v2.key},
                {
                    "example.whispering-archive@1.0.0",
                    "example.whispering-archive@1.1.0",
                },
            )

    def test_concurrent_same_package_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "concurrent.trpgmod"
            build_package(TEMPLATE, package)
            registry = ModuleRegistry(PROJECT_ROOT, root / "runtime")

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(registry.install, [package, package]))

            self.assertEqual(sorted(result[2] for result in results), [False, True])
            self.assertEqual(results[0][0].path, results[1][0].path)

    def test_changed_package_cannot_overwrite_same_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(TEMPLATE, source)
            first = root / "first.trpgmod"
            second = root / "second.trpgmod"
            build_package(source, first)
            (source / "keeper.md").write_text("# changed", encoding="utf-8")
            build_package(source, second)
            registry = ModuleRegistry(PROJECT_ROOT, root / "runtime")
            registry.install(first)
            with self.assertRaises(ModulePackageError) as raised:
                registry.install(second)
            self.assertEqual(raised.exception.code, "version_conflict")

    def test_rejects_newer_engine_requirement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(TEMPLATE, source)
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            manifest["min_engine_version"] = "99.0.0"
            (source / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaises(ModulePackageError) as raised:
                build_package(source, root / "future.trpgmod")
            self.assertEqual(raised.exception.code, "engine_too_old")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "unsafe.trpgmod"
            build_package(TEMPLATE, package)
            add_zip_entry(package, "../assets/escape.png", b"bad")
            with self.assertRaises(ModulePackageError) as raised:
                inspect_package(package)
            self.assertEqual(raised.exception.code, "unsafe_path")

    def test_rejects_symlink_and_executable_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            symlink_package = root / "symlink.trpgmod"
            build_package(TEMPLATE, symlink_package)
            add_zip_entry(
                symlink_package,
                "assets/link.png",
                b"target.png",
                mode=stat.S_IFLNK | 0o777,
            )
            with self.assertRaises(ModulePackageError) as raised:
                inspect_package(symlink_package)
            self.assertEqual(raised.exception.code, "symlink")

            script_package = root / "script.trpgmod"
            build_package(TEMPLATE, script_package)
            add_zip_entry(script_package, "skills/evil.py", b"print('bad')")
            with self.assertRaises(ModulePackageError) as raised:
                inspect_package(script_package)
            self.assertEqual(raised.exception.code, "forbidden_file")

            source = root / "source"
            shutil.copytree(TEMPLATE, source)
            (source / "assets").mkdir()
            (source / "assets" / "linked.png").symlink_to(source / "keeper.md")
            with self.assertRaises(ModulePackageError) as raised:
                build_package(source, root / "source-link.trpgmod")
            self.assertEqual(raised.exception.code, "symlink")

    def test_rejects_nonportable_windows_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "reserved.trpgmod"
            build_package(TEMPLATE, package)
            add_zip_entry(package, "assets/CON.png", b"bad")
            with self.assertRaises(ModulePackageError) as raised:
                inspect_package(package)
            self.assertEqual(raised.exception.code, "unsafe_path")

    def test_rejects_missing_referenced_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            shutil.copytree(TEMPLATE, source)
            module = json.loads((source / "module.json").read_text(encoding="utf-8"))
            module["assets"]["clues"]["well_fragment"] = {
                "file": "assets/missing.png",
                "label": "纸片",
            }
            module["clues"]["well_paper_fragment"]["asset_id"] = "well_fragment"
            (source / "module.json").write_text(
                json.dumps(module, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with self.assertRaises(ModulePackageError) as raised:
                build_package(source, Path(temp_dir) / "missing.trpgmod")
            self.assertEqual(raised.exception.code, "missing_reference")

    def test_custom_skill_requires_capability_and_emits_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            shutil.copytree(TEMPLATE, source)
            (source / "skills").mkdir()
            (source / "skills" / "archive.skill").write_text(
                "# 档案馆守秘提示\n",
                encoding="utf-8",
            )

            with self.assertRaises(ModulePackageError) as raised:
                build_package(source, root / "undeclared.trpgmod")
            self.assertEqual(raised.exception.code, "undeclared_capability")

            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            manifest["capabilities"] = ["custom_skills"]
            (source / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            inspection = build_package(source, root / "declared.trpgmod")
            self.assertIn("自定义 Skill", " ".join(inspection.warnings))


class ModuleImportApiTests(unittest.TestCase):
    def test_http_compile_preview_is_located_and_has_no_runtime_side_effects(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "runtime"
            payload = {
                "manifest": json.loads(
                    (TEMPLATE / "manifest.json").read_text(encoding="utf-8")
                ),
                "module": json.loads(
                    (TEMPLATE / "module.json").read_text(encoding="utf-8")
                ),
                "keeper_document": (TEMPLATE / "keeper.md").read_text(encoding="utf-8"),
                "lorebook": json.loads(
                    (TEMPLATE / "lorebook.json").read_text(encoding="utf-8")
                ),
            }
            with (
                patch.object(server, "RUNTIME_ROOT", runtime_root),
                TestClient(server.app) as client,
            ):
                schema_response = client.get("/api/modules/schema/lorebook-v3")
                self.assertEqual(schema_response.status_code, 200)
                self.assertTrue(
                    schema_response.json()["$id"].endswith("lorebook-v3.json")
                )
                response = client.post("/api/modules/compile", json=payload)

                self.assertEqual(response.status_code, 200)
                report = response.json()
                self.assertTrue(report["ok"])
                self.assertEqual(
                    report["outputs"]["world_state_initial"]["current_scene"]["id"],
                    "archive_study",
                )
                self.assertTrue(report["trace"])

                payload["module"]["scenes"]["archive_study"]["description"] = ""
                invalid = client.post("/api/modules/compile", json=payload).json()
                self.assertFalse(invalid["ok"])
                self.assertIsNone(invalid["outputs"])
                self.assertEqual(
                    invalid["diagnostics"][0]["path"],
                    "module.scenes.archive_study.description",
                )

            self.assertFalse(runtime_root.exists())

    def test_http_inspect_and_import(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "api.trpgmod"
            build_package(TEMPLATE, package)
            registry = ModuleRegistry(PROJECT_ROOT, root / "runtime")
            with (
                patch.object(server, "MODULE_REGISTRY", registry),
                patch.object(server, "RUNTIME_ROOT", root / "runtime"),
                TestClient(server.app) as client,
            ):
                preflight = client.options(
                    "/api/modules/inspect",
                    headers={
                        "Origin": "null",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type,x-module-filename",
                    },
                )
                self.assertEqual(preflight.status_code, 200)
                self.assertEqual(preflight.headers["access-control-allow-origin"], "null")

                payload = package.read_bytes()
                inspected = client.post(
                    "/api/modules/inspect",
                    content=payload,
                    headers={"Content-Type": "application/vnd.trpg-master.module+zip"},
                )
                self.assertEqual(inspected.status_code, 200)
                self.assertEqual(inspected.json()["module"]["title"], "低语档案馆")

                imported = client.post(
                    "/api/modules/import",
                    content=payload,
                    headers={"Content-Type": "application/vnd.trpg-master.module+zip"},
                )
                self.assertEqual(imported.status_code, 201)
                module_key = imported.json()["module"]["id"]
                self.assertEqual(module_key, "example.whispering-archive@1.0.0")

                modules = client.get("/api/modules").json()["modules"]
                self.assertIn(module_key, {module["id"] for module in modules})


if __name__ == "__main__":
    unittest.main()
