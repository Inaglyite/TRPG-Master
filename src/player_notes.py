"""Player-owned notes kept outside authoritative gameplay state and prompts."""

from __future__ import annotations

import threading
from pathlib import Path

from .database import (
    PlayerNote,
    World,
    database_url,
    initialize_database,
    new_id,
    session_scope,
    utcnow,
)

PLAYER_NOTES_SCHEMA_VERSION = 1
MAX_PLAYER_NOTES_CHARS = 20_000


class PlayerNotesConflict(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"笔记版本已变化：期望 {expected}，当前 {actual}")


_NOTES_LOCKS: dict[str, threading.RLock] = {}
_NOTES_LOCKS_GUARD = threading.Lock()


def _notes_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _NOTES_LOCKS_GUARD:
        return _NOTES_LOCKS.setdefault(key, threading.RLock())


class PlayerNotesStore:
    def __init__(self, world_dir: Path, *, user_id: str | None = None) -> None:
        self.world_dir = Path(world_dir).resolve()
        self.path = self.world_dir / "player_notes.json"
        self.world_id = self.world_dir.name
        self.user_id = user_id
        self.owner_key = user_id or "__local__"
        runtime_root = (
            self.world_dir.parent.parent
            if self.world_dir.parent.name == "worlds"
            else self.world_dir.parent
        )
        self.database_url = database_url(runtime_root)
        if self.database_url.startswith("sqlite:"):
            initialize_database(self.database_url)
        self._thread_lock = _notes_lock(self.path)
        with session_scope(self.database_url) as session:
            if session.get(World, self.world_id) is None:
                session.add(World(id=self.world_id, module_name="unknown"))

    @staticmethod
    def _empty() -> dict:
        return {
            "schema_version": PLAYER_NOTES_SCHEMA_VERSION,
            "revision": 0,
            "text": "",
            "updated_at": None,
        }

    def load(self) -> dict:
        with self._thread_lock, session_scope(self.database_url) as session:
            row = (
                session.query(PlayerNote)
                .filter_by(world_id=self.world_id, owner_key=self.owner_key)
                .one_or_none()
            )
            if row is None:
                return self._empty()
            return {
                "schema_version": PLAYER_NOTES_SCHEMA_VERSION,
                "revision": row.revision,
                "text": row.text,
                "updated_at": row.updated_at.isoformat(),
            }

    def save(self, text: object, *, expected_revision: int | None = None) -> dict:
        if not isinstance(text, str):
            raise TypeError("玩家笔记必须是字符串")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(normalized) > MAX_PLAYER_NOTES_CHARS:
            raise ValueError(f"玩家笔记不能超过 {MAX_PLAYER_NOTES_CHARS} 个字符")
        with self._thread_lock, session_scope(self.database_url) as session:
            row = (
                session.query(PlayerNote)
                .filter_by(world_id=self.world_id, owner_key=self.owner_key)
                .one_or_none()
            )
            actual = int(row.revision if row else 0)
            if expected_revision is not None and expected_revision != actual:
                raise PlayerNotesConflict(expected_revision, actual)
            if row is None:
                row = PlayerNote(
                    id=new_id("note"),
                    world_id=self.world_id,
                    user_id=self.user_id,
                    owner_key=self.owner_key,
                )
                session.add(row)
            row.revision = actual + 1
            row.text = normalized
            row.updated_at = utcnow()
            payload = {
                "schema_version": PLAYER_NOTES_SCHEMA_VERSION,
                "revision": row.revision,
                "text": row.text,
                "updated_at": row.updated_at.isoformat(),
            }
            return payload
