import tempfile
import unittest
from pathlib import Path

from src.editor_projects import (
    EditorProjectConflict,
    EditorProjectError,
    EditorProjectNotFound,
    EditorProjectStore,
)


def project(title: str = "测试模组") -> dict:
    return {
        "editor_version": 2,
        "manifest": {"id": "test.module", "version": "0.1.0", "title": title},
        "module": {"scenes": {}},
        "keeperDocument": "",
        "theme": {},
        "lorebook": None,
    }


class EditorProjectStoreTests(unittest.TestCase):
    def test_revision_conflict_preserves_latest_project(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EditorProjectStore(Path(temp))
            created = store.create(project())
            updated = store.update(created["session_id"], 0, project("新标题"))
            self.assertEqual(1, updated["revision"])
            self.assertEqual("新标题", store.get(created["session_id"])["project"]["manifest"]["title"])
            with self.assertRaises(EditorProjectConflict) as caught:
                store.update(created["session_id"], 0, project("过期窗口"))
            self.assertEqual("新标题", caught.exception.current["project"]["manifest"]["title"])

    def test_list_and_delete_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EditorProjectStore(Path(temp))
            created = store.create(project())
            self.assertEqual(created["session_id"], store.list()[0]["session_id"])
            store.delete(created["session_id"])
            with self.assertRaises(EditorProjectNotFound):
                store.get(created["session_id"])

    def test_rejects_oversized_project(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EditorProjectStore(Path(temp))
            oversized = project()
            oversized["module"]["payload"] = "x" * (8 * 1024 * 1024)
            with self.assertRaises(EditorProjectError):
                store.create(oversized)

    def test_skills_section_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EditorProjectStore(Path(temp))
            with_skills = project()
            with_skills["skills"] = [
                {
                    "name": "house_rules",
                    "body": "# 房规\n\n测试正文",
                    "version": "1.0.0",
                    "description": "测试房规",
                    "required_tools": ["skill_check"],
                }
            ]
            created = store.create(with_skills)
            stored = store.get(created["session_id"])["project"]["skills"][0]
            self.assertEqual("house_rules", stored["name"])
            self.assertEqual(["skill_check"], stored["required_tools"])

            for bad_skill in (
                {"name": "Bad Name!", "body": "x"},  # 非法文件名
                {"name": "ok_name", "body": "x", "script": "rm -rf"},  # 未知字段
                {"name": "ok_name", "body": ""},  # 空正文
                {"name": "ok_name", "body": "x", "required_tools": ["Not A Tool"]},
            ):
                bad = project()
                bad["skills"] = [bad_skill]
                with self.assertRaises(EditorProjectError, msg=str(bad_skill)):
                    store.create(bad)

            duplicate = project()
            duplicate["skills"] = [
                {"name": "dup", "body": "a"},
                {"name": "dup", "body": "b"},
            ]
            with self.assertRaisesRegex(EditorProjectError, "重名"):
                store.create(duplicate)
