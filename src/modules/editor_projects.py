"""Persistent, revision-checked authoring sessions for TRPG Mod Editor."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import shutil
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.ai.skills.skill_manifest import TOOL_NAME_RE
from src.storage.world_store import atomic_write_bytes, atomic_write_json, file_lock

MAX_EDITOR_PROJECT_BYTES = 8 * 1024 * 1024
_MAX_SKILL_BODY_BYTES = 256 * 1024
_MAX_SKILLS_PER_PROJECT = 64
_SKILL_NAME_RE = re.compile(r"^[a-z0-9_]{1,80}$")


class EditorSkillDraft(BaseModel):
    """模组工程里的自定义 Skill 草稿（H3.1 作者契约）。

    导出时写为包内 ``skills/<name>.skill``，安装后以 local-author trust、
    固定预算进入运行时 catalog。声明字段（required/allowed_tools）随工程
    保存并做形状校验；模组格式 v2 的 per-Skill manifest 落地前，它们不
    参与运行时行为——编辑器不得把它们宣传为已生效权限。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1)
    version: str = Field(default="0", max_length=80)
    description: str = Field(default="", max_length=120)
    required_tools: list[str] = Field(default_factory=list, max_length=64)
    allowed_tools: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not _SKILL_NAME_RE.fullmatch(value):
            raise ValueError("Skill 文件名只能含小写字母/数字/下划线")
        return value

    @field_validator("body")
    @classmethod
    def _body_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_SKILL_BODY_BYTES:
            raise ValueError("单个 Skill 正文超过 256 KiB")
        return value

    @field_validator("required_tools", "allowed_tools")
    @classmethod
    def _tool_names(cls, value: list[str], info) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not TOOL_NAME_RE.fullmatch(item):
                raise ValueError(f"{info.field_name} 包含非法工具名")
        return value


def editor_skill_json_schema() -> dict[str, Any]:
    schema = EditorSkillDraft.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://trpggame.xyz/schemas/trpgmod/editor-skill-v1.schema.json"
    return schema


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _editor_file_name(value: object) -> str:
    from src.modules.module_registry import (
        ModulePackageError,
        _is_allowed_package_file,
        _safe_archive_name,
    )

    try:
        name = _safe_archive_name(value) if isinstance(value, str) else None
    except ModulePackageError as exc:
        raise EditorProjectError(exc.message) from exc
    if (
        not name
        or PurePosixPath(name).parts[0] not in {"assets", "characters", "scenes"}
        or not _is_allowed_package_file(name)
    ):
        raise EditorProjectError("工程文件只能位于 assets/、characters/ 或 scenes/，且扩展名受支持")
    return name


