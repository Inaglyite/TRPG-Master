import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import PROJECT_ROOT
from src.editor_api import create_editor_router
from src.editor_projects import EditorProjectStore


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
