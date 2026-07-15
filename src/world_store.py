"""带版本检查、房间锁和原子落盘的世界状态存储。"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .world_migrations import CURRENT_WORLD_SCHEMA_VERSION, migrate_world_state


class WorldStoreError(RuntimeError):
    pass


class WorldNotInitializedError(WorldStoreError):
    pass


class CorruptWorldError(WorldStoreError):
    pass


class StaleRevisionError(WorldStoreError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"世界状态 revision 已过期：期望 {expected}，当前 {actual}")


@dataclass(frozen=True)
class WorldSnapshot:
    state: dict
    revision: int


_ROOM_LOCKS: dict[str, threading.RLock] = {}
_ROOM_LOCKS_GUARD = threading.Lock()


def _room_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _ROOM_LOCKS_GUARD:
        return _ROOM_LOCKS.setdefault(key, threading.RLock())


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """同目录临时文件 + fsync + replace，失败时保留原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except (AttributeError, OSError):
            return
        try:
            try:
                os.fsync(dir_fd)
            except OSError:
                # 某些文件系统不支持目录 fsync；文件替换本身已经完成。
                pass
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


def atomic_write_json(path: Path, data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, payload)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """跨进程独占锁；Unix/Windows 均只依赖标准库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        acquired = True
        yield
    finally:
        try:
            if acquired and os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif acquired:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class WorldStore:
    """一个 world_id 对应一个实例；所有写入都在房间事务中完成。"""

    def __init__(self, world_dir: Path):
        self.world_dir = world_dir.resolve()
        self.state_path = self.world_dir / "world_state.json"
        self.backup_path = self.world_dir / "world_state.backup.json"
        self.lock_path = self.world_dir / ".world.lock"
        self.migration_report_path = self.world_dir / "migration-report.json"
        self._thread_lock = _room_lock(self.state_path)

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._thread_lock:
            with file_lock(self.lock_path):
                yield

    def _read_json(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorruptWorldError(f"无法读取世界状态 {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise CorruptWorldError(f"世界状态根节点不是 object: {path}")
        return data

    def _load_unlocked(self) -> dict:
        if not self.state_path.exists():
            raise WorldNotInitializedError(f"世界尚未初始化: {self.state_path}")
        try:
            state = self._read_json(self.state_path)
        except CorruptWorldError as primary_error:
            if not self.backup_path.exists():
                raise primary_error
            try:
                state = self._read_json(self.backup_path)
            except CorruptWorldError as backup_error:
                raise CorruptWorldError(
                    f"主状态和备份均损坏: {primary_error}; {backup_error}"
                ) from backup_error
            # 备份通常比损坏的主文件落后一版；跨过两个 revision，避免恢复后
            # 与崩溃前客户端持有的版本号发生 ABA 冲突。
            state["revision"] = int(state.get("revision", 0)) + 2
            atomic_write_json(self.state_path, state)

        source_version = int(state.get("schema_version", 0) or 0)
        migrated, changed = migrate_world_state(state)
        if changed:
            backup_path = self.world_dir / (
                f"world_state.v{source_version}.migration-backup.json"
            )
            if not backup_path.exists():
                atomic_write_json(backup_path, state)
            atomic_write_json(self.migration_report_path, {
                "from_version": source_version,
                "to_version": CURRENT_WORLD_SCHEMA_VERSION,
                "source_revision": int(state.get("revision", 0) or 0),
                "backup_file": backup_path.name,
                "migrations": migrated.get("migration_history", []),
            })
            self._commit_unlocked(migrated, preserve_backup=True)
        return migrated

    def _commit_unlocked(self, state: dict, *, preserve_backup: bool = False) -> None:
        if self.state_path.exists() and not preserve_backup:
            try:
                current = self._read_json(self.state_path)
            except CorruptWorldError:
                current = None
            if current is not None:
                atomic_write_json(self.backup_path, current)
        atomic_write_json(self.state_path, state)

    def initialize(self, template: dict, *, overwrite: bool = False) -> WorldSnapshot:
        with self.locked():
            if self.state_path.exists() and not overwrite:
                state = self._load_unlocked()
                return WorldSnapshot(copy.deepcopy(state), state["revision"])

            state, _ = migrate_world_state(template)
            if self.state_path.exists():
                try:
                    current = self._load_unlocked()
                    state["revision"] = current["revision"] + 1
                except WorldStoreError:
                    state["revision"] = 0
            else:
                state["revision"] = 0
            state["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            self._commit_unlocked(state)
            return WorldSnapshot(copy.deepcopy(state), state["revision"])

    def load(self) -> dict:
        return self.snapshot().state

    @property
    def revision(self) -> int:
        return self.snapshot().revision

    def snapshot(self) -> WorldSnapshot:
        with self.locked():
            state = self._load_unlocked()
            return WorldSnapshot(copy.deepcopy(state), state["revision"])

    def update(
        self,
        mutator: Callable[[dict], dict | None],
        *,
        expected_revision: int | None = None,
    ) -> WorldSnapshot:
        with self.locked():
            current = self._load_unlocked()
            actual = current["revision"]
            if expected_revision is not None and expected_revision != actual:
                raise StaleRevisionError(expected_revision, actual)

            working = copy.deepcopy(current)
            replacement = mutator(working)
            if replacement is not None:
                if not isinstance(replacement, dict):
                    raise TypeError("WorldStore mutator 只能返回 dict 或 None")
                working = replacement
            working, _ = migrate_world_state(working)
            working["revision"] = actual + 1
            working["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            self._commit_unlocked(working)
            return WorldSnapshot(copy.deepcopy(working), working["revision"])

    @contextmanager
    def transaction(self, *, expected_revision: int | None = None) -> Iterator[dict]:
        """持锁的可变事务；仅当内容变化时提交并递增 revision。"""
        with self.locked():
            current = self._load_unlocked()
            actual = current["revision"]
            if expected_revision is not None and expected_revision != actual:
                raise StaleRevisionError(expected_revision, actual)
            working = copy.deepcopy(current)
            yield working
            working, _ = migrate_world_state(working)
            if working != current:
                working["revision"] = actual + 1
                working["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
                self._commit_unlocked(working)

    def restore(
        self,
        snapshot: WorldSnapshot | dict,
        *,
        expected_revision: int | None = None,
    ) -> WorldSnapshot:
        source = snapshot.state if isinstance(snapshot, WorldSnapshot) else snapshot
        if not isinstance(source, dict):
            raise TypeError("snapshot 必须是 WorldSnapshot 或 dict")

        with self.locked():
            current = self._load_unlocked()
            actual = current["revision"]
            if expected_revision is not None and expected_revision != actual:
                raise StaleRevisionError(expected_revision, actual)
            restored, _ = migrate_world_state(source)
            restored["revision"] = actual + 1
            restored["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            self._commit_unlocked(restored)
            return WorldSnapshot(copy.deepcopy(restored), restored["revision"])

    def seed_from_snapshot(
        self,
        snapshot: WorldSnapshot | dict,
        *,
        expected_revision: int = 0,
    ) -> WorldSnapshot:
        """Initialize a freshly-created branch while preserving its fork revision."""
        source = snapshot.state if isinstance(snapshot, WorldSnapshot) else snapshot
        if not isinstance(source, dict):
            raise TypeError("snapshot 必须是 WorldSnapshot 或 dict")

        with self.locked():
            current = self._load_unlocked()
            actual = int(current.get("revision", 0))
            if actual != expected_revision:
                raise StaleRevisionError(expected_revision, actual)
            seeded, _ = migrate_world_state(copy.deepcopy(source))
            seeded["revision"] = max(0, int(source.get("revision", 0)))
            seeded["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            self._commit_unlocked(seeded)
            return WorldSnapshot(copy.deepcopy(seeded), seeded["revision"])
