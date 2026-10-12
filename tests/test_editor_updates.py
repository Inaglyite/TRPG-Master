import hashlib
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.editor_updates import EditorUpdateError, EditorUpdateStore
from src.web.frontend_http import mount_editor_bundle


def make_bundle(version="0.3.1", *, extra=None):
    files = {"index.html": '<script src="./assets/new.js"></script>',
             "assets/new.js": "console.log('new')",
             "editor-build.json": json.dumps({"version": version, "editor_api": 1})}
    files.update(extra or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def fixture_store(tmp_path, *, data=None, manifest_overrides=None, prerelease=False):
    bundled = tmp_path / "editor" / "dist"
    (bundled / "assets").mkdir(parents=True)
    (bundled / "index.html").write_text("old editor")
    (bundled / "assets" / "old.js").write_text("old asset")
    (bundled / "editor-build.json").write_text('{"version":"0.3.0","editor_api":1}')
    data = data or make_bundle()
    manifest = {"version": "0.3.1", "editor_api": 1, "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data), "notes": "新版工作台"}
    manifest.update(manifest_overrides or {})
    tag = "editor-web-v0.3.1"
    prefix = f"https://github.com/Inaglyite/TRPG_Mod_Editor/releases/download/{tag}/"
    release = {"tag_name": tag, "draft": False, "prerelease": prerelease, "assets": [
        {"name": name, "browser_download_url": prefix + name}
        for name in ("editor-web-update.json", "editor-web.zip")]}
    requests = []

    def fetch(url, limit, timeout=8):
        requests.append(url)
        if url.endswith("editor-web.zip"):
            return data
        if url.endswith("editor-web-update.json"):
            return json.dumps(manifest).encode()
        return json.dumps([release]).encode()

    return EditorUpdateStore(bundled, tmp_path / "runtime", fetch=fetch), manifest, requests


def test_check_does_not_download_or_change_current_editor(tmp_path):
    store, manifest, requests = fixture_store(tmp_path)
    assert store.check()["update"]["version"] == "0.3.1"
    assert not any(url.endswith(".zip") for url in requests)
    assert store.current() == {"version": "0.3.0", "source": "bundled"}
    assert not store.root.exists()
    store.check()
    assert len(requests) == 2
    store.check(force=True)
    assert len(requests) == 4


def test_install_activates_atomically_and_keeps_bundled_assets(tmp_path):
    store, manifest, _ = fixture_store(tmp_path)
    store.check()
    result = store.install(manifest["version"], manifest["sha256"])
    assert result["current"]["source"] == "downloaded"
    assert (store.bundled / "index.html").read_text() == "old editor"
    assert store.bundle_directory(result["current"]["bundle_id"]) is not None
    reloaded = EditorUpdateStore(store.bundled, tmp_path / "runtime")
    assert reloaded.current() == result["current"]


def test_bad_checksum_cannot_activate(tmp_path):
    store, manifest, _ = fixture_store(tmp_path, manifest_overrides={"sha256": "0" * 64})
    store.check()
    with pytest.raises(EditorUpdateError, match="校验失败"):
        store.install(manifest["version"], manifest["sha256"])
    assert store.current()["source"] == "bundled"


@pytest.mark.parametrize("name", ["../escape.js", "/escape.js", "assets\\escape.js", "C:/escape.js", "evil.exe"])
def test_unsafe_archives_cannot_activate(tmp_path, name):
    store, manifest, _ = fixture_store(tmp_path, data=make_bundle(extra={name: "bad"}))
    store.check()
    with pytest.raises(EditorUpdateError):
        store.install(manifest["version"], manifest["sha256"])
    assert not (store.root / "active.json").exists()
    assert not list(store.root.glob(".install-*"))


def test_incompatible_and_prerelease_updates_are_not_offered(tmp_path):
    store, _, _ = fixture_store(tmp_path, manifest_overrides={"editor_api": 2})
    assert store.check()["update"] is None
    other, _, requests = fixture_store(tmp_path / "other", prerelease=True)
    assert other.check()["update"] is None
    assert len(requests) == 1


def test_mismatched_confirmation_or_expired_offer_cannot_install(tmp_path):
    store, manifest, _ = fixture_store(tmp_path)
    store.check()
    with pytest.raises(EditorUpdateError):
        store.install("0.4.0", manifest["sha256"])
    store._checked_at -= 1801
    with pytest.raises(EditorUpdateError):
        store.install(manifest["version"], manifest["sha256"])


def test_corrupt_download_falls_back_and_full_game_upgrade_takes_precedence(tmp_path):
    store, manifest, _ = fixture_store(tmp_path)
    store.check()
    store.install(manifest["version"], manifest["sha256"])
    directory = store.bundle_directory(store.current()["bundle_id"])
    (store.bundled / "editor-build.json").write_text('{"version":"0.4.0","editor_api":1}')
    assert store.current()["source"] == "bundled"
    (store.bundled / "editor-build.json").write_text('{"version":"0.3.0","editor_api":1}')
    (directory / "index.html").write_text("damaged")
    assert store.current()["source"] == "bundled"


def test_http_version_paths_preserve_old_tabs_and_redirect_new_tabs(tmp_path, monkeypatch):
    store, manifest, _ = fixture_store(tmp_path)
    store.check()
    store.install(manifest["version"], manifest["sha256"])
    app = FastAPI()
    mount_editor_bundle(app, tmp_path, tmp_path / "runtime")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/editor/assets/old.js").text == "old asset"
        response = client.get("/editor/", follow_redirects=False)
        assert response.status_code == 307
        assert client.get(response.headers["location"]).status_code == 200
        assert client.get(response.headers["location"].replace("index.html", "assets/new.js")).status_code == 200
        assert client.get("/api/editor/updates/check").json()["enabled"] is False
        monkeypatch.setenv("TRPG_ENABLE_EDITOR_UPDATES", "1")
        monkeypatch.setenv("TRPG_REQUIRE_AUTH", "1")
        assert client.get("/api/editor/updates/check").json()["enabled"] is False
        assert client.post("/api/editor/updates/install", json=manifest).status_code == 403
        monkeypatch.setenv("TRPG_REQUIRE_AUTH", "0")
        assert client.post("/api/editor/updates/install", json=manifest,
                           headers={"origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/editor/updates/install", json=manifest).status_code == 403
