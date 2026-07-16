"""Persistent, revision-checked authoring sessions for TRPG Mod Editor."""

from __future__ import annotations

import copy
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from .world_store import atomic_write_json, file_lock

MAX_EDITOR_PROJECT_BYTES = 8 * 1024 * 1024


class EditorProjectError(RuntimeError):
    pass


class EditorProjectNotFound(EditorProjectError):
    pass


class EditorProjectConflict(EditorProjectError):
    def __init__(self, current: dict):
        super().__init__("工程已被其他窗口更新")
        self.current = current


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class EditorProjectStore:
    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root) / ".editor-projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / ".lock"

    @staticmethod
    def _validate_project(project: object) -> dict:
        if not isinstance(project, dict):
            raise EditorProjectError("工程必须是 JSON object")
        if not isinstance(project.get("manifest"), dict):
            raise EditorProjectError("工程缺少 manifest")
        if not isinstance(project.get("module"), dict):
            raise EditorProjectError("工程缺少 module")
        if len(json.dumps(project, ensure_ascii=False).encode("utf-8")) > MAX_EDITOR_PROJECT_BYTES:
            raise EditorProjectError("工程超过 8 MiB 上限；素材应保存为引用而不是内嵌数据")
        return copy.deepcopy(project)

    @staticmethod
    def _session_id(value: str) -> str:
        if not re.fullmatch(r"editor_[a-f0-9]{24}", value or ""):
            raise EditorProjectNotFound("工程会话不存在")
        return value

    def _path(self, session_id: str) -> Path:
        return self.root / f"{self._session_id(session_id)}.json"

    def create(self, project: object) -> dict:
        now = _now()
        record = {
            "session_id": f"editor_{secrets.token_hex(12)}",
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "project": self._validate_project(project),
        }
        with file_lock(self.lock_path):
            atomic_write_json(self._path(record["session_id"]), record)
        return copy.deepcopy(record)

    def get(self, session_id: str) -> dict:
        with file_lock(self.lock_path):
            path = self._path(session_id)
            if not path.is_file():
                raise EditorProjectNotFound("工程会话不存在")
            return _load_json(path)

    def update(self, session_id: str, expected_revision: object, project: object) -> dict:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise EditorProjectError("expected_revision 必须是整数")
        validated = self._validate_project(project)
        with file_lock(self.lock_path):
            path = self._path(session_id)
            if not path.is_file():
                raise EditorProjectNotFound("工程会话不存在")
            current = _load_json(path)
            if current.get("revision") != expected_revision:
                raise EditorProjectConflict(copy.deepcopy(current))
            current.update({
                "revision": expected_revision + 1,
                "updated_at": _now(),
                "project": validated,
            })
            atomic_write_json(path, current)
            return copy.deepcopy(current)

    def delete(self, session_id: str) -> None:
        with file_lock(self.lock_path):
            path = self._path(session_id)
            if not path.is_file():
                raise EditorProjectNotFound("工程会话不存在")
            path.unlink()

    def list(self) -> list[dict]:
        with file_lock(self.lock_path):
            records = []
            for path in self.root.glob("editor_*.json"):
                try:
                    record = _load_json(path)
                except Exception:
                    continue
                manifest = record.get("project", {}).get("manifest", {})
                records.append({
                    "session_id": record.get("session_id"),
                    "revision": record.get("revision", 0),
                    "updated_at": record.get("updated_at"),
                    "title": manifest.get("title") or "未命名工程",
                    "package_id": manifest.get("id") or "",
                    "version": manifest.get("version") or "",
                })
            return sorted(records, key=lambda item: item.get("updated_at") or "", reverse=True)
