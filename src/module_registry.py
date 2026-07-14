"""内置/用户模组注册表与安全 .trpgmod 包安装。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from pydantic import ValidationError

from .lorebook import LorebookEnvelope, validate_lorebook_references
from .module_compiler import compile_module
from .module_format import (
    ModuleDefinition,
    ModuleManifest,
    engine_supports,
    is_portable_path_component,
)
from .world_store import atomic_write_json


MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_FILES = 1024

_ROOT_FILES = {
    "manifest.json",
    "module.json",
    "keeper.md",
    "theme.json",
    "lorebook.json",
}
_ALLOWED_EXTENSIONS = {
    "assets": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".mp3", ".ogg", ".wav"},
    "skills": {".skill"},
    "characters": {".json"},
    "scenes": {".md"},
}


class ModulePackageError(ValueError):
    def __init__(self, code: str, message: str, *, details: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


def _validation_details(exc: ValidationError) -> list[str]:
    details = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error.get("loc", [])) or "$"
        details.append(f"{location}: {error.get('msg', '格式错误')}")
    return details


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_name(raw_name: str) -> str | None:
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise ModulePackageError("unsafe_path", f"包内路径不安全: {raw_name!r}")
    if raw_name.endswith("/"):
        raw_name = raw_name[:-1]
        if not raw_name:
            return None
    path = PurePosixPath(raw_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ModulePackageError("unsafe_path", f"包内路径不安全: {raw_name!r}")
    if path.parts[0] == "__MACOSX" or path.name in {".DS_Store", "Thumbs.db"}:
        return None
    if any(not is_portable_path_component(part) for part in path.parts):
        raise ModulePackageError("unsafe_path", f"包内路径无法跨平台使用: {raw_name!r}")
    return path.as_posix()


def _is_allowed_package_file(name: str) -> bool:
    path = PurePosixPath(name)
    if len(path.parts) == 1:
        return name in _ROOT_FILES
    root = path.parts[0]
    extensions = _ALLOWED_EXTENSIONS.get(root)
    return bool(extensions and path.suffix.lower() in extensions)


def _check_zip_info(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise ModulePackageError("encrypted_file", f"不支持加密文件: {info.filename}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise ModulePackageError("symlink", f"模组包不能包含符号链接: {info.filename}")
    if info.file_size > MAX_SINGLE_FILE_BYTES:
        raise ModulePackageError("file_too_large", f"单个文件过大: {info.filename}")
    if info.compress_size and info.file_size > 1024 * 1024:
        if info.file_size / info.compress_size > 200:
            raise ModulePackageError("compression_bomb", f"文件压缩比异常: {info.filename}")


@dataclass(frozen=True)
class ModuleRecord:
    key: str
    package_id: str
    version: str
    title: str
    description: str
    author: str
    system: str
    path: Path
    source: str
    format_version: str
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.key,
            "package_id": self.package_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "system": self.system,
            "source": self.source,
            "format_version": self.format_version,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class PackageInspection:
    manifest: ModuleManifest
    module: ModuleDefinition
    keeper_notes: str
    theme: dict
    lorebook: LorebookEnvelope | None
    files: tuple[str, ...]
    package_sha256: str
    warnings: tuple[str, ...] = ()

    @property
    def module_key(self) -> str:
        return f"{self.manifest.id}@{self.manifest.version}"

    def summary(self) -> dict:
        return {
            "module_key": self.module_key,
            "package_id": self.manifest.id,
            "version": self.manifest.version,
            "title": self.manifest.title,
            "author": self.manifest.author,
            "description": self.manifest.description,
            "system": self.manifest.system,
            "capabilities": self.manifest.capabilities,
            "has_lorebook": self.lorebook is not None,
            "file_count": len(self.files),
            "package_sha256": self.package_sha256,
            "warnings": list(self.warnings),
        }


class ModuleRegistry:
    def __init__(self, project_root: Path, runtime_root: Path):
        self.project_root = Path(project_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.builtin_root = self.project_root / "mod"
        self.user_root = self.runtime_root / "modules"
        self._install_lock = threading.RLock()

    def _legacy_record(self, path: Path) -> ModuleRecord:
        theme = {}
        theme_file = path / "theme.json"
        if theme_file.exists():
            try:
                loaded = json.loads(theme_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    theme = loaded
            except (OSError, json.JSONDecodeError):
                pass
        return ModuleRecord(
            key=path.name,
            package_id=path.name,
            version="legacy",
            title=str(theme.get("title") or path.name),
            description=str(theme.get("description") or ""),
            author="",
            system="",
            path=path.resolve(),
            source="builtin",
            format_version="legacy",
        )

    def _installed_record(self, path: Path) -> ModuleRecord | None:
        manifest_file = path / "manifest.json"
        if not manifest_file.exists() or not (path / "module.md").exists():
            return None
        try:
            manifest = ModuleManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return None
        return ModuleRecord(
            key=f"{manifest.id}@{manifest.version}",
            package_id=manifest.id,
            version=manifest.version,
            title=manifest.title,
            description=manifest.description,
            author=manifest.author,
            system=manifest.system,
            path=path.resolve(),
            source="user",
            format_version=manifest.format_version,
            capabilities=tuple(manifest.capabilities),
        )

    def list_modules(self) -> list[ModuleRecord]:
        records: dict[str, ModuleRecord] = {}
        if self.builtin_root.is_dir():
            for path in sorted(self.builtin_root.iterdir()):
                if path.is_dir() and (path / "module.md").exists() and (
                    (path / "world_state_initial.json").exists()
                    or (path / "world_state.json").exists()
                ):
                    record = self._legacy_record(path)
                    records[record.key] = record
        if self.user_root.is_dir():
            for package_dir in sorted(self.user_root.iterdir()):
                if not package_dir.is_dir() or package_dir.name.startswith("."):
                    continue
                for version_dir in sorted(package_dir.iterdir()):
                    if version_dir.is_dir():
                        record = self._installed_record(version_dir)
                        if record:
                            records[record.key] = record
        return sorted(records.values(), key=lambda item: (item.title.casefold(), item.version, item.key))

    def resolve(self, module_key: str) -> ModuleRecord:
        for record in self.list_modules():
            if record.key == module_key:
                return record
        raise FileNotFoundError(f"模组不存在: {module_key}")

    def install(self, package_path: Path) -> tuple[ModuleRecord, PackageInspection, bool]:
        with self._install_lock:
            return self._install_locked(package_path)

    def _install_locked(
        self,
        package_path: Path,
    ) -> tuple[ModuleRecord, PackageInspection, bool]:
        inspection = inspect_package(package_path)
        manifest = inspection.manifest
        target = self.user_root / manifest.id / manifest.version
        install_meta = target / "install.json"
        if target.exists():
            try:
                installed = json.loads(install_meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                installed = {}
            if installed.get("package_sha256") == inspection.package_sha256:
                record = self._installed_record(target)
                if record:
                    return record, inspection, True
            raise ModulePackageError(
                "version_conflict",
                f"{manifest.title} {manifest.version} 已安装；修改内容后请提升模组版本",
            )

        staging_root = self.user_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / uuid.uuid4().hex
        staging.mkdir()
        try:
            with zipfile.ZipFile(package_path) as archive:
                allowed = set(inspection.files)
                for info in archive.infolist():
                    name = _safe_archive_name(info.filename)
                    if not name or name not in allowed:
                        continue
                    destination = staging.joinpath(*PurePosixPath(name).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(info))

            compiled = compile_module(
                inspection.manifest,
                inspection.module,
                inspection.keeper_notes,
            )
            if not compiled.ok:
                details = [
                    f"{diagnostic.path}: {diagnostic.message}"
                    for diagnostic in compiled.diagnostics
                    if diagnostic.level == "error"
                ]
                raise ModulePackageError(
                    "compile_failed",
                    "模组编译失败",
                    details=details,
                )
            atomic_write_json(staging / "world_state_initial.json", compiled.world_state)
            (staging / "module.md").write_text(compiled.keeper_prompt, encoding="utf-8")
            if not (staging / "theme.json").exists():
                atomic_write_json(staging / "theme.json", {
                    "title": manifest.title,
                    "subtitle": manifest.system,
                    "description": manifest.description,
                })
            atomic_write_json(staging / "install.json", {
                "module_key": inspection.module_key,
                "package_sha256": inspection.package_sha256,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "format_version": manifest.format_version,
                "compiler_version": compiled.compiler_version,
            })
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        record = self._installed_record(target)
        if record is None:
            raise ModulePackageError("install_failed", "模组安装完成后无法注册")
        return record, inspection, False


def _load_json_entry(read: Callable[[str], bytes], name: str) -> dict:
    try:
        data = json.loads(read(name).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ModulePackageError("invalid_encoding", f"{name} 必须使用 UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ModulePackageError(
            "invalid_json",
            f"{name} 不是有效 JSON: 第 {exc.lineno} 行第 {exc.colno} 列",
        ) from exc
    if not isinstance(data, dict):
        raise ModulePackageError("invalid_json", f"{name} 根节点必须是 object")
    return data


def _validate_package_content(
    names: set[str],
    read: Callable[[str], bytes],
) -> tuple[
    ModuleManifest,
    ModuleDefinition,
    str,
    dict,
    LorebookEnvelope | None,
    list[str],
]:
    required = {"manifest.json", "module.json"}
    missing = sorted(required - names)
    if missing:
        raise ModulePackageError("missing_file", f"模组包缺少: {', '.join(missing)}")

    try:
        manifest = ModuleManifest.model_validate(_load_json_entry(read, "manifest.json"))
    except ValidationError as exc:
        raise ModulePackageError(
            "invalid_manifest", "manifest.json 校验失败", details=_validation_details(exc)
        ) from exc
    if not engine_supports(manifest.min_engine_version):
        raise ModulePackageError(
            "engine_too_old",
            f"模组需要 TRPG Master {manifest.min_engine_version} 或更高版本",
        )
    try:
        module = ModuleDefinition.model_validate(_load_json_entry(read, manifest.entry))
    except ValidationError as exc:
        raise ModulePackageError(
            "invalid_module", "module.json 校验失败", details=_validation_details(exc)
        ) from exc

    referenced_files = set()
    if manifest.keeper_document:
        referenced_files.add(manifest.keeper_document)
    if manifest.theme:
        referenced_files.add(manifest.theme)
    if manifest.lorebook:
        referenced_files.add(manifest.lorebook)
    for group in (module.assets.npcs, module.assets.scenes, module.assets.clues):
        referenced_files.update(asset.file for asset in group.values())
    referenced_files.update(
        scene.document for scene in module.scenes.values() if scene.document
    )
    missing_refs = sorted(referenced_files - names)
    if missing_refs:
        raise ModulePackageError(
            "missing_reference",
            "模组定义引用了包内不存在的文件",
            details=missing_refs,
        )
    if "lorebook.json" in names and not manifest.lorebook:
        raise ModulePackageError(
            "undeclared_lorebook",
            "模组包包含 lorebook.json，但 manifest.lorebook 未声明该文件",
        )

    skill_files = [name for name in names if name.startswith("skills/")]
    character_files = [name for name in names if name.startswith("characters/")]
    scene_files = [name for name in names if name.startswith("scenes/")]
    declared = set(manifest.capabilities)
    capability_files = {
        "custom_skills": skill_files,
        "bundled_characters": character_files,
        "scene_documents": scene_files,
    }
    undeclared = [capability for capability, files in capability_files.items() if files and capability not in declared]
    if undeclared:
        raise ModulePackageError(
            "undeclared_capability",
            f"manifest 未声明包内能力: {', '.join(undeclared)}",
        )

    for path, expected in manifest.checksums.items():
        if path not in names:
            raise ModulePackageError("missing_checksum_file", f"checksum 文件不存在: {path}")
        actual = hashlib.sha256(read(path)).hexdigest()
        if actual != expected:
            raise ModulePackageError("checksum_mismatch", f"文件校验失败: {path}")

    keeper_notes = ""
    if manifest.keeper_document:
        try:
            keeper_notes = read(manifest.keeper_document).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModulePackageError("invalid_encoding", "keeper.md 必须使用 UTF-8") from exc

    theme = {}
    if manifest.theme:
        theme = _load_json_entry(read, manifest.theme)
    lorebook = None
    if manifest.lorebook:
        try:
            lorebook = LorebookEnvelope.model_validate(
                _load_json_entry(read, manifest.lorebook)
            )
        except ValidationError as exc:
            raise ModulePackageError(
                "invalid_lorebook",
                "lorebook.json 校验失败",
                details=_validation_details(exc),
            ) from exc
        reference_errors = validate_lorebook_references(
            lorebook,
            scene_ids=set(module.scenes),
            npc_ids=set(module.npcs),
            clue_ids=set(module.clues),
            flag_ids=set(module.initial_state.flags),
        )
        if reference_errors:
            raise ModulePackageError(
                "invalid_lorebook_reference",
                "lorebook.json 引用了不存在的模组实体",
                details=reference_errors,
            )
    for path in character_files:
        _load_json_entry(read, path)
    for path in skill_files + scene_files:
        try:
            read(path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModulePackageError("invalid_encoding", f"{path} 必须使用 UTF-8") from exc

    warnings = []
    if "custom_skills" in declared:
        warnings.append("模组包含会进入守秘人上下文的自定义 Skill")
    if lorebook and lorebook.data.recursive_scanning:
        warnings.append("当前版本会保留但不会执行 Lorebook recursive_scanning")
    if lorebook and any(entry.use_regex for entry in lorebook.data.entries):
        warnings.append("当前版本会保留但不会执行 Lorebook 正则触发词")
    if not manifest.license:
        warnings.append("模组未声明许可证或授权信息")
    return manifest, module, keeper_notes, theme, lorebook, warnings


def inspect_package(package_path: Path) -> PackageInspection:
    package_path = Path(package_path)
    if not package_path.is_file():
        raise ModulePackageError("not_found", f"模组包不存在: {package_path}")
    if package_path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ModulePackageError("package_too_large", "模组包超过 64 MiB 上限")
    try:
        archive = zipfile.ZipFile(package_path)
    except zipfile.BadZipFile as exc:
        raise ModulePackageError("invalid_archive", "文件不是有效的 .trpgmod/ZIP 包") from exc

    with archive:
        names: dict[str, zipfile.ZipInfo] = {}
        casefolded: set[str] = set()
        total_size = 0
        file_count = 0
        for info in archive.infolist():
            name = _safe_archive_name(info.filename)
            if not name or info.is_dir():
                continue
            _check_zip_info(info)
            if not _is_allowed_package_file(name):
                raise ModulePackageError("forbidden_file", f"模组包包含不允许的文件: {name}")
            folded = name.casefold()
            if name in names or folded in casefolded:
                raise ModulePackageError("duplicate_path", f"模组包包含重复路径: {name}")
            names[name] = info
            casefolded.add(folded)
            total_size += info.file_size
            file_count += 1
            if file_count > MAX_PACKAGE_FILES:
                raise ModulePackageError("too_many_files", "模组包文件数量超过 1024")
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ModulePackageError("expanded_too_large", "模组包解压后超过 256 MiB")

        def read(name: str) -> bytes:
            try:
                return archive.read(names[name])
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise ModulePackageError("invalid_archive", f"ZIP 条目损坏: {name}") from exc

        (
            manifest,
            module,
            keeper_notes,
            theme,
            lorebook,
            warnings,
        ) = _validate_package_content(set(names), read)
    return PackageInspection(
        manifest=manifest,
        module=module,
        keeper_notes=keeper_notes,
        theme=theme,
        lorebook=lorebook,
        files=tuple(sorted(names)),
        package_sha256=_sha256_path(package_path),
        warnings=tuple(warnings),
    )


def build_package(source_dir: Path, output_path: Path) -> PackageInspection:
    """校验编辑工程并生成可导入的 .trpgmod。"""
    source_dir = Path(source_dir).resolve()
    output_path = Path(output_path).resolve()
    if not source_dir.is_dir():
        raise ModulePackageError("not_found", f"模组工程不存在: {source_dir}")

    source_files: dict[str, Path] = {}
    total_size = 0
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise ModulePackageError("symlink", f"模组工程不能包含符号链接: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir).as_posix()
        name = _safe_archive_name(relative)
        if not name:
            continue
        if not _is_allowed_package_file(name):
            raise ModulePackageError("forbidden_file", f"模组工程包含不允许的文件: {name}")
        if path.stat().st_size > MAX_SINGLE_FILE_BYTES:
            raise ModulePackageError("file_too_large", f"单个文件过大: {name}")
        source_files[name] = path
        total_size += path.stat().st_size
        if len(source_files) > MAX_PACKAGE_FILES:
            raise ModulePackageError("too_many_files", "模组工程文件数量超过 1024")
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ModulePackageError("expanded_too_large", "模组工程总大小超过 256 MiB")

    source_overrides: dict[str, bytes] = {}
    manifest_path = source_files.get("manifest.json")
    if manifest_path is not None:
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest_data, dict):
                # Checksums describe the built archive, not the mutable editor workspace.
                manifest_data["checksums"] = {}
                source_overrides["manifest.json"] = json.dumps(
                    manifest_data,
                    ensure_ascii=False,
                ).encode("utf-8")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # The regular package validator below reports the precise source error.
            pass

    def read_source(name: str) -> bytes:
        return source_overrides.get(name) or source_files[name].read_bytes()

    manifest, _module, _notes, _theme, _lorebook, _warnings = (
        _validate_package_content(set(source_files), read_source)
    )
    manifest_data = manifest.model_dump(by_alias=True, exclude_none=True)
    manifest_data["checksums"] = {
        name: hashlib.sha256(read_source(name)).hexdigest()
        for name in sorted(source_files)
        if name != "manifest.json"
    }
    manifest_bytes = (json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(source_files):
                payload = manifest_bytes if name == "manifest.json" else read_source(name)
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payload)
        if temp_path.stat().st_size > MAX_PACKAGE_BYTES:
            raise ModulePackageError("package_too_large", "生成的模组包超过 64 MiB 上限")
        inspection = inspect_package(temp_path)
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return inspection
