"""每个运行世界的显式路径与存储上下文。"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_MODULE_NAME, PROJECT_ROOT, RUNTIME_ROOT
from .handouts import refresh_static_handout_config
from .module_registry import ModuleRecord, ModuleRegistry
from .world_store import WorldStore, atomic_write_json


def default_world_id(module_name: str) -> str:
    slug = re.sub(r"\s+", "_", module_name.strip())
    slug = re.sub(r"[^\w-]+", "-", slug, flags=re.UNICODE).strip("-_")
    return f"local-{slug or 'world'}"


def _validate_component(value: str, label: str) -> str:
    value = str(value).strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"非法的 {label}: {value!r}")
    return value


def _file_revision(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


@dataclass(frozen=True)
class RuntimeContext:
    project_root: Path
    runtime_root: Path
    world_id: str
    module_name: str
    world_store: WorldStore = field(init=False, repr=False, compare=False)
    module_record: ModuleRecord = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())
        object.__setattr__(self, "runtime_root", Path(self.runtime_root).resolve())
        object.__setattr__(self, "world_id", _validate_component(self.world_id, "world_id"))
        object.__setattr__(self, "module_name", _validate_component(self.module_name, "module_name"))
        registry = ModuleRegistry(self.project_root, self.runtime_root)
        object.__setattr__(self, "module_record", registry.resolve(self.module_name))
        object.__setattr__(self, "world_store", WorldStore(self.world_dir))

    @property
    def module_dir(self) -> Path:
        return self.module_record.path

    @property
    def initial_state_file(self) -> Path:
        return self.module_dir / "world_state_initial.json"

    @property
    def legacy_state_file(self) -> Path:
        return self.module_dir / "world_state.json"

    @property
    def world_dir(self) -> Path:
        return self.runtime_root / "worlds" / self.world_id

    @property
    def state_file(self) -> Path:
        return self.world_store.state_path

    @property
    def saves_dir(self) -> Path:
        return self.world_dir / "saves"

    @property
    def theme_file(self) -> Path:
        return self.module_dir / "theme.json"

    @property
    def lorebook_file(self) -> Path:
        return self.module_dir / "lorebook.json"

    @property
    def assets_dir(self) -> Path:
        return self.module_dir / "assets"

    @property
    def metadata_file(self) -> Path:
        return self.world_dir / "world.json"

    @property
    def default_characters_dir(self) -> Path:
        return self.project_root / "characters" / "default"

    @property
    def custom_characters_dir(self) -> Path:
        return self.runtime_root / "characters" / "custom"

    @property
    def profiles_dir(self) -> Path:
        return self.runtime_root / "profiles"

    @property
    def player_profile_file(self) -> Path:
        return self.profiles_dir / "player_profile.json"

    def ensure_initialized(self, *, migrate_legacy: bool = False) -> "RuntimeContext":
        if not self.module_dir.is_dir():
            raise FileNotFoundError(f"模组不存在: {self.module_dir}")
        self.world_dir.mkdir(parents=True, exist_ok=True)
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.custom_characters_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

        if self.metadata_file.exists():
            metadata = json.loads(self.metadata_file.read_text(encoding="utf-8"))
            if metadata.get("module_name") != self.module_name:
                raise ValueError(
                    f"world_id={self.world_id} 已绑定模组 {metadata.get('module_name')}"
                )
            expected_metadata = {
                "module_id": self.module_record.package_id,
                "module_version": self.module_record.version,
            }
            if any(key not in metadata for key in expected_metadata):
                for key, value in expected_metadata.items():
                    metadata.setdefault(key, value)
                atomic_write_json(self.metadata_file, metadata)
        else:
            metadata = {
                "world_id": self.world_id,
                "module_name": self.module_name,
                "module_id": self.module_record.package_id,
                "module_version": self.module_record.version,
                "created_at": datetime.now().isoformat(),
                "layout_version": 1,
            }
            atomic_write_json(self.metadata_file, metadata)

        if not self.state_file.exists():
            source = None
            migrated_from = None
            if migrate_legacy and self.legacy_state_file.exists():
                source = self.legacy_state_file
                migrated_from = str(source)
            elif self.initial_state_file.exists():
                source = self.initial_state_file
            elif self.legacy_state_file.exists():
                source = self.legacy_state_file
            if source is None:
                raise FileNotFoundError(f"模组缺少初始世界模板: {self.module_dir}")
            template = json.loads(source.read_text(encoding="utf-8"))
            self.world_store.initialize(template)
            if migrated_from:
                metadata["migrated_from"] = migrated_from
                metadata["migrated_at"] = datetime.now().isoformat()
                atomic_write_json(self.metadata_file, metadata)

        initial_state_revision = (
            _file_revision(self.initial_state_file)
            if self.initial_state_file.exists()
            else ""
        )
        if initial_state_revision and (
            metadata.get("initial_state_revision") != initial_state_revision
        ):
            template = json.loads(self.initial_state_file.read_text(encoding="utf-8"))
            with self.world_store.transaction() as state:
                refresh_static_handout_config(state, template)
            metadata["initial_state_revision"] = initial_state_revision
            atomic_write_json(self.metadata_file, metadata)

        if migrate_legacy and not metadata.get("legacy_saves_migrated"):
            legacy_save_dirs = [
                self.runtime_root / "saves" / self.module_name,
                self.project_root / "saves" / self.module_name,
            ]
            if not any(self.saves_dir.iterdir()):
                copied: set[Path] = set()
                for legacy_saves in legacy_save_dirs:
                    resolved = legacy_saves.resolve()
                    if (
                        resolved not in copied
                        and legacy_saves.is_dir()
                        and resolved != self.saves_dir.resolve()
                    ):
                        shutil.copytree(legacy_saves, self.saves_dir, dirs_exist_ok=True)
                        metadata["legacy_saves_from"] = str(resolved)
                        copied.add(resolved)
                        break
            metadata["legacy_saves_migrated"] = True
            metadata["legacy_saves_migrated_at"] = datetime.now().isoformat()
            atomic_write_json(self.metadata_file, metadata)
        self.world_store.load()
        return self

    def reset_world(self) -> None:
        source = self.initial_state_file if self.initial_state_file.exists() else self.legacy_state_file
        template = json.loads(source.read_text(encoding="utf-8"))
        self.world_store.initialize(template, overwrite=True)

    def child_process_env(self) -> dict[str, str]:
        return {
            "TRPG_PROJECT_ROOT": str(self.project_root),
            "TRPG_RUNTIME_ROOT": str(self.runtime_root),
            "TRPG_WORLD_ID": self.world_id,
            "TRPG_MODULE": self.module_name,
        }

    @classmethod
    def create(
        cls,
        world_id: str,
        module_name: str,
        *,
        project_root: Path = PROJECT_ROOT,
        runtime_root: Path = RUNTIME_ROOT,
        migrate_legacy: bool = False,
    ) -> "RuntimeContext":
        context = cls(project_root, runtime_root, world_id, module_name)
        return context.ensure_initialized(migrate_legacy=migrate_legacy)

    @classmethod
    def local(
        cls,
        module_name: str = DEFAULT_MODULE_NAME,
        *,
        project_root: Path = PROJECT_ROOT,
        runtime_root: Path = RUNTIME_ROOT,
    ) -> "RuntimeContext":
        return cls.create(
            default_world_id(module_name),
            module_name,
            project_root=project_root,
            runtime_root=runtime_root,
            migrate_legacy=True,
        )

    @classmethod
    def from_env(cls) -> "RuntimeContext":
        module_name = os.environ.get("TRPG_MODULE", DEFAULT_MODULE_NAME)
        world_id = os.environ.get("TRPG_WORLD_ID")
        project_root = Path(os.environ.get("TRPG_PROJECT_ROOT", PROJECT_ROOT))
        runtime_root = Path(os.environ.get("TRPG_RUNTIME_ROOT", RUNTIME_ROOT))
        if world_id:
            return cls.create(
                world_id,
                module_name,
                project_root=project_root,
                runtime_root=runtime_root,
            )
        return cls.local(
            module_name,
            project_root=project_root,
            runtime_root=runtime_root,
        )
