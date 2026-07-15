"""Player-owned notes kept outside authoritative gameplay state and prompts."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from .world_store import atomic_write_json, file_lock

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
    def __init__(self, world_dir: Path) -> None:
        self.world_dir = Path(world_dir).resolve()
        self.path = self.world_dir / "player_notes.json"
        self.lock_path = self.world_dir / ".player_notes.lock"
        self._thread_lock = _notes_lock(self.path)

    @staticmethod
    def _empty() -> dict:
        return {
            "schema_version": PLAYER_NOTES_SCHEMA_VERSION,
            "revision": 0,
            "text": "",
            "updated_at": None,
        }

    def _read_unlocked(self) -> dict:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取玩家笔记: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("玩家笔记根节点必须是 object")
        result = self._empty()
        result.update(data)
        result["revision"] = max(0, int(result.get("revision", 0)))
        result["text"] = str(result.get("text") or "")
        return result

    def load(self) -> dict:
        with self._thread_lock, file_lock(self.lock_path):
            return self._read_unlocked()

    def save(self, text: object, *, expected_revision: int | None = None) -> dict:
        if not isinstance(text, str):
            raise TypeError("玩家笔记必须是字符串")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(normalized) > MAX_PLAYER_NOTES_CHARS:
            raise ValueError(f"玩家笔记不能超过 {MAX_PLAYER_NOTES_CHARS} 个字符")
        with self._thread_lock, file_lock(self.lock_path):
            current = self._read_unlocked()
            actual = int(current["revision"])
            if expected_revision is not None and expected_revision != actual:
                raise PlayerNotesConflict(expected_revision, actual)
            payload = {
                "schema_version": PLAYER_NOTES_SCHEMA_VERSION,
                "revision": actual + 1,
                "text": normalized,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            atomic_write_json(self.path, payload)
            return payload
