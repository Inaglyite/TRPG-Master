"""Import, full-text pins, and failed-switch regressions without model calls."""

import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ai.context.lorebook import estimate_text_tokens
from src.ai.skills.skill_manifest import CatalogError, catalog_for
from src.ai.skills.skill_pins import PinUnavailable, read_world_pins
from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.modules.module_registry import ModulePackageError, ModuleRegistry, inspect_package
from src.storage.persistence import load_system_prompt
from src.web.operation_errors import operation_error_message


def make_package(root: Path, content: str) -> Path:
    template = PROJECT_ROOT / "examples" / "module-template"
    manifest = json.loads((template / "manifest.json").read_text())
    manifest["capabilities"] = ["custom_skills"]
    package = root / "long-module.trpgmod"
    with zipfile.ZipFile(package, "w") as archive:
        for path in template.rglob("*"):
            if path.is_file() and path.name != "manifest.json":
                archive.write(path, path.relative_to(template).as_posix())
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("skills/guide.skill", content)
    return package


def test_long_import_keeps_full_text_in_catalog_prompt_and_frozen_pins(tmp_path):
    content = "完整主持资料\n" * 2200 + "\n最后一段不能丢失。"
    assert estimate_text_tokens(content) > 4000
    package = make_package(tmp_path, content)
    runtime = tmp_path / "runtime"
    registry = ModuleRegistry(PROJECT_ROOT, runtime)
    record, _, reused = registry.install(package)
    assert not reused
    assert (record.path / "skills/guide.skill").read_text() == content
    context = RuntimeContext.create(
        "long-module-world", record.key, project_root=PROJECT_ROOT, runtime_root=runtime
    )
    catalog = catalog_for(context)
    entry = next(e for e in catalog.skills if e.trust == "local-author")
    assert entry.max_context_tokens == estimate_text_tokens(content)
    assert entry.trust == "local-author"
    assert content in load_system_prompt(context, profile="full")
    pins = read_world_pins(context)
    assert pins[entry.id].content == content
    # Disk edits must not silently replace already frozen contents.
    (record.path / "skills/guide.skill").write_text("后来编辑的内容")
    assert content in load_system_prompt(context, profile="full")


def test_oversized_skill_rejected_at_inspect_and_install_without_partial_package(tmp_path):
    package = make_package(tmp_path, "过长的主持资料" * 20000)
    with pytest.raises(ModulePackageError, match="skills/guide.skill.*单篇上限"):
        inspect_package(package)
    registry = ModuleRegistry(PROJECT_ROOT, tmp_path / "runtime")
    with pytest.raises(ModulePackageError) as failure:
        registry.install(package)
    assert failure.value.code == "skill_budget_exceeded"
    assert not registry.user_root.exists()


def test_existing_installed_skill_over_limit_still_fails_at_runtime(tmp_path):
    registry = ModuleRegistry(PROJECT_ROOT, tmp_path / "runtime")
    record, _, _ = registry.install(make_package(tmp_path, "合法正文"))
    (record.path / "skills/guide.skill").write_text("过长的主持资料" * 20000)
    context = RuntimeContext.create(
        "oversized-world", record.key, project_root=PROJECT_ROOT,
        runtime_root=tmp_path / "runtime",
    )
    with pytest.raises(CatalogError, match="单篇上限"):
        catalog_for(context)


@pytest.mark.parametrize("failure_stage", ["prompt", "after_prepare"])
def test_failed_switch_keeps_old_context_history_journal_and_caches(tmp_path, failure_stage):
    old = RuntimeContext.create(
        "old-world", "mansion_of_madness", project_root=PROJECT_ROOT, runtime_root=tmp_path
    )
    target = RuntimeContext.create(
        "target-world", "mansion_of_madness", project_root=PROJECT_ROOT, runtime_root=tmp_path
    )
    with patch("src.structured.engine_gate.OpenAI", return_value=object()):
        engine = GameEngine(old)
    engine.prepare_session()
    engine.messages.append({"role": "user", "content": "保留我的对话"})
    previous = engine.__dict__.copy()
    prepare = engine.prepare_session

    def fail_after_prepare():
        prepare()
        raise PinUnavailable("准备后的失败")

    failure = (
        patch("src.app.engine.load_system_prompt", side_effect=PinUnavailable("坏目录"))
        if failure_stage == "prompt"
        else patch.object(engine, "prepare_session", side_effect=fail_after_prepare)
    )
    with failure:
        with pytest.raises(PinUnavailable):
            engine.switch_context(target)
    assert engine.__dict__.keys() == previous.keys()
    for key, value in previous.items():
        assert engine.__dict__[key] is value, key
    engine.switch_context(target)
    assert engine.context is target
    assert engine.turn_journal.world_id == target.world_id
    assert len(engine.messages) == 1


def test_module_load_error_is_actionable_but_unexpected_errors_stay_private():
    assert "单篇上限" in operation_error_message(
        "switch_module", PinUnavailable("Skill 正文超过单篇上限")
    )
    assert "private" not in operation_error_message(
        "switch_module", RuntimeError("private diagnostic")
    )
