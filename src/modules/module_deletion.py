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


def account_module_usage(db_url: str, module_key: str) -> dict:
    with session_scope(db_url) as session:
        world_ids = list(session.scalars(select(World.id).where(World.module_name == module_key)))
        saves = session.query(SaveSlot).filter(SaveSlot.world_id.in_(world_ids)).count()
    return {"world_count": len(world_ids), "save_count": saves}


def _delete_world_records(session, world_ids: list[str]) -> int:
    save_count = session.query(SaveSlot).filter(SaveSlot.world_id.in_(world_ids)).count()
    session.execute(delete(SaveSlot).where(SaveSlot.world_id.in_(world_ids)))
    session.execute(
        update(ContextSession)
        .where(ContextSession.world_id.in_(world_ids))
        .values(parent_session_id=None)
    )
    session.execute(
        update(MemoryFact)
        .where(MemoryFact.world_id.in_(world_ids))
        .values(supersedes_id=None)
    )
    session.execute(delete(ModelServiceConfig).where(ModelServiceConfig.world_id.in_(world_ids)))
    session.execute(delete(World).where(World.id.in_(world_ids)))
    return save_count


def _delete_installed_record(
    registry: ModuleRegistry,
    record,
    package_root: Path,
    db_url: str,
) -> dict:
    package_dir = _contained_path(package_root, record.package_id)
    target = _contained_path(package_dir, record.version)
    if target.resolve() != record.path:
        raise ModulePackageError("unsafe_path", "模组路径与安装记录不一致")
    moved: list[tuple[Path, Path]] = []
    staging = registry.runtime_root / ".module-deletions" / uuid.uuid4().hex
    staging.mkdir(parents=True, mode=0o700)
    try:
        with session_scope(db_url) as session:
            worlds = list(session.scalars(
                select(World).where(World.module_name == record.key).with_for_update()
            ))
            if record.source == "account" and any(
                world.status in {"pending", "active"}
                and (world.metadata_json or {}).get("play_mode") == "multiplayer"
                for world in worlds
            ):
                raise ModulePackageError(
                    "multiplayer_world_in_use", "仍有多人房间使用此模组，请先由房主归档房间"
                )
            world_ids = [world.id for world in worlds]
            paths = [target]
            paths.extend(
                _contained_path(registry.runtime_root / "worlds", wid) for wid in world_ids
            )
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
                    if isinstance(info, dict) and info.get("module_name") == record.key:
                        paths.append(_contained_path(worlds_root, directory.name))
            for root in {registry.runtime_root, registry.project_root}:
                paths.append(_contained_path(root / "saves", record.key))
            for path in dict.fromkeys(paths):
                if path.exists():
                    destination = staging / str(len(moved))
                    path.rename(destination)
                    moved.append((path, destination))
            save_count = _delete_world_records(session, world_ids)
    except Exception:
        for original, destination in reversed(moved):
            destination.rename(original)
        shutil.rmtree(staging)
        raise
    cleanup_pending = False
    try:
        shutil.rmtree(staging)
    except OSError:
        cleanup_pending = True
    return {
        "world_count": len(world_ids),
        "save_count": save_count,
        "cleanup_pending": cleanup_pending,
    }


def delete_module(registry: ModuleRegistry, module_key: str) -> dict:
    with registry._install_lock:
        record = registry.resolve(module_key)
        if record.source != "user":
            raise ModulePackageError("builtin_module", "内置模组不能删除")
        return _delete_installed_record(
            registry, record, registry.user_root, database_url(registry.runtime_root)
        )


def delete_account_module(
    registry: ModuleRegistry,
    module_key: str,
    user_id: str,
    db_url: str,
) -> dict:
    with registry._install_lock:
        record = registry.resolve_for_account(module_key, user_id)
        if record.source != "account":
            raise FileNotFoundError(module_key)
        scope = registry._account_scope(user_id)
        package_root = registry._account_store(scope).user_root
        return _delete_installed_record(registry, record, package_root, db_url)
