"""Durable, per-world records for completed GM turns.

The journal is deliberately separate from ``WorldStore``.  World state remains
the gameplay authority; a turn record binds one visible narration to the world
snapshot and message history that were committed with it.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .world_store import atomic_write_json, file_lock


TURN_RECORD_SCHEMA_VERSION = 1
PROCESS_INSTANCE_ID = secrets.token_hex(12)
_TURN_ID = re.compile(r"^turn_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{8}$")
_REPLAY_EVENT_TYPES = {
    "narrative_chunk",
    "tension",
    "dice_result",
    "glm_summary",
    "handout",
    "error",
    "choices",
}


class TurnJournalError(RuntimeError):
    pass


class ActiveTurnError(TurnJournalError):
    pass


class TurnNotFoundError(TurnJournalError):
    pass


_JOURNAL_LOCKS: dict[str, threading.RLock] = {}
_JOURNAL_LOCKS_GUARD = threading.Lock()


def _journal_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _JOURNAL_LOCKS_GUARD:
        return _JOURNAL_LOCKS.setdefault(key, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_turn_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"turn_{stamp}_{secrets.token_hex(4)}"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def serialize_messages(messages: list[dict]) -> list[dict]:
    """Keep only fields accepted by the OpenAI-compatible message history."""
    serializable: list[dict] = []
    for message in messages:
        entry = {
            "role": message.get("role", ""),
            "content": message.get("content", ""),
        }
        if "tool_calls" in message:
            entry["tool_calls"] = _json_safe(message["tool_calls"])
        if "tool_call_id" in message:
            entry["tool_call_id"] = message["tool_call_id"]
        serializable.append(entry)
    return serializable


class TurnJournal:
    """Atomic turn records and replay metadata for one world directory."""

    def __init__(
        self,
        world_dir: Path,
        *,
        world_id: str,
        module_name: str,
        owner_token: str = PROCESS_INSTANCE_ID,
    ) -> None:
        self.world_dir = Path(world_dir).resolve()
        self.turns_dir = self.world_dir / "turns"
        self.index_path = self.turns_dir / "index.json"
        self.lock_path = self.turns_dir / ".turns.lock"
        self.world_id = world_id
        self.module_name = module_name
        self.owner_token = owner_token
        self._thread_lock = _journal_lock(self.turns_dir)
        self._active_events: dict[str, list[dict]] = {}
        self._started_at: dict[str, float] = {}
        self.turns_dir.mkdir(parents=True, exist_ok=True)
        self.recover_stale_turn()

    @staticmethod
    def _empty_index() -> dict:
        return {
            "schema_version": TURN_RECORD_SCHEMA_VERSION,
            "active_turn_id": None,
            "latest_completed_turn_id": None,
        }

    def _turn_dir(self, turn_id: str) -> Path:
        if not _TURN_ID.fullmatch(str(turn_id)):
            raise ValueError(f"非法 turn_id: {turn_id!r}")
        return self.turns_dir / turn_id

    def _record_path(self, turn_id: str) -> Path:
        return self._turn_dir(turn_id) / "record.json"

    def _load_index_unlocked(self) -> dict:
        if not self.index_path.exists():
            return self._empty_index()
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_index()
        if not isinstance(data, dict):
            return self._empty_index()
        index = self._empty_index()
        index.update(data)
        return index

    def _read_record_unlocked(self, turn_id: str) -> dict:
        path = self._record_path(turn_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TurnNotFoundError(f"回合记录不存在: {turn_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise TurnJournalError(f"无法读取回合记录 {turn_id}: {exc}") from exc
        if not isinstance(data, dict):
            raise TurnJournalError(f"回合记录根节点不是 object: {turn_id}")
        return data

    def _write_record_unlocked(self, record: dict) -> None:
        turn_dir = self._turn_dir(str(record["turn_id"]))
        turn_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(turn_dir / "record.json", record)

    def _recover_stale_unlocked(self, index: dict) -> bool:
        active_id = index.get("active_turn_id")
        if not active_id:
            return False
        try:
            record = self._read_record_unlocked(str(active_id))
        except TurnJournalError:
            index["active_turn_id"] = None
            return True
        if record.get("status") != "active":
            index["active_turn_id"] = None
            return True
        if record.get("owner_token") == self.owner_token:
            return False
        record.update({
            "status": "interrupted",
            "interrupted_at": _now(),
            "error": "服务进程在回合提交前结束",
        })
        self._write_record_unlocked(record)
        index["active_turn_id"] = None
        return True

    def recover_stale_turn(self) -> dict | None:
        """Mark a record owned by a previous server process as interrupted."""
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            active_id = index.get("active_turn_id")
            changed = self._recover_stale_unlocked(index)
            if changed:
                atomic_write_json(self.index_path, index)
            if not active_id:
                return None
            try:
                return self._read_record_unlocked(str(active_id))
            except TurnJournalError:
                return None

    def begin(self, *, kind: str, player_input: str | None) -> str:
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            if self._recover_stale_unlocked(index):
                atomic_write_json(self.index_path, index)
            active_id = index.get("active_turn_id")
            if active_id:
                raise ActiveTurnError(f"回合 {active_id} 尚未结束")

            turn_id = _new_turn_id()
            record = {
                "schema_version": TURN_RECORD_SCHEMA_VERSION,
                "turn_id": turn_id,
                "world_id": self.world_id,
                "module_name": self.module_name,
                "parent_turn_id": index.get("latest_completed_turn_id"),
                "kind": str(kind or "action"),
                "status": "active",
                "created_at": _now(),
                "owner_token": self.owner_token,
                "player_input": player_input,
                "events": [],
            }
            self._write_record_unlocked(record)
            index["active_turn_id"] = turn_id
            atomic_write_json(self.index_path, index)
            self._active_events[turn_id] = []
            self._started_at[turn_id] = time.monotonic()
            return turn_id

    def append_event(self, turn_id: str | None, payload: dict) -> None:
        if not turn_id or payload.get("type") not in _REPLAY_EVENT_TYPES:
            return
        event = _json_safe(payload)
        event.pop("turn_id", None)
        event.pop("seq", None)
        if event.get("type") == "handout":
            event.pop("asset_data_uri", None)
        with self._thread_lock:
            events = self._active_events.get(turn_id)
            if events is None:
                return
            elapsed = max(0, int((time.monotonic() - self._started_at[turn_id]) * 1000))
            event["offset_ms"] = elapsed
            if (
                event.get("type") == "narrative_chunk"
                and events
                and events[-1].get("type") == "narrative_chunk"
            ):
                events[-1]["text"] = (
                    str(events[-1].get("text", "")) + str(event.get("text", ""))
                )
                events[-1]["offset_ms"] = elapsed
                return
            events.append(event)

    def complete(
        self,
        turn_id: str,
        *,
        messages: list[dict],
        world_state: dict,
        narrative: str,
        choices: list[dict],
        executed_tools: list[dict] | None = None,
        lore_entry_ids: list[str] | None = None,
        diagnostics: dict | None = None,
    ) -> dict:
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            record = self._read_record_unlocked(turn_id)
            if record.get("status") == "completed":
                return record
            if record.get("status") != "active":
                raise TurnJournalError(
                    f"回合 {turn_id} 状态为 {record.get('status')}，不能提交"
                )

            serializable_messages = serialize_messages(messages)
            turn_dir = self._turn_dir(turn_id)
            atomic_write_json(turn_dir / "messages.json", serializable_messages)
            atomic_write_json(turn_dir / "snapshot.json", _json_safe(world_state))

            events = self._active_events.pop(turn_id, [])
            started_at = self._started_at.pop(turn_id, None)
            record.update({
                "status": "completed",
                "completed_at": _now(),
                "duration_ms": (
                    max(0, int((time.monotonic() - started_at) * 1000))
                    if started_at is not None else None
                ),
                "world_revision": int(world_state.get("revision", 0)),
                "message_count": len(serializable_messages),
                "narrative": str(narrative or ""),
                "choices": _json_safe(choices),
                "events": events,
                "executed_tools": _json_safe(executed_tools or []),
                "lore_entry_ids": [str(item) for item in (lore_entry_ids or [])],
                "diagnostics": _json_safe(diagnostics or {}),
            })
            # record.json is the commit marker: snapshots are written first.
            self._write_record_unlocked(record)
            index["active_turn_id"] = None
            index["latest_completed_turn_id"] = turn_id
            atomic_write_json(self.index_path, index)
            return copy.deepcopy(record)

    def finish_incomplete(
        self,
        turn_id: str,
        *,
        status: str,
        error: str = "",
    ) -> dict:
        if status not in {"cancelled", "failed", "interrupted"}:
            raise ValueError(f"非法回合结束状态: {status}")
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            record = self._read_record_unlocked(turn_id)
            if record.get("status") != "active":
                return record
            self._active_events.pop(turn_id, None)
            started_at = self._started_at.pop(turn_id, None)
            record.update({
                "status": status,
                f"{status}_at": _now(),
                "duration_ms": (
                    max(0, int((time.monotonic() - started_at) * 1000))
                    if started_at is not None else None
                ),
                "error": str(error or ""),
            })
            self._write_record_unlocked(record)
            if index.get("active_turn_id") == turn_id:
                index["active_turn_id"] = None
                atomic_write_json(self.index_path, index)
            return copy.deepcopy(record)

    def read(self, turn_id: str) -> dict:
        with self._thread_lock, file_lock(self.lock_path):
            return copy.deepcopy(self._read_record_unlocked(turn_id))

    def load_artifacts(self, turn_id: str) -> tuple[list[dict], dict]:
        with self._thread_lock, file_lock(self.lock_path):
            record = self._read_record_unlocked(turn_id)
            if record.get("status") != "completed":
                raise TurnJournalError(f"回合 {turn_id} 尚未完整提交")
            turn_dir = self._turn_dir(turn_id)
            try:
                messages = json.loads((turn_dir / "messages.json").read_text(encoding="utf-8"))
                snapshot = json.loads((turn_dir / "snapshot.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TurnJournalError(f"回合 {turn_id} 的快照不完整: {exc}") from exc
            return messages, snapshot

    def latest_completed_id(self) -> str | None:
        with self._thread_lock, file_lock(self.lock_path):
            return self._load_index_unlocked().get("latest_completed_turn_id")

    def _lineage_unlocked(self, turn_id: str | None) -> list[dict]:
        lineage: list[dict] = []
        visited: set[str] = set()
        current_id = turn_id
        while current_id:
            if current_id in visited:
                raise TurnJournalError(f"回合父链存在循环: {current_id}")
            visited.add(current_id)
            record = self._read_record_unlocked(current_id)
            if record.get("status") != "completed":
                raise TurnJournalError(f"回合 {current_id} 尚未完整提交")
            lineage.append(record)
            parent = record.get("parent_turn_id")
            current_id = str(parent) if parent else None
        lineage.reverse()
        return lineage

    def public_history(self, turn_id: str | None = None) -> list[dict]:
        """Return the active lineage without private tool output or prompt content."""
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            target_id = turn_id or index.get("latest_completed_turn_id")
            if not target_id:
                return []
            return [
                {
                    "turn_id": record.get("turn_id"),
                    "kind": record.get("kind"),
                    "player_input": record.get("player_input"),
                    "narrative": record.get("narrative", ""),
                    "choices": copy.deepcopy(record.get("choices", [])),
                    "completed_at": record.get("completed_at"),
                    "world_revision": record.get("world_revision"),
                }
                for record in self._lineage_unlocked(str(target_id))
            ]

    def clone_lineage_to(self, target: "TurnJournal", through_turn_id: str) -> None:
        """Clone committed artifacts through one turn into an empty world journal."""
        artifacts: list[tuple[dict, list[dict], dict]] = []
        with self._thread_lock, file_lock(self.lock_path):
            for record in self._lineage_unlocked(through_turn_id):
                turn_id = str(record["turn_id"])
                turn_dir = self._turn_dir(turn_id)
                try:
                    messages = json.loads(
                        (turn_dir / "messages.json").read_text(encoding="utf-8")
                    )
                    snapshot = json.loads(
                        (turn_dir / "snapshot.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise TurnJournalError(
                        f"回合 {turn_id} 的分支快照不完整: {exc}"
                    ) from exc
                artifacts.append((copy.deepcopy(record), messages, snapshot))

        with target._thread_lock, file_lock(target.lock_path):
            target_index = target._load_index_unlocked()
            if target_index.get("active_turn_id") or target_index.get(
                "latest_completed_turn_id"
            ):
                raise TurnJournalError("目标世界已经包含回合记录")
            for record, messages, snapshot in artifacts:
                turn_id = str(record["turn_id"])
                origin_world_id = record.get("origin_world_id") or record.get("world_id")
                record["origin_world_id"] = origin_world_id
                record["world_id"] = target.world_id
                record["module_name"] = target.module_name
                turn_dir = target._turn_dir(turn_id)
                turn_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(turn_dir / "messages.json", _json_safe(messages))
                atomic_write_json(turn_dir / "snapshot.json", _json_safe(snapshot))
                target._write_record_unlocked(record)
            target_index["active_turn_id"] = None
            target_index["latest_completed_turn_id"] = through_turn_id
            atomic_write_json(target.index_path, target_index)

    def add_narrative_variant(
        self,
        turn_id: str,
        *,
        narrative: str,
        messages: list[dict],
        model: str,
        diagnostics: list[dict] | None = None,
    ) -> dict:
        """Select a new prose variant without touching the authoritative snapshot."""
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            if index.get("latest_completed_turn_id") != turn_id:
                raise TurnJournalError("只能重新叙述当前世界的最后一个完整回合")
            record = self._read_record_unlocked(turn_id)
            if record.get("status") != "completed":
                raise TurnJournalError(f"回合 {turn_id} 尚未完整提交")

            variants = record.get("narrative_variants")
            if not isinstance(variants, list):
                variants = []
            if not variants:
                variants.append({
                    "variant_id": "original",
                    "created_at": record.get("completed_at"),
                    "narrative": record.get("narrative", ""),
                    "model": None,
                    "selected": False,
                })
            for variant in variants:
                if isinstance(variant, dict):
                    variant["selected"] = False
            variant_id = f"variant_{len(variants):03d}"
            variant = {
                "variant_id": variant_id,
                "created_at": _now(),
                "narrative": narrative,
                "model": model,
                "selected": True,
            }
            variants.append(variant)

            events = [
                copy.deepcopy(event)
                for event in record.get("events", [])
                if isinstance(event, dict)
            ]
            narrative_indices = [
                index for index, event in enumerate(events)
                if event.get("type") == "narrative_chunk"
            ]
            if narrative_indices:
                insertion_source = narrative_indices[-1]
                offset_ms = events[insertion_source].get("offset_ms", 0)
                insertion_index = sum(
                    1 for event in events[:insertion_source]
                    if event.get("type") != "narrative_chunk"
                )
                events = [
                    event for event in events
                    if event.get("type") != "narrative_chunk"
                ]
                events.insert(insertion_index, {
                    "type": "narrative_chunk",
                    "text": narrative,
                    "offset_ms": offset_ms,
                })
            else:
                events.append({
                    "type": "narrative_chunk",
                    "text": narrative,
                    "offset_ms": 0,
                })

            serializable_messages = serialize_messages(messages)
            atomic_write_json(
                self._turn_dir(turn_id) / "messages.json",
                serializable_messages,
            )
            rewrite_diagnostics = record.get("rewrite_diagnostics")
            if not isinstance(rewrite_diagnostics, list):
                rewrite_diagnostics = []
            rewrite_diagnostics.append({
                "variant_id": variant_id,
                "created_at": variant["created_at"],
                "model_calls": _json_safe(diagnostics or []),
            })
            record.update({
                "narrative": narrative,
                "narrative_variants": variants,
                "selected_variant_id": variant_id,
                "rewrite_diagnostics": rewrite_diagnostics,
                "events": events,
                "message_count": len(serializable_messages),
                "updated_at": _now(),
            })
            self._write_record_unlocked(record)
            return copy.deepcopy(variant)

    @staticmethod
    def _public_record(record: dict | None) -> dict | None:
        if not record:
            return None
        return {
            key: copy.deepcopy(record.get(key))
            for key in (
                "turn_id",
                "parent_turn_id",
                "kind",
                "status",
                "created_at",
                "completed_at",
                "interrupted_at",
                "duration_ms",
                "player_input",
                "narrative",
                "choices",
                "events",
                "world_revision",
                "message_count",
                "error",
            )
            if key in record
        }

    def recovery_status(self, requested_turn_id: str | None = None) -> dict:
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            if self._recover_stale_unlocked(index):
                atomic_write_json(self.index_path, index)

            def optional(turn_id: str | None) -> dict | None:
                if not turn_id:
                    return None
                try:
                    return self._read_record_unlocked(turn_id)
                except TurnJournalError:
                    return None

            requested = optional(requested_turn_id)
            active = optional(index.get("active_turn_id"))
            latest = optional(index.get("latest_completed_turn_id"))
            return {
                "requested": self._public_record(requested),
                "active": self._public_record(active),
                "latest_completed": self._public_record(latest),
            }

    def diagnostic_report(self, turn_id: str | None = None) -> dict | None:
        """Return metadata only; prompt text and authoritative tool output stay private."""
        with self._thread_lock, file_lock(self.lock_path):
            index = self._load_index_unlocked()
            target_id = turn_id or index.get("latest_completed_turn_id")
            if not target_id:
                return None
            record = self._read_record_unlocked(str(target_id))
            if record.get("status") != "completed":
                return None
            diagnostics = record.get("diagnostics")
            if not isinstance(diagnostics, dict):
                diagnostics = {}
            calls = diagnostics.get("model_calls")
            if not isinstance(calls, list):
                calls = []
            lorebook = diagnostics.get("lorebook")
            if not isinstance(lorebook, dict):
                lorebook = {}
            tool_names = [
                str(item.get("name"))
                for item in record.get("executed_tools", [])
                if isinstance(item, dict) and item.get("name")
            ]
            event_counts: dict[str, int] = {}
            for event in record.get("events", []):
                if not isinstance(event, dict) or not event.get("type"):
                    continue
                event_type = str(event["type"])
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
            return {
                "turn_id": record.get("turn_id"),
                "kind": record.get("kind"),
                "completed_at": record.get("completed_at"),
                "duration_ms": record.get("duration_ms"),
                "world_revision": record.get("world_revision"),
                "message_count": record.get("message_count"),
                "model_calls": copy.deepcopy(calls),
                "lorebook": copy.deepcopy(lorebook),
                "tool_names": tool_names,
                "event_counts": event_counts,
            }

    def list_completed(self, *, limit: int = 100) -> list[dict]:
        records: list[dict] = []
        with self._thread_lock, file_lock(self.lock_path):
            for path in self.turns_dir.glob("turn_*/record.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(record, dict) and record.get("status") == "completed":
                    records.append(record)
        records.sort(key=lambda item: str(item.get("completed_at", "")), reverse=True)
        return [self._public_record(item) or {} for item in records[:max(0, limit)]]
