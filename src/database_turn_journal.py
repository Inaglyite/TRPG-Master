"""Transactional turn journal stored in PostgreSQL/JSONB."""

from __future__ import annotations

import copy
import os
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .config import AUTO_SAVE_SLOT
from .database import (
    ModelCall,
    SaveSlot,
    Snapshot,
    Turn,
    TurnEvent,
    World,
    WorldState,
    database_url,
    new_id,
    session_scope,
    utcnow,
)
from .turn_journal import (
    PROCESS_INSTANCE_ID,
    TURN_RECORD_SCHEMA_VERSION,
    ActiveTurnError,
    TurnJournalError,
    TurnNotFoundError,
    _json_safe,
    _new_turn_id,
    _now,
    serialize_messages,
)
from .world_store import StaleRevisionError, atomic_write_json

_REPLAY_EVENT_TYPES = {
    "narrative_chunk",
    "narrative_segment",
    "tension",
    "dice_result",
    "glm_summary",
    "handout",
    "error",
    "choices",
}


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class DatabaseTurnJournal:
    def __init__(
        self,
        world_dir: Path,
        *,
        world_id: str,
        module_name: str,
        owner_token: str = PROCESS_INSTANCE_ID,
    ) -> None:
        self.world_dir = Path(world_dir).resolve()
        runtime_root = self.world_dir.parent.parent
        self.database_url = database_url(runtime_root)
        self.world_id = world_id
        self.module_name = module_name
        self.owner_token = owner_token
        self._active_events: dict[str, list[dict]] = {}
        self._started_at: dict[str, float] = {}
        self.recover_stale_turn()

    def _export_completed(self, record: dict, messages: list[dict], snapshot: dict) -> None:
        """Optional derived JSON export for desktop/backward compatibility."""
        if os.environ.get("TRPG_WRITE_COMPAT_EXPORTS", "1").lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return
        turn_dir = self.world_dir / "turns" / str(record["turn_id"])
        atomic_write_json(turn_dir / "messages.json", messages)
        atomic_write_json(turn_dir / "snapshot.json", snapshot)
        atomic_write_json(turn_dir / "record.json", record)
        atomic_write_json(
            self.world_dir / "turns" / "index.json",
            {
                "schema_version": TURN_RECORD_SCHEMA_VERSION,
                "active_turn_id": None,
                "latest_completed_turn_id": record["turn_id"],
            },
        )

    @staticmethod
    def _record(row: Turn) -> dict:
        return copy.deepcopy(row.record or {})

    def _row(self, session, turn_id: str) -> Turn:
        row = session.scalar(select(Turn).where(Turn.world_id == self.world_id, Turn.id == turn_id))
        if row is None:
            raise TurnNotFoundError(f"回合记录不存在: {turn_id}")
        return row

    def recover_stale_turn(self) -> dict | None:
        with session_scope(self.database_url) as session:
            rows = session.scalars(
                select(Turn)
                .where(Turn.world_id == self.world_id, Turn.status == "active")
                .with_for_update()
            ).all()
            recovered = None
            for row in rows:
                record = self._record(row)
                if row.owner_token == self.owner_token:
                    recovered = record
                    continue
                record.update(
                    {
                        "status": "interrupted",
                        "interrupted_at": _now(),
                        "error": "服务进程在回合提交前结束",
                    }
                )
                row.status = "interrupted"
                row.record = record
                recovered = record
            return copy.deepcopy(recovered)

    def begin(self, *, kind: str, player_input: str | None) -> str:
        with session_scope(self.database_url) as session:
            world = session.scalar(select(World).where(World.id == self.world_id).with_for_update())
            if world is None:
                raise TurnJournalError(f"世界不存在: {self.world_id}")
            active = session.scalar(
                select(Turn).where(Turn.world_id == self.world_id, Turn.status == "active")
            )
            if active:
                raise ActiveTurnError(f"回合 {active.id} 尚未结束")
            latest = session.scalar(
                select(Turn)
                .where(Turn.world_id == self.world_id, Turn.status == "completed")
                .order_by(Turn.completed_at.desc())
                .limit(1)
            )
            turn_id = _new_turn_id()
            record = {
                "schema_version": TURN_RECORD_SCHEMA_VERSION,
                "turn_id": turn_id,
                "world_id": self.world_id,
                "module_name": self.module_name,
                "parent_turn_id": latest.id if latest else None,
                "kind": str(kind or "action"),
                "status": "active",
                "created_at": _now(),
                "owner_token": self.owner_token,
                "player_input": player_input,
                "events": [],
            }
            session.add(
                Turn(
                    pk=new_id("turnrow"),
                    id=turn_id,
                    world_id=self.world_id,
                    parent_turn_id=record["parent_turn_id"],
                    kind=record["kind"],
                    status="active",
                    owner_token=self.owner_token,
                    player_input=player_input,
                    record=record,
                )
            )
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
        events = self._active_events.get(turn_id)
        if events is None:
            return
        elapsed = max(0, int((time.monotonic() - self._started_at[turn_id]) * 1000))
        event["offset_ms"] = elapsed
        if (
            event.get("type") == "narrative_chunk"
            and events
            and events[-1].get("type") == "narrative_chunk"
            and not event.get("npc_id")
            and not events[-1].get("npc_id")
        ):
            events[-1]["text"] = str(events[-1].get("text", "")) + str(event.get("text", ""))
            events[-1]["offset_ms"] = elapsed
        else:
            events.append(event)

    def complete(
        self,
        turn_id: str,
        *,
        messages: list[dict],
        world_state: dict,
        narrative: str,
        choices: list[dict],
        executed_tools=None,
        lore_entry_ids=None,
        diagnostics=None,
        narrative_segments=None,
        expected_world_revision: int | None = None,
    ) -> dict:
        journal_started = time.monotonic()
        with session_scope(self.database_url) as session:
            row = self._row(session, turn_id)
            if row.status == "completed":
                return self._record(row)
            if row.status != "active":
                raise TurnJournalError(f"回合 {turn_id} 状态为 {row.status}，不能提交")
            serializable = serialize_messages(messages)
            if expected_world_revision is not None:
                world_row = session.scalar(
                    select(WorldState)
                    .where(WorldState.world_id == self.world_id)
                    .with_for_update()
                )
                if world_row is None:
                    raise TurnJournalError(f"世界状态不存在: {self.world_id}")
                if world_row.revision != expected_world_revision:
                    raise StaleRevisionError(
                        expected_world_revision, world_row.revision
                    )
                world_row.state = _json_safe(world_state)
                world_row.revision = int(world_state.get("revision", 0))
                world_row.schema_version = int(world_state.get("schema_version", 0))
                world_row.updated_at = utcnow()
            events = self._active_events.pop(turn_id, [])
            started_at = self._started_at.pop(turn_id, None)
            record = self._record(row)
            record.update(
                {
                    "status": "completed",
                    "completed_at": _now(),
                    "duration_ms": max(0, int((time.monotonic() - started_at) * 1000))
                    if started_at
                    else None,
                    "world_revision": int(world_state.get("revision", 0)),
                    "message_count": len(serializable),
                    "narrative": str(narrative or ""),
                    "choices": _json_safe(choices),
                    "narrative_segments": _json_safe(narrative_segments or []),
                    "events": events,
                    "executed_tools": _json_safe(executed_tools or []),
                    "lore_entry_ids": [str(x) for x in (lore_entry_ids or [])],
                    "diagnostics": _json_safe(diagnostics or {}),
                }
            )
            row.status = "completed"
            row.record = copy.deepcopy(record)
            row.messages = serializable
            snapshot_row = Snapshot(
                id=new_id("snapshot"),
                world_id=self.world_id,
                source_turn_id=turn_id,
                kind="turn",
                revision=int(world_state.get("revision", 0)),
                state=_json_safe(world_state),
            )
            session.add(snapshot_row)
            row.snapshot_id = snapshot_row.id
            row.completed_at = _dt(record["completed_at"]) or utcnow()
            auto_save = (
                session.query(SaveSlot)
                .filter_by(world_id=self.world_id, slot_key=AUTO_SAVE_SLOT)
                .one_or_none()
            )
            if auto_save is None:
                auto_save = SaveSlot(
                    id=new_id("save"),
                    world_id=self.world_id,
                    slot_key=AUTO_SAVE_SLOT,
                    kind="auto",
                    snapshot_id=snapshot_row.id,
                )
                session.add(auto_save)
            auto_save.snapshot_id = snapshot_row.id
            auto_save.messages = serializable
            auto_save.world_revision = int(world_state.get("revision", 0))
            auto_save.metadata_json = self._save_metadata(world_state, serializable)
            auto_save.updated_at = utcnow()
            for sequence, event in enumerate(events):
                session.add(
                    TurnEvent(
                        id=new_id("event"),
                        turn_pk=row.pk,
                        turn_id=turn_id,
                        sequence=sequence,
                        event_type=str(event.get("type") or "unknown"),
                        payload=event,
                    )
                )
            for call in (diagnostics or {}).get("model_calls", []):
                if not isinstance(call, dict):
                    continue
                usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
                session.add(
                    ModelCall(
                        id=new_id("modelcall"),
                        turn_pk=row.pk,
                        model=str(call.get("model") or ""),
                        prompt_profile=str(call.get("prompt_profile") or ""),
                        duration_ms=call.get("elapsed_ms"),
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        total_tokens=usage.get("total_tokens"),
                        success=str(call.get("status") or "ok") not in {"error", "failed"},
                        details=_json_safe(call),
                    )
                )
            session.flush()
            performance = record.setdefault("diagnostics", {}).setdefault(
                "performance", {}
            )
            performance.setdefault("phases_ms", {})["journal_commit"] = round(
                (time.monotonic() - journal_started) * 1000, 3
            )
            row.record = copy.deepcopy(record)
            self._export_completed(record, serializable, world_state)
            self._export_auto_save(serializable, world_state, auto_save.metadata_json)
            return copy.deepcopy(record)

    def _save_metadata(self, state: dict, messages: list[dict]) -> dict:
        pc = state.get("pc", {})
        scene = state.get("current_scene", {})
        clues = state.get("clues_found", {})
        clue_count = (
            sum(len(items) for items in clues.values())
            if isinstance(clues, dict)
            else len(clues) if isinstance(clues, list) else 0
        )
        return {
            "created_at": _now(),
            "scene_id": scene.get("id", ""),
            "scene_name": scene.get("name", ""),
            "character_id": pc.get("character_id", ""),
            "character_name": pc.get("name", ""),
            "hp": f"{pc.get('hp', 0)}/{pc.get('max_hp', 0)}",
            "san": f"{pc.get('san', 0)}/{pc.get('max_san', 0)}",
            "clue_count": clue_count,
            "message_count": len(messages),
            "world_id": self.world_id,
            "module_name": self.module_name,
            "world_revision": int(state.get("revision", 0)),
            "schema_version": int(state.get("schema_version", 0)),
        }

    def _export_auto_save(self, messages: list[dict], state: dict, metadata: dict) -> None:
        if os.environ.get("TRPG_WRITE_COMPAT_EXPORTS", "1").lower() not in {
            "1", "true", "yes", "on"
        }:
            return
        slot_dir = self.world_dir / "saves" / AUTO_SAVE_SLOT
        slot_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(slot_dir / "messages.json", messages)
        atomic_write_json(slot_dir / "snapshot.json", state)
        atomic_write_json(slot_dir / "meta.json", metadata)

    def finish_incomplete(self, turn_id: str, *, status: str, error: str = "") -> dict:
        if status not in {"cancelled", "failed", "interrupted"}:
            raise ValueError(f"非法回合结束状态: {status}")
        with session_scope(self.database_url) as session:
            row = self._row(session, turn_id)
            if row.status != "active":
                return self._record(row)
            started = self._started_at.pop(turn_id, None)
            self._active_events.pop(turn_id, None)
            record = self._record(row)
            record.update(
                {
                    "status": status,
                    f"{status}_at": _now(),
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000))
                    if started
                    else None,
                    "error": str(error or ""),
                }
            )
            row.status = status
            row.record = record
            return copy.deepcopy(record)

    def read(self, turn_id: str) -> dict:
        with session_scope(self.database_url) as session:
            return self._record(self._row(session, turn_id))

    def load_artifacts(self, turn_id: str) -> tuple[list[dict], dict]:
        with session_scope(self.database_url) as session:
            row = self._row(session, turn_id)
            if row.status != "completed" or row.snapshot_id is None:
                raise TurnJournalError(f"回合 {turn_id} 尚未完整提交")
            snapshot = session.get(Snapshot, row.snapshot_id)
            if snapshot is None:
                raise TurnJournalError(f"回合 {turn_id} 的快照不存在")
            return copy.deepcopy(row.messages or []), copy.deepcopy(snapshot.state)

    def latest_completed_id(self) -> str | None:
        with session_scope(self.database_url) as session:
            row = session.scalar(
                select(Turn)
                .where(Turn.world_id == self.world_id, Turn.status == "completed")
                .order_by(Turn.completed_at.desc())
                .limit(1)
            )
            return row.id if row else None

    def _lineage(self, session, turn_id: str | None) -> list[Turn]:
        result = []
        visited = set()
        current = turn_id
        while current:
            if current in visited:
                raise TurnJournalError(f"回合父链存在循环: {current}")
            visited.add(current)
            row = self._row(session, current)
            if row.status != "completed":
                raise TurnJournalError(f"回合 {current} 尚未完整提交")
            result.append(row)
            current = row.parent_turn_id
        result.reverse()
        return result

    def public_history(self, turn_id: str | None = None) -> list[dict]:
        with session_scope(self.database_url) as session:
            target = turn_id or self.latest_completed_id()
            return (
                [
                    {
                        k: copy.deepcopy(r.record.get(k))
                        for k in (
                            "turn_id",
                            "parent_turn_id",
                            "kind",
                            "player_input",
                            "narrative",
                            "choices",
                            "narrative_segments",
                            "completed_at",
                            "world_revision",
                        )
                    }
                    for r in self._lineage(session, target)
                ]
                if target
                else []
            )

    def clone_lineage_to(self, target: DatabaseTurnJournal, through_turn_id: str) -> None:
        with session_scope(self.database_url) as source_session:
            source = []
            for row in self._lineage(source_session, through_turn_id):
                snapshot = source_session.get(Snapshot, row.snapshot_id)
                if snapshot is None:
                    raise TurnJournalError(f"回合 {row.id} 的快照不存在")
                source.append(
                    (self._record(row), copy.deepcopy(row.messages), copy.deepcopy(snapshot.state))
                )
        with session_scope(target.database_url) as session:
            if session.scalar(select(Turn).where(Turn.world_id == target.world_id).limit(1)):
                raise TurnJournalError("目标世界已经包含回合记录")
            for record, messages, snapshot in source:
                origin = record.get("origin_world_id") or record.get("world_id")
                record.update(
                    {
                        "origin_world_id": origin,
                        "world_id": target.world_id,
                        "module_name": target.module_name,
                    }
                )
                snapshot_row = Snapshot(
                    id=new_id("snapshot"),
                    world_id=target.world_id,
                    source_turn_id=record["turn_id"],
                    kind="branch",
                    revision=int(snapshot.get("revision", 0)),
                    state=snapshot,
                )
                session.add(snapshot_row)
                cloned = Turn(
                    pk=new_id("turnrow"),
                    id=record["turn_id"],
                    world_id=target.world_id,
                    parent_turn_id=record.get("parent_turn_id"),
                    origin_world_id=origin,
                    kind=record.get("kind", "action"),
                    status="completed",
                    owner_token=record.get("owner_token", ""),
                    player_input=record.get("player_input"),
                    record=record,
                    messages=messages,
                    snapshot_id=snapshot_row.id,
                    created_at=_dt(record.get("created_at")) or utcnow(),
                    completed_at=_dt(record.get("completed_at")) or utcnow(),
                )
                session.add(cloned)
                session.flush()
                for sequence, event in enumerate(record.get("events", [])):
                    if isinstance(event, dict):
                        session.add(
                            TurnEvent(
                                id=new_id("event"),
                                turn_pk=cloned.pk,
                                turn_id=cloned.id,
                                sequence=sequence,
                                event_type=str(event.get("type") or "unknown"),
                                payload=event,
                            )
                        )
                target._export_completed(record, messages, snapshot)

    def add_narrative_variant(
        self,
        turn_id: str,
        *,
        narrative: str,
        messages: list[dict],
        model: str,
        diagnostics=None,
        narrative_segments=None,
    ) -> dict:
        if self.latest_completed_id() != turn_id:
            raise TurnJournalError("只能重新叙述当前世界的最后一个完整回合")
        with session_scope(self.database_url) as session:
            row = self._row(session, turn_id)
            record = self._record(row)
            variants = record.get("narrative_variants") or []
            if not variants:
                variants.append(
                    {
                        "variant_id": "original",
                        "created_at": record.get("completed_at"),
                        "narrative": record.get("narrative", ""),
                        "model": None,
                        "selected": False,
                    }
                )
            for value in variants:
                value["selected"] = False
            variant = {
                "variant_id": f"variant_{len(variants):03d}",
                "created_at": _now(),
                "narrative": narrative,
                "model": model,
                "selected": True,
                "narrative_segments": _json_safe(narrative_segments or []),
            }
            variants.append(variant)
            events = [e for e in record.get("events", []) if e.get("type") != "narrative_chunk"]
            events.append({"type": "narrative_chunk", "text": narrative, "offset_ms": 0})
            rewrites = record.get("rewrite_diagnostics") or []
            rewrites.append(
                {
                    "variant_id": variant["variant_id"],
                    "created_at": variant["created_at"],
                    "model_calls": _json_safe(diagnostics or []),
                }
            )
            record.update(
                {
                    "narrative": narrative,
                    "narrative_variants": variants,
                    "selected_variant_id": variant["variant_id"],
                    "rewrite_diagnostics": rewrites,
                    "narrative_segments": _json_safe(narrative_segments or []),
                    "events": events,
                    "message_count": len(messages),
                    "updated_at": _now(),
                }
            )
            row.record = record
            row.messages = serialize_messages(messages)
            return copy.deepcopy(variant)

    @staticmethod
    def _public_record(record: dict | None) -> dict | None:
        if not record:
            return None
        keys = (
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
            "narrative_segments",
            "events",
            "world_revision",
            "message_count",
            "error",
        )
        return {k: copy.deepcopy(record.get(k)) for k in keys if k in record}

    def recovery_status(self, requested_turn_id: str | None = None) -> dict:
        self.recover_stale_turn()
        with session_scope(self.database_url) as session:

            def optional(value):
                row = self._row(session, value) if value else None
                return self._record(row) if row else None

            active = session.scalar(
                select(Turn).where(Turn.world_id == self.world_id, Turn.status == "active").limit(1)
            )
            latest = self.latest_completed_id()
            return {
                "requested": self._public_record(optional(requested_turn_id)),
                "active": self._public_record(self._record(active) if active else None),
                "latest_completed": self._public_record(optional(latest)),
            }

    def diagnostic_report(self, turn_id: str | None = None) -> dict | None:
        target = turn_id or self.latest_completed_id()
        if not target:
            return None
        record = self.read(target)
        if record.get("status") != "completed":
            return None
        diagnostics = record.get("diagnostics") or {}
        calls = diagnostics.get("model_calls") or []
        lorebook = diagnostics.get("lorebook") or {}
        tool_names = [str(x.get("name")) for x in record.get("executed_tools", []) if x.get("name")]
        counts = {}
        for event in record.get("events", []):
            kind = event.get("type")
            if kind:
                counts[kind] = counts.get(kind, 0) + 1
        return {
            "turn_id": record.get("turn_id"),
            "kind": record.get("kind"),
            "completed_at": record.get("completed_at"),
            "duration_ms": record.get("duration_ms"),
            "world_revision": record.get("world_revision"),
            "message_count": record.get("message_count"),
            "model_calls": copy.deepcopy(calls),
            "lorebook": copy.deepcopy(lorebook),
            "performance": copy.deepcopy(diagnostics.get("performance") or {}),
            "mutations": copy.deepcopy(diagnostics.get("mutations") or []),
            "tool_names": tool_names,
            "event_counts": counts,
        }

    def list_completed(self, *, limit: int = 100) -> list[dict]:
        with session_scope(self.database_url) as session:
            rows = session.scalars(
                select(Turn)
                .where(Turn.world_id == self.world_id, Turn.status == "completed")
                .order_by(Turn.completed_at.desc())
                .limit(max(0, limit))
            ).all()
            return [self._public_record(self._record(row)) or {} for row in rows]
