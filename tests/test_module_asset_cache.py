"""Cache policy for versioned module asset URLs (?v=<module version>)."""

import os
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.module_http import ModuleHttpDependencies, create_module_http_router
from src.module_registry import ModuleRegistry


def _asset_client(tmp_path):
    module_dir = tmp_path / "mod" / "demo"
    (module_dir / "assets").mkdir(parents=True)
    (module_dir / "assets" / "bg.png").write_bytes(b"png-bytes")
    record = SimpleNamespace(path=module_dir)
    registry = SimpleNamespace(
        resolve=lambda name: record if name == "demo" else (_ for _ in ()).throw(FileNotFoundError)
    )
    deps = ModuleHttpDependencies(
        registry=lambda: registry,
        project_root=tmp_path,
        runtime_root=lambda: tmp_path,
        active_context=lambda: None,
        set_active_context=lambda context: None,
        auth_required=lambda: False,
    )
    app = FastAPI()
    app.include_router(create_module_http_router(deps))
    return TestClient(app)


def test_versioned_module_asset_is_immutable(tmp_path):
    with _asset_client(tmp_path) as client:
        response = client.get("/api/assets/demo/bg.png?v=1.2.0")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_unversioned_module_asset_falls_back_to_etag(tmp_path):
    with _asset_client(tmp_path) as client:
        response = client.get("/api/assets/demo/bg.png")

    assert response.status_code == 200
    assert "immutable" not in response.headers.get("cache-control", "")
    assert "etag" in response.headers


def test_legacy_module_asset_version_tracks_assets_mtime(tmp_path):
    project_root = tmp_path / "proj"
    module_dir = project_root / "mod" / "demo"
    (module_dir / "assets").mkdir(parents=True)
    (module_dir / "module.md").write_text("# demo", encoding="utf-8")
    (module_dir / "world_state.json").write_text("{}", encoding="utf-8")
    image = module_dir / "assets" / "bg.png"
    image.write_bytes(b"v1")
    os.utime(image, (1_700_000_000, 1_700_000_000))
    registry = ModuleRegistry(project_root, tmp_path / "runtime")

    record = registry.resolve("demo")
    first = record.to_dict()["asset_version"]
    assert record.version == "legacy"
    assert first == "legacy-1700000000"

    os.utime(image, (1_800_000_000, 1_800_000_000))
    assert registry.resolve("demo").to_dict()["asset_version"] != first


def test_installed_module_asset_version_defaults_to_manifest_version(tmp_path):
    from src.module_registry import ModuleRecord

    record = ModuleRecord(
        key="demo@2.3.0",
        package_id="demo",
        version="2.3.0",
        title="demo",
        description="",
        author="",
        system="",
        path=tmp_path,
        source="user",
        format_version="2.0",
    )
    assert record.to_dict()["asset_version"] == "2.3.0"
