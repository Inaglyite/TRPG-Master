import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.editor_api import create_editor_router
from src.editor_projects import EditorProjectStore


def project(title: str = "测试模组") -> dict:
    return {
        "editor_version": 2,
        "manifest": {"id": "test.module", "version": "0.1.0", "title": title},
        "module": {"scenes": {}},
    }


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