def export_project_package(
    project: object,
    work_dir: Path,
    package_files: Mapping[str, Path] | None = None,
) -> tuple[Path, Any]:
    """把工程文本与独立保存的包内文件编译为可导入的 .trpgmod。"""
    from src.modules.module_registry import build_package

    validated = EditorProjectStore._validate_project(project)
    work_dir = Path(work_dir)
    source = work_dir / "source"
    source.mkdir(parents=True, exist_ok=True)
    manifest = copy.deepcopy(validated["manifest"])
    skills = validated.get("skills") or []
    capabilities = {str(item) for item in manifest.get("capabilities") or []}
    if skills:
        capabilities.add("custom_skills")
    for raw_name, source_path in (package_files or {}).items():
        name = _editor_file_name(raw_name)
        if not source_path.is_file():
            raise EditorProjectError(f"工程文件不存在: {name}")
        target = source / Path(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
    if any(name.startswith("characters/") for name in (package_files or {})):
        capabilities.add("bundled_characters")
    if any(name.startswith("scenes/") for name in (package_files or {})):
        capabilities.add("scene_documents")
    manifest["capabilities"] = sorted(capabilities)
    _write_json(source / "manifest.json", manifest)
    _write_json(source / "module.json", validated["module"])
    keeper_text = validated.get("keeperDocument")
    if manifest.get("keeper_document"):
        (source / "keeper.md").write_text(
            keeper_text if isinstance(keeper_text, str) else "", encoding="utf-8"
        )
    if manifest.get("theme"):
        theme = validated.get("theme")
        _write_json(source / "theme.json", theme if isinstance(theme, dict) else {})
    if manifest.get("lorebook"):
        lorebook = validated.get("lorebook")
        if not isinstance(lorebook, dict):
            lorebook = {"spec": "lorebook_v3", "data": {"extensions": {}, "entries": []}}
        _write_json(source / "lorebook.json", lorebook)
    if skills:
        skills_dir = source / "skills"
        skills_dir.mkdir()
        for item in skills:
            draft = EditorSkillDraft.model_validate(item)
            (skills_dir / f"{draft.name}.skill").write_text(draft.body, encoding="utf-8")
    package_path = work_dir / "package.trpgmod"
    inspection = build_package(source, package_path)
    return package_path, inspection


def read_project_package(package_path: Path) -> tuple[dict, dict[str, bytes]]:
    """校验并拆分模组包；JSON 进入工程，其余文件保持原始字节。"""
    from src.modules.module_registry import inspect_package

    inspection = inspect_package(package_path)
    names = set(inspection.files)
    with zipfile.ZipFile(package_path) as archive:
        def read_text(name: str) -> str:
            return archive.read(name).decode("utf-8")

        project: dict[str, Any] = {
            "editor_version": 2,
            "manifest": json.loads(read_text("manifest.json")),
            "module": json.loads(read_text("module.json")),
            "keeperDocument": read_text("keeper.md") if "keeper.md" in names else "",
            "theme": json.loads(read_text("theme.json")) if "theme.json" in names else {},
        }
        if "lorebook.json" in names:
            project["lorebook"] = json.loads(read_text("lorebook.json"))
        skills = []
        for name in sorted(names):
            if name.startswith("skills/") and name.endswith(".skill"):
                skills.append({
                    "name": PurePosixPath(name).stem,
                    "body": read_text(name),
                    "version": "0",
                    "description": "",
                    "required_tools": [],
                    "allowed_tools": [],
                })
        if skills:
            project["skills"] = skills
        files = {
            name: archive.read(name)
            for name in sorted(names)
            if PurePosixPath(name).parts[0] in {"assets", "characters", "scenes"}
        }
    return project, files


def project_from_package(package_path: Path) -> dict:
    return read_project_package(package_path)[0]


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
        skills = project.get("skills")
        if skills is not None:
            if not isinstance(skills, list) or len(skills) > _MAX_SKILLS_PER_PROJECT:
                raise EditorProjectError("skills 必须是不超过 64 条的数组")
            names: set[str] = set()
            for index, item in enumerate(skills):
                try:
                    draft = EditorSkillDraft.model_validate(item)
                except ValidationError as exc:
                    first = exc.errors(include_url=False)[0]
                    location = ".".join(str(part) for part in first.get("loc", []))
                    raise EditorProjectError(
                        f"skills[{index}]{'.' + location if location else ''} "
                        f"{first.get('msg', '格式错误')}"
                    ) from exc
                if draft.name in names:
                    raise EditorProjectError(f"skills[{index}] 与前面的 Skill 重名: {draft.name}")
                names.add(draft.name)
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

    def _files_root(self, session_id: str) -> Path:
        return self.root / "files" / self._session_id(session_id)

    def _file_path(self, session_id: str, name: object) -> Path:
        safe_name = _editor_file_name(name)
        root = self._files_root(session_id).resolve()
        path = (root / Path(*PurePosixPath(safe_name).parts)).resolve()
        if not path.is_relative_to(root):
            raise EditorProjectError("工程文件路径不安全")
        return path

    def create(
        self,
        project: object,
        files: Mapping[str, bytes] | None = None,
        source_session_id: object = None,
    ) -> dict:
        now = _now()
        record = {
            "session_id": f"editor_{secrets.token_hex(12)}",
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "project": self._validate_project(project),
        }
        with file_lock(self.lock_path):
            try:
                atomic_write_json(self._path(record["session_id"]), record)
                if source_session_id is not None:
                    source_files = self._file_paths_unlocked(str(source_session_id))
                    for name, path in source_files.items():
                        self._write_file_unlocked(record["session_id"], name, path.read_bytes())
                for name, payload in (files or {}).items():
                    self._write_file_unlocked(record["session_id"], name, payload)
            except BaseException:
                self._path(record["session_id"]).unlink(missing_ok=True)
                shutil.rmtree(self._files_root(record["session_id"]), ignore_errors=True)
                raise
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

    def _file_paths_unlocked(self, session_id: str) -> dict[str, Path]:
        if not self._path(session_id).is_file():
            raise EditorProjectNotFound("工程会话不存在")
        root = self._files_root(session_id)
        if not root.is_dir():
            return {}
        return {
            path.relative_to(root).as_posix(): path
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    def file_paths(self, session_id: str) -> dict[str, Path]:
        with file_lock(self.lock_path):
            return self._file_paths_unlocked(session_id)

    def list_files(self, session_id: str) -> list[dict]:
        return [
            {
                "path": name,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in sorted(self.file_paths(session_id).items())
        ]

    def _write_file_unlocked(self, session_id: str, name: object, payload: bytes) -> dict:
        from src.modules.module_registry import (
            MAX_PACKAGE_FILES,
            MAX_SINGLE_FILE_BYTES,
            MAX_UNCOMPRESSED_BYTES,
        )

        if not isinstance(payload, bytes):
            raise EditorProjectError("工程文件必须是二进制内容")
        if len(payload) > MAX_SINGLE_FILE_BYTES:
            raise EditorProjectError("单个工程文件超过 32 MiB 上限")
        path = self._file_path(session_id, name)
        safe_name = _editor_file_name(name)
        existing = self._file_paths_unlocked(session_id)
        collision = next(
            (item for item in existing if item.casefold() == safe_name.casefold() and item != safe_name),
            None,
        )
        if collision:
            raise EditorProjectError(f"文件路径与现有文件冲突: {collision}")
        total = sum(item.stat().st_size for key, item in existing.items() if key != safe_name)
        if safe_name not in existing and len(existing) >= MAX_PACKAGE_FILES:
            raise EditorProjectError("工程文件数量超过上限")
        if total + len(payload) > MAX_UNCOMPRESSED_BYTES:
            raise EditorProjectError("工程文件解压后总大小超过 256 MiB 上限")
        atomic_write_bytes(path, payload)
        return {"path": safe_name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

    def write_file(self, session_id: str, name: object, payload: bytes) -> dict:
        with file_lock(self.lock_path):
            if not self._path(session_id).is_file():
                raise EditorProjectNotFound("工程会话不存在")
            return self._write_file_unlocked(session_id, name, payload)

    def delete_file(self, session_id: str, name: object) -> None:
        with file_lock(self.lock_path):
            if not self._path(session_id).is_file():
                raise EditorProjectNotFound("工程会话不存在")
            path = self._file_path(session_id, name)
            if not path.is_file() or path.is_symlink():
                raise EditorProjectNotFound("工程文件不存在")
            path.unlink()
            for parent in path.parents:
                if parent == self._files_root(session_id):
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break

    def delete(self, session_id: str) -> None:
        with file_lock(self.lock_path):
            path = self._path(session_id)
            if not path.is_file():
                raise EditorProjectNotFound("工程会话不存在")
            path.unlink()
            shutil.rmtree(self._files_root(session_id), ignore_errors=True)

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
