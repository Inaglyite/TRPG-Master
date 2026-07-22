"""PostgreSQL/JSONB implementation of the world-state store contract."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .database import World, WorldState, session_scope
from .world_migrations import CURRENT_WORLD_SCHEMA_VERSION, migrate_world_state
from .world_store import (
    StaleRevisionError,
    WorldNotInitializedError,
    WorldSnapshot,
    WorldStoreError,
)

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(key: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class DatabaseWorldStore:
    """WorldStore-compatible state authority backed by one transactional row."""

    def __init__(self, database_url: str, world_id: str, world_dir: Path):
        self.database_url = database_url
        self.world_id = world_id
        # Compatibility paths are intentionally not read or written by this store.
        self.world_dir = Path(world_dir).resolve()
        self.state_path = self.world_dir / "world_state.json"
        self._thread_lock = _lock(f"{database_url}:{world_id}")
        self._cache_depth = 0
        self._cached_snapshot: WorldSnapshot | None = None
        self._turn_state: dict | None = None
        self._turn_base_revision: int | None = None
        self._turn_dirty = False
        self._turn_flushed = False

    @property
    def exists(self) -> bool:
        with session_scope(self.database_url) as session:
            return session.get(WorldState, self.world_id) is not None

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._thread_lock:
            yield

    @contextmanager
    def turn_cache(self) -> Iterator[None]:
        """Buffer one serialized engine turn and commit its state at most once."""
        with self._thread_lock:
            outermost = self._cache_depth == 0
            if outermost:
                snapshot = self.snapshot()
                self._turn_state = copy.deepcopy(snapshot.state)
                self._turn_base_revision = snapshot.revision
                self._turn_dirty = False
                self._turn_flushed = False
            self._cache_depth += 1
        try:
            yield
        except BaseException:
            if outermost:
                self._discard_turn_state()
            raise
        finally:
            with self._thread_lock:
                self._cache_depth = max(0, self._cache_depth - 1)
                if self._cache_depth == 0:
                    if self._turn_dirty and not self._turn_flushed:
                        self.flush_turn()
                    self._cached_snapshot = None
                    self._discard_turn_state()

    def _discard_turn_state(self) -> None:
        self._turn_state = None
        self._turn_base_revision = None
        self._turn_dirty = False
        self._turn_flushed = False

    def flush_turn(self) -> WorldSnapshot:
        """Commit the buffered state once using the revision read at turn start."""
        with self._thread_lock:
            if self._turn_state is None or self._turn_base_revision is None:
                return self.snapshot()
            if not self._turn_dirty:
                return WorldSnapshot(
                    copy.deepcopy(self._turn_state), self._turn_base_revision
                )
            with session_scope(self.database_url) as session:
                row = self._row(session, for_update=True)
                if row.revision != self._turn_base_revision:
                    raise StaleRevisionError(self._turn_base_revision, row.revision)
                working, _ = migrate_world_state(copy.deepcopy(self._turn_state))
                working["revision"] = row.revision + 1
                working["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
                row.state = working
                row.revision += 1
                row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
                row.updated_at = datetime.now(UTC)
                session.flush()
                snapshot = WorldSnapshot(copy.deepcopy(working), row.revision)
            self._turn_state = copy.deepcopy(snapshot.state)
            self._turn_base_revision = snapshot.revision
            self._turn_dirty = False
            self._turn_flushed = True
            self._cache(snapshot)
            return snapshot

    def prepare_turn_commit(self) -> tuple[dict, int | None]:
        """Return a final state plus the DB revision an atomic journal must verify."""
        with self._thread_lock:
            if self._turn_state is None or self._turn_base_revision is None:
                return self.load(), None
            state, _ = migrate_world_state(copy.deepcopy(self._turn_state))
            expected = self._turn_base_revision if self._turn_dirty else None
            state["revision"] = self._turn_base_revision + (1 if self._turn_dirty else 0)
            state["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            return state, expected

    def accept_turn_commit(self, state: dict) -> None:
        """Synchronize the work unit after the journal transaction committed it."""
        with self._thread_lock:
            snapshot = WorldSnapshot(
                copy.deepcopy(state), int(state.get("revision", 0))
            )
            self._turn_state = copy.deepcopy(snapshot.state)
            self._turn_base_revision = snapshot.revision
            self._turn_dirty = False
            self._turn_flushed = True
            self._cache(snapshot)

    def invalidate_cache(self) -> None:
        with self._thread_lock:
            if self._turn_state is not None:
                raise WorldStoreError("回合工作单元期间不能从外部失效状态")
            self._cached_snapshot = None

    def _cache(self, snapshot: WorldSnapshot) -> None:
        if self._cache_depth:
            self._cached_snapshot = WorldSnapshot(
                copy.deepcopy(snapshot.state), snapshot.revision
            )

    def _row(self, session, *, for_update: bool = False) -> WorldState:
        statement = select(WorldState).where(WorldState.world_id == self.world_id)
        if for_update:
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is None:
            raise WorldNotInitializedError(f"世界尚未初始化: {self.world_id}")
        return row

    def initialize(self, template: dict, *, overwrite: bool = False) -> WorldSnapshot:
        with self._thread_lock, session_scope(self.database_url) as session:
            row = session.get(WorldState, self.world_id)
            state, _ = migrate_world_state(copy.deepcopy(template))
            if row is not None and not overwrite:
                return WorldSnapshot(copy.deepcopy(row.state), row.revision)
            revision = row.revision + 1 if row is not None else 0
            state["revision"] = revision
            state["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            if row is None:
                if session.get(World, self.world_id) is None:
                    raise WorldStoreError(f"世界元数据不存在: {self.world_id}")
                row = WorldState(
                    world_id=self.world_id,
                    schema_version=CURRENT_WORLD_SCHEMA_VERSION,
                    revision=revision,
                    state=state,
                )
                session.add(row)
            else:
                row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
                row.revision = revision
                row.state = state
                row.updated_at = datetime.now(UTC)
            session.flush()
            snapshot = WorldSnapshot(copy.deepcopy(state), revision)
            self._cache(snapshot)
            return snapshot

    def load(self) -> dict:
        return self.snapshot().state

    @property
    def revision(self) -> int:
        return self.snapshot().revision

    def snapshot(self) -> WorldSnapshot:
        with self._thread_lock, session_scope(self.database_url) as session:
            if self._turn_state is not None and self._turn_base_revision is not None:
                return WorldSnapshot(
                    copy.deepcopy(self._turn_state),
                    int(self._turn_state.get("revision", self._turn_base_revision)),
                )
            if self._cache_depth and self._cached_snapshot is not None:
                return WorldSnapshot(
                    copy.deepcopy(self._cached_snapshot.state),
                    self._cached_snapshot.revision,
                )
            row = self._row(session)
            state, changed = migrate_world_state(copy.deepcopy(row.state))
            if changed:
                state["revision"] = row.revision + 1
                state["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
                row.revision += 1
                row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
                row.state = state
                row.updated_at = datetime.now(UTC)
            snapshot = WorldSnapshot(copy.deepcopy(state), int(state["revision"]))
            self._cache(snapshot)
            return snapshot

    def update(
        self,
        mutator: Callable[[dict], dict | None],
        *,
        expected_revision: int | None = None,
    ) -> WorldSnapshot:
        if self._turn_state is not None and self._turn_base_revision is not None:
            with self._thread_lock:
                actual = int(self._turn_state.get("revision", self._turn_base_revision))
                if expected_revision is not None and expected_revision != actual:
                    raise StaleRevisionError(expected_revision, actual)
                working = copy.deepcopy(self._turn_state)
                replacement = mutator(working)
                if replacement is not None:
                    if not isinstance(replacement, dict):
                        raise TypeError("WorldStore mutator 只能返回 dict 或 None")
                    working = replacement
                working, _ = migrate_world_state(working)
                working["revision"] = actual + 1
                working["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
                self._turn_state = working
                self._turn_dirty = True
                self._turn_flushed = False
                snapshot = WorldSnapshot(copy.deepcopy(working), actual + 1)
                self._cache(snapshot)
                return snapshot
        with self._thread_lock, session_scope(self.database_url) as session:
            row = self._row(session, for_update=True)
            actual = row.revision
            if expected_revision is not None and expected_revision != actual:
                raise StaleRevisionError(expected_revision, actual)
            working = copy.deepcopy(row.state)
            replacement = mutator(working)
            if replacement is not None:
                if not isinstance(replacement, dict):
                    raise TypeError("WorldStore mutator 只能返回 dict 或 None")
                working = replacement
            working, _ = migrate_world_state(working)
            working["revision"] = actual + 1
            working["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            row.state = working
            row.revision = actual + 1
            row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
            row.updated_at = datetime.now(UTC)
            session.flush()
            snapshot = WorldSnapshot(copy.deepcopy(working), row.revision)
            self._cache(snapshot)
            return snapshot

    @contextmanager
    def transaction(self, *, expected_revision: int | None = None) -> Iterator[dict]:
        if self._turn_state is not None:
            before = copy.deepcopy(self._turn_state)
            yield self._turn_state
            if self._turn_state != before:
                actual = int(before.get("revision", self._turn_base_revision or 0))
                if expected_revision is not None and expected_revision != actual:
                    self._turn_state = before
                    raise StaleRevisionError(expected_revision, actual)
                self._turn_state["revision"] = actual + 1
                self._turn_state["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
                self._turn_dirty = True
                self._turn_flushed = False
            return
        with self._thread_lock, session_scope(self.database_url) as session:
            row = self._row(session, for_update=True)
            actual = row.revision
            if expected_revision is not None and expected_revision != actual:
                raise StaleRevisionError(expected_revision, actual)
            current = copy.deepcopy(row.state)
            working = copy.deepcopy(current)
            yield working
            working, _ = migrate_world_state(working)
            if working != current:
                working["revision"] = actual + 1
                working["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
                row.state = working
                row.revision = actual + 1
                row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
                row.updated_at = datetime.now(UTC)
                self._cache(WorldSnapshot(copy.deepcopy(working), row.revision))

    def restore(
        self,
        snapshot: WorldSnapshot | dict,
        *,
        expected_revision: int | None = None,
    ) -> WorldSnapshot:
        source = snapshot.state if isinstance(snapshot, WorldSnapshot) else snapshot
        if not isinstance(source, dict):
            raise TypeError("snapshot 必须是 WorldSnapshot 或 dict")
        return self.update(
            lambda _current: copy.deepcopy(source), expected_revision=expected_revision
        )

    def seed_from_snapshot(
        self,
        snapshot: WorldSnapshot | dict,
        *,
        expected_revision: int = 0,
    ) -> WorldSnapshot:
        source = snapshot.state if isinstance(snapshot, WorldSnapshot) else snapshot
        if not isinstance(source, dict):
            raise TypeError("snapshot 必须是 WorldSnapshot 或 dict")
        with self._thread_lock, session_scope(self.database_url) as session:
            row = self._row(session, for_update=True)
            if row.revision != expected_revision:
                raise StaleRevisionError(expected_revision, row.revision)
            seeded, _ = migrate_world_state(copy.deepcopy(source))
            seeded["revision"] = max(0, int(source.get("revision", 0)))
            seeded["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
            row.state = seeded
            row.revision = seeded["revision"]
            row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
            row.updated_at = datetime.now(UTC)
            snapshot = WorldSnapshot(copy.deepcopy(seeded), row.revision)
            self._cache(snapshot)
            return snapshot
