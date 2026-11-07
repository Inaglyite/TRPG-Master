import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.runtime import RuntimeContext
from src.modules.module_deletion import delete_module, module_usage
from src.modules.module_registry import ModulePackageError, ModuleRegistry, build_package
from src.storage.database import (
    ContextSession,
    SaveSlot,
    Snapshot,
    World,
    WorldState,
    session_scope,
)
from src.web.module_http import ModuleHttpDependencies, create_module_http_router
from tests.test_module_packages import TEMPLATE


@pytest.fixture
def installed(tmp_path):
    registry = ModuleRegistry(tmp_path, tmp_path)
    package = tmp_path / "test.trpgmod"
    build_package(TEMPLATE, package)
    record, _, _ = registry.install(package)
    context = RuntimeContext.create("test-root", record.key, project_root=tmp_path, runtime_root=tmp_path)
    branch = RuntimeContext.create("test-branch", record.key, project_root=tmp_path, runtime_root=tmp_path)
    with session_scope(context.database_url) as session:
        session.add(World(id="other", module_name=record.package_id + "@2.0.0", metadata_json={}))
        session.get(World, branch.world_id).status = "archived"
        session.add(Snapshot(id="snapshot", world_id=context.world_id, revision=1, state={}))
        session.flush()
        session.add(SaveSlot(id="save", world_id=context.world_id, slot_key="auto", snapshot_id="snapshot"))
        session.add(ContextSession(id="parent", world_id=context.world_id, root_world_id=context.world_id, session_epoch=1))
        session.flush()
        session.add(ContextSession(id="child", world_id=branch.world_id, root_world_id=context.world_id, session_epoch=1, parent_session_id="parent"))
    return registry, package, record, context, branch


def test_delete_removes_worlds_saves_exports_and_allows_same_version_reimport(installed):
    registry, package, record, context, branch = installed
    legacy = registry.runtime_root / "saves" / record.key
    legacy.mkdir(parents=True)
    (legacy / "auto.json").write_text('{}')
    assert module_usage(registry, record.key) == {"world_count": 2, "save_count": 1}
    result = delete_module(registry, record.key)
    assert result == {"world_count": 2, "save_count": 1, "cleanup_pending": False}
    assert not record.path.exists()
    assert not context.world_dir.exists() and not branch.world_dir.exists()
    assert not legacy.exists()
    with session_scope(context.database_url) as session:
        assert list(session.scalars(select(World.id))) == ["other"]
        for model in (WorldState, SaveSlot, Snapshot, ContextSession):
            assert session.query(model).count() == 0
    reinstalled, _, existing = registry.install(package)
    assert reinstalled.key == record.key and not existing
    fresh = RuntimeContext.create("test-root", record.key, project_root=registry.project_root, runtime_root=registry.runtime_root)
    assert fresh.world_store.exists


def test_failed_database_delete_restores_files_and_saves(installed):
    registry, _, record, context, _ = installed
    # A different module referencing this context prevents the transaction from committing.
    with session_scope(context.database_url) as session:
        session.add(ContextSession(id="external", world_id="other", root_world_id="other", session_epoch=1, parent_session_id="parent"))
    with pytest.raises(IntegrityError):
        delete_module(registry, record.key)
    assert record.path.exists() and context.world_dir.exists()
    assert module_usage(registry, record.key) == {"world_count": 2, "save_count": 1}


def test_delete_rejects_linked_world_directory(installed):
    registry, _, record, context, _ = installed
    original = context.world_dir
    moved = original.with_name("outside")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ModulePackageError, match="路径异常"):
        delete_module(registry, record.key)
    assert moved.exists() and record.path.exists()


def test_delete_http_guards_and_preview(installed):
    registry, _, record, context, _ = installed
    current = SimpleNamespace(module_name=record.key)
    hosted = False
    in_use = set()
    app = FastAPI()
    app.include_router(create_module_http_router(ModuleHttpDependencies(
        registry=lambda: registry, project_root=registry.project_root,
        runtime_root=lambda: registry.runtime_root, active_context=lambda: current,
        set_active_context=lambda value: None, auth_required=lambda: hosted,
        modules_in_use=lambda: in_use,
    )))
    with TestClient(app) as client:
        url = f"/api/modules/{record.key}"
        assert client.get("/api/modules/manage").json()["modules"][0]["save_count"] == 1
        assert client.delete(url).status_code == 409
        current.module_name = "other"
        in_use.add(record.key)
        assert client.delete(url).status_code == 409
        in_use.clear()
        hosted = True
        assert client.delete(url).status_code == 403
        assert client.get("/api/modules/manage").status_code == 403
        hosted = False
        assert client.delete(url).status_code == 200
        assert client.delete(url).status_code == 404


def test_builtin_cannot_be_removed(tmp_path):
    path = tmp_path / "mod" / "builtin"
    path.mkdir(parents=True)
    (path / "module.md").write_text("builtin")
    (path / "world_state_initial.json").write_text(json.dumps({}))
    with pytest.raises(ModulePackageError, match="内置"):
        delete_module(ModuleRegistry(tmp_path, tmp_path), "builtin")
    assert path.exists()
