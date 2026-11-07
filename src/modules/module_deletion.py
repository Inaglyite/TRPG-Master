"""Local package removal, including its exact-version worlds and save history."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy import delete, select, update

from src.modules.module_registry import ModulePackageError, ModuleRegistry
from src.storage.database import (
    ContextSession,
    MemoryFact,
    ModelServiceConfig,
    SaveSlot,
    World,
    database_url,
    session_scope,
)


def module_usage(registry: ModuleRegistry, module_key: str) -> dict:
    with session_scope(database_url(registry.runtime_root)) as session:
        world_ids = list(session.scalars(select(World.id).where(World.module_name == module_key)))
        saves = session.query(SaveSlot).filter(SaveSlot.world_id.in_(world_ids)).count()
    return {"world_count": len(world_ids), "save_count": saves}


def _contained_path(root: Path, name: str) -> Path:
    path = root / name
    if root.is_symlink() or not name or Path(name).name != name or name in {".", ".."} or path.is_symlink():
        raise ModulePackageError("unsafe_path", "模组或存档路径异常，已停止删除")
    if path.resolve().parent != root.resolve():
        raise ModulePackageError("unsafe_path", "模组或存档路径超出数据目录，已停止删除")
    return path


def delete_module(registry: ModuleRegistry, module_key: str) -> dict:
    # Share the installation lock: reinstall cannot race a removal of the same version.
    with registry._install_lock:
        record = registry.resolve(module_key)
        if record.source != "user":
            raise ModulePackageError("builtin_module", "内置模组不能删除")
        package_dir = _contained_path(registry.user_root, record.package_id)
        target = _contained_path(package_dir, record.version)
        if target.resolve() != record.path:
            raise ModulePackageError("unsafe_path", "模组路径与安装记录不一致")
        moved: list[tuple[Path, Path]] = []
        staging = registry.runtime_root / ".module-deletions" / uuid.uuid4().hex
        staging.mkdir(parents=True, mode=0o700)
        try:
            with session_scope(database_url(registry.runtime_root)) as session:
                world_ids = list(session.scalars(
                    select(World.id).where(World.module_name == module_key).with_for_update()
                ))
                save_count = session.query(SaveSlot).filter(SaveSlot.world_id.in_(world_ids)).count()
                paths = [target]
                paths.extend(_contained_path(registry.runtime_root / "worlds", wid) for wid in world_ids)
                # Remove file-only legacy worlds too, so migration cannot resurrect deleted saves.
                worlds_root = registry.runtime_root / "worlds"
                if worlds_root.is_dir():
                    for directory in worlds_root.iterdir():
                        if directory.is_symlink() or not directory.is_dir():
                            continue
                        metadata = directory / "world.json"
                        if not metadata.is_file():
                            continue
                        try:
                            info = json.loads(metadata.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            continue
                        if isinstance(info, dict) and info.get("module_name") == module_key:
                            paths.append(_contained_path(worlds_root, directory.name))
                for root in {registry.runtime_root, registry.project_root}:
                    paths.append(_contained_path(root / "saves", module_key))
                for path in dict.fromkeys(paths):
                    if path.exists():
                        destination = staging / str(len(moved))
                        path.rename(destination)
                        moved.append((path, destination))
                # RESTRICT references must be detached within the deleted set first.
                session.execute(delete(SaveSlot).where(SaveSlot.world_id.in_(world_ids)))
                session.execute(update(ContextSession).where(ContextSession.world_id.in_(world_ids))
                                .values(parent_session_id=None))
                session.execute(update(MemoryFact).where(MemoryFact.world_id.in_(world_ids))
                                .values(supersedes_id=None))
                session.execute(delete(ModelServiceConfig).where(ModelServiceConfig.world_id.in_(world_ids)))
                session.execute(delete(World).where(World.id.in_(world_ids)))
                # All world-owned data cascade in the same database transaction.
        except Exception:
            for original, destination in reversed(moved):
                destination.rename(original)
            shutil.rmtree(staging)
            raise
        # A cleanup failure must not turn a committed deletion into an apparent failure.
        cleanup_pending = False
        try:
            shutil.rmtree(staging)
        except OSError:
            cleanup_pending = True
        return {"world_count": len(world_ids), "save_count": save_count,
                "cleanup_pending": cleanup_pending}
