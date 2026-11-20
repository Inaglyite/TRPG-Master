import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.config import PROJECT_ROOT
from src.modules.editor_projects import EditorProjectStore
from src.web.editor_api import create_editor_router


def project(title: str = "测试模组") -> dict:
    return {
        "editor_version": 2,
        "manifest": {"id": "test.module", "version": "0.1.0", "title": title},
        "module": {"scenes": {}},
    }


def template_project(skills: list | None = None) -> dict:
    """examples/module-template 的完整工程 + 可选 skills 段。"""
    template = PROJECT_ROOT / "examples" / "module-template"
    result = {
        "editor_version": 2,
        "manifest": json.loads((template / "manifest.json").read_text(encoding="utf-8")),
        "module": json.loads((template / "module.json").read_text(encoding="utf-8")),
        "keeperDocument": "",
        "theme": {},
        "lorebook": None,
    }
    if skills is not None:
        result["skills"] = skills
    return result


class EditorApiTests(unittest.TestCase):
    def test_crud_and_revision_conflict_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            app = FastAPI()
            app.include_router(create_editor_router(EditorProjectStore(Path(temp))))
            client = TestClient(app)

            created = client.post("/api/editor/projects", json={"project": project()})
            self.assertEqual(201, created.status_code)
            session_id = created.json()["session_id"]

            listed = client.get("/api/editor/projects")
            self.assertEqual(session_id, listed.json()["projects"][0]["session_id"])

            updated = client.patch(
                f"/api/editor/projects/{session_id}",
                json={"expected_revision": 0, "project": project("新标题")},
            )
            self.assertEqual(1, updated.json()["revision"])

            conflict = client.patch(
                f"/api/editor/projects/{session_id}",
                json={"expected_revision": 0, "project": project("过期标题")},
            )
            self.assertEqual(409, conflict.status_code)
            self.assertEqual("revision_conflict", conflict.json()["error_code"])
            self.assertEqual("新标题", conflict.json()["current"]["project"]["manifest"]["title"])

            self.assertEqual(200, client.delete(f"/api/editor/projects/{session_id}").status_code)
            self.assertEqual(404, client.get(f"/api/editor/projects/{session_id}").status_code)

    def test_export_builds_importable_package_with_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            app = FastAPI()
            app.include_router(create_editor_router(EditorProjectStore(Path(temp))))
            client = TestClient(app)

            created = client.post(
                "/api/editor/projects",
                json={
                    "project": template_project(
                        skills=[{"name": "house_rules", "body": "# 房规\n\n测试正文"}]
                    )
                },
            )
            self.assertEqual(201, created.status_code)
            session_id = created.json()["session_id"]

            response = client.post(f"/api/editor/projects/{session_id}/export")
            self.assertEqual(200, response.status_code)
            package = Path(temp) / "out.trpgmod"
            package.write_bytes(response.content)
            with zipfile.ZipFile(package) as archive:
                names = archive.namelist()
                manifest = json.loads(archive.read("manifest.json"))
            # skills 段入包；manifest 自动补声明 custom_skills capability。
            self.assertIn("skills/house_rules.skill", names)
            self.assertIn("custom_skills", manifest["capabilities"])

    def test_export_surfaces_package_validation_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            app = FastAPI()
            app.include_router(create_editor_router(EditorProjectStore(Path(temp))))
            client = TestClient(app)

            broken = template_project()
            broken["manifest"]["id"] = "非法包 ID!"
            created = client.post("/api/editor/projects", json={"project": broken})
            session_id = created.json()["session_id"]

            response = client.post(f"/api/editor/projects/{session_id}/export")
            self.assertEqual(400, response.status_code)
            self.assertFalse(response.json()["ok"])
            self.assertTrue(response.json()["error_code"])

    def test_import_package_roundtrips_exported_project(self):
        with tempfile.TemporaryDirectory() as temp:
            app = FastAPI()
            app.include_router(create_editor_router(EditorProjectStore(Path(temp))))
            client = TestClient(app)

            source = template_project(
                skills=[{"name": "house_rules", "body": "# 房规\n\n测试正文"}]
            )
            source["keeperDocument"] = "# Keeper\n\n线索安排。"
            created = client.post("/api/editor/projects", json={"project": source})
            session_id = created.json()["session_id"]
            exported = client.post(f"/api/editor/projects/{session_id}/export")
            self.assertEqual(200, exported.status_code)

            imported = client.post(
                "/api/editor/projects/import",
                content=exported.content,
                headers={"X-Module-Filename": "roundtrip.trpgmod"},
            )
            self.assertEqual(201, imported.status_code)
            project = imported.json()["project"]
            self.assertEqual(2, project["editor_version"])
            self.assertEqual(source["manifest"]["id"], project["manifest"]["id"])
            self.assertEqual(source["module"], project["module"])
            self.assertEqual("# Keeper\n\n线索安排。", project["keeperDocument"])
            self.assertEqual("house_rules", project["skills"][0]["name"])
            self.assertEqual("# 房规\n\n测试正文", project["skills"][0]["body"])
            self.assertEqual("0", project["skills"][0]["version"])

    def test_import_edit_export_preserves_package_files_and_uploads(self):
        with tempfile.TemporaryDirectory() as temp:
            app = FastAPI()
            store = EditorProjectStore(Path(temp))
            app.include_router(create_editor_router(store))
            client = TestClient(app)
            source = template_project()
            created = client.post("/api/editor/projects", json={"project": source})
            base_package = client.post(
                f"/api/editor/projects/{created.json()['session_id']}/export"
            ).content

            source_path = Path(temp) / "source.trpgmod"
            with zipfile.ZipFile(source_path, "w") as output:
                import io
                with zipfile.ZipFile(io.BytesIO(base_package)) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    module = json.loads(archive.read("module.json"))
                    manifest["capabilities"] = sorted(set(manifest["capabilities"]) | {
                        "bundled_characters", "scene_documents"
                    })
                    manifest["checksums"] = {}
                    module["assets"]["clues"]["photo"] = {
                        "file": "assets/clues/photo.png", "label": "照片", "alt": "照片",
                        "media_type": "image/png", "reveal_on": [],
                    }
                    first_scene = next(iter(module["scenes"]))
                    module["scenes"][first_scene]["document"] = "scenes/arrival.md"
                    for info in archive.infolist():
                        if info.filename == "manifest.json":
                            payload = json.dumps(manifest).encode()
                        elif info.filename == "module.json":
                            payload = json.dumps(module).encode()
                        else:
                            payload = archive.read(info.filename)
                        output.writestr(info, payload)
                    output.writestr("assets/clues/photo.png", b"\x89PNG\r\noriginal-image")
                    output.writestr("characters/investigator.json", b'{"name":"Investigator"}')
                    output.writestr("scenes/arrival.md", b"# Arrival\n")

            imported = client.post(
                "/api/editor/projects/import",
                content=source_path.read_bytes(),
                headers={"X-Module-Filename": "with-files.trpgmod"},
            )
            self.assertEqual(201, imported.status_code, imported.text)
            record = imported.json()
            session_id = record["session_id"]
            self.assertEqual([
                "assets/clues/photo.png", "characters/investigator.json", "scenes/arrival.md"
            ], [item["path"] for item in record["files"]])
            original = client.get(f"/api/editor/projects/{session_id}/files/assets/clues/photo.png")
            self.assertEqual(b"\x89PNG\r\noriginal-image", original.content)

            uploaded = client.put(
                f"/api/editor/projects/{session_id}/files/assets/clues/replacement.webp",
                content=b"replacement-image",
            )
            self.assertEqual(200, uploaded.status_code, uploaded.text)
            edited = record["project"]
            edited["manifest"]["title"] = "保留素材后的版本"
            edited["module"]["assets"]["clues"]["photo"]["file"] = "assets/clues/replacement.webp"
            saved = client.patch(
                f"/api/editor/projects/{session_id}",
                json={"expected_revision": 0, "project": edited},
            )
            self.assertEqual(200, saved.status_code, saved.text)
            exported = client.post(f"/api/editor/projects/{session_id}/export")
            self.assertEqual(200, exported.status_code, exported.text)
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                self.assertEqual(b"\x89PNG\r\noriginal-image", archive.read("assets/clues/photo.png"))
                self.assertEqual(b"replacement-image", archive.read("assets/clues/replacement.webp"))
                self.assertEqual(b'{"name":"Investigator"}', archive.read("characters/investigator.json"))
                self.assertEqual(b"# Arrival\n", archive.read("scenes/arrival.md"))
                self.assertEqual("保留素材后的版本", json.loads(archive.read("manifest.json"))["title"])

            rejected = client.put(
                f"/api/editor/projects/{session_id}/files/../manifest.json", content=b"bad"
            )
            self.assertIn(rejected.status_code, {400, 404})
            self.assertFalse((store.root / "files" / session_id / "manifest.json").exists())

    def test_file_upload_cors_preflight_allows_put(self):
        from server import app
        client = TestClient(app)
        response = client.options(
            "/api/editor/projects/editor_0123456789abcdef01234567/files/assets/clues/photo.png",
            headers={
                "Origin": "http://127.0.0.1:4179",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn("PUT", response.headers["access-control-allow-methods"])

    def test_import_package_rejects_invalid_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            app = FastAPI()
            app.include_router(create_editor_router(EditorProjectStore(Path(temp))))
            client = TestClient(app)

            response = client.post(
                "/api/editor/projects/import",
                content=b"not a zip at all",
                headers={"X-Module-Filename": "broken.trpgmod"},
            )
            self.assertEqual(400, response.status_code)
            self.assertEqual("invalid_archive", response.json()["error_code"])
