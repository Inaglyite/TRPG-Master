"""H2 append-only model-context event timeline (store + pure projection).

The authoritative turn/world state stays in the existing Turn/WorldState/
Snapshot rows.  This module only records *what the model saw*, so a later
release can rebuild the exact request surface instead of trusting a mutable
``messages`` list.

Design rules (H2 contract):
- ``ContextEventStore`` serializes writes with the World row lock (PostgreSQL;
  SQLite degrades to the same application-level serialization as the turn
  journal).  A world keeps one active session; ``begin_epoch`` closes it and
  opens a new epoch whose parent points at the old session + cutoff sequence,
  so resuming an old save can never see events written after the cutoff.
  ``begin_epoch`` validates ``0 <= cutoff <= head`` and never calls
  ``ensure_session`` inside an open transaction (it inlines the same logic).
- ``ContextProjector`` is pure: it takes ancestor→current sessions and per
  session events, applies ``replace`` checkpoints by masking only the explicit
  source references (``(session_id, sequence)`` pairs, never a bare sequence
  number, so a checkpoint can never mask an ancestor's same-numbered event),
  never deletes raw events, and refuses lineage cycles / excessive depth.
  Sibling branches are isolated by construction.
- Non-surface events (``request_envelope``, ``compaction_checkpoint``) never
  produce a message in the projection; checkpoints only mask, envelopes only
  record digests.
- Turn-scoped message events from failed/cancelled/interrupted turns never
  enter later projections; the current active turn is visible only when
  explicitly requested via ``include_turn_id``.
- ``payload`` is the model-visible message for that event and is never
  returned by ordinary APIs or diagnostics (see ``event_metadata``).
- ``request_envelope`` events store digests + section metadata only, never
  the prompt text.
- Writing events is fail-safe: ``safe_append`` returns a metadata-only error
  string on failure so a diagnostic can be recorded without touching the
  authoritative turn; payloads never go into logs.
- ``seed_legacy`` imports an old save in a single World-lock transaction
  (batch insert + idempotent re-entry): a concurrent seed with the same digest
  is a no-op, a different digest fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func

from .database import (
    ContextSession,
    ModelContextEvent,
    Turn,
    World,
    new_id,
    session_scope,
    utcnow,
)

# --------------------------------------------------------------------------
# Feature flags (H2): shadow writes on by default, never a read source yet.
# --------------------------------------------------------------------------

EVENT_SHADOW_FLAG = "TRPG_CONTEXT_EVENT_SHADOW"  # double-write + shadow compare
EVENT_READ_FLAG = "TRPG_CONTEXT_EVENT_READ"  # projection becomes the read source


def _flag_enabled(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def shadow_writes_enabled() -> bool:
    return _flag_enabled(EVENT_SHADOW_FLAG, True)


def projection_reads_enabled() -> bool:
    return _flag_enabled(EVENT_READ_FLAG, False)


# --------------------------------------------------------------------------
# Process-local write serialization: one RLock per (database_url, world_id).
# SQLite has no real row locks, so concurrent seeds/session-lifecycle writes
# would race on the partial unique index; the application-level lock keeps
# every write path (seed / ensure / begin_epoch / fork / append / GC) serial
# per world, matching the PostgreSQL row-lock semantics.
# --------------------------------------------------------------------------

_WRITE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _world_write_lock(url: str, world_id: str) -> threading.RLock:
    key = (url, world_id)
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITE_LOCKS[key] = lock
        return lock


# --------------------------------------------------------------------------
# Event vocabulary (H2 first batch).
# --------------------------------------------------------------------------

EVENT_ENTERED_PLAYER_ACTION = "entered_player_action"
EVENT_CONTEXT_INJECTION = "context_injection"
EVENT_REQUEST_ENVELOPE = "request_envelope"
EVENT_ASSISTANT_MESSAGE = "assistant_message"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_CHECKPOINT = "compaction_checkpoint"

# Events that produce a model-visible message in the projection.  The rest
# (request envelopes, checkpoints) are metadata-only: they never show up as a
# projected message, only influence the projection (masking) or diagnostics.
SURFACE_EVENT_TYPES = frozenset(
    {
        EVENT_ENTERED_PLAYER_ACTION,
        EVENT_CONTEXT_INJECTION,
        EVENT_ASSISTANT_MESSAGE,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
    }
)

LEGACY_SAVE_KIND = "legacy_save"
TURN_KIND = "turn"

# Mirrors engine.CONTROL_MESSAGE_PREFIX; kept here so the pure classifier has
# no import cycle with engine.py.  Keep in sync with src/engine.py.
CONTROL_MESSAGE_PREFIX = "[引擎控制指令｜非玩家发言]"

TURN_STATUS_COMMITTED = "completed"
TURN_STATUS_ACTIVE = "active"


def _is_control_message(message: dict[str, Any]) -> bool:
    return str(message.get("role") or "") == "user" and str(
        message.get("content") or ""
    ).startswith(CONTROL_MESSAGE_PREFIX)


def infer_event_type(message: dict[str, Any]) -> str:
    """Map a model-visible message role to its H2 event type.

    Programmatic control instructions are ``context_injection`` even though
    they arrive with role=user, so the projection keeps them visually and
    semantically separate from player actions.
    """
    role = str(message.get("role") or "")
    if role == "user":
        if _is_control_message(message):
            return EVENT_CONTEXT_INJECTION
        return EVENT_ENTERED_PLAYER_ACTION
    if role == "tool":
        return EVENT_TOOL_RESULT
    if role == "assistant":
        return EVENT_TOOL_CALL if message.get("tool_calls") else EVENT_ASSISTANT_MESSAGE
    # system / developer sections are seeded or injected separately.
    return EVENT_CONTEXT_INJECTION


def payload_digest(payload: object) -> str:
    """Stable digest of one message payload (canonical JSON)."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def messages_digest(messages: Sequence[dict[str, Any]]) -> str:
    """Digest of an ordered message list (normalized tool pairing)."""
    from .persistence import normalize_tool_message_history

    normalized = normalize_tool_message_history([dict(m) for m in messages])
    return payload_digest(normalized)


def _session_dict(row: ContextSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "world_id": row.world_id,
        "root_world_id": row.root_world_id,
        "session_epoch": row.session_epoch,
        "parent_session_id": row.parent_session_id,
        "parent_world_id": row.parent_world_id,
        "source_sequence": row.source_sequence,
        "head_sequence": row.head_sequence,
        "status": row.status,
        "seed_digest": row.seed_digest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }


# --------------------------------------------------------------------------
# Store: row-lock serialized session lifecycle + appends.
# --------------------------------------------------------------------------


class ContextEventStore:
    def __init__(self, url: str) -> None:
        self.url = url

    # -- session lifecycle ------------------------------------------------

    def _lock_world(self, session, world_id: str) -> World:
        world = session.query(World).filter_by(id=world_id).with_for_update().one_or_none()
        if world is None:
            raise ValueError(f"世界不存在: {world_id}")
        return world

    def _active_session(self, session, world_id: str) -> ContextSession | None:
        return (
            session.query(ContextSession)
            .filter_by(world_id=world_id, status="active")
            .one_or_none()
        )

    def _create_session_locked(
        self,
        session,
        world_id: str,
        *,
        root_world_id: str | None = None,
        parent_session_id: str | None = None,
        parent_world_id: str | None = None,
        source_sequence: int = 0,
        session_epoch: int = 1,
        cross_world_parent: bool = False,
    ) -> ContextSession:
        """Create the next session inside an already-locked transaction.

        Never opens a nested transaction; callers must hold the World row
        lock (``_lock_world``) before invoking this.  ``cross_world_parent``
        allows the parent to live in a different world (branch forks);
        without it the parent must belong to ``world_id`` (begin_epoch).
        """
        if parent_session_id is not None:
            parent = session.get(ContextSession, parent_session_id)
            if parent is None:
                raise ValueError(f"parent context session 不存在: {parent_session_id}")
            if not cross_world_parent and parent.world_id != world_id:
                raise ValueError("begin_epoch 的 parent session 不属于同一世界")
        root = root_world_id or world_id
        row = ContextSession(
            id=new_id("ctx"),
            world_id=world_id,
            root_world_id=root,
            session_epoch=session_epoch,
            parent_session_id=parent_session_id,
            parent_world_id=parent_world_id,
            source_sequence=source_sequence,
            head_sequence=0,
            status="active",
            seed_digest="",
        )
        session.add(row)
        session.flush()
        return row

    def ensure_session(
        self,
        world_id: str,
        root_world_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the active session, creating epoch 1 on first use.

        Seeding (legacy import) is managed by :meth:`seed_legacy`, which owns
        the ``seed_digest`` marker; this method never marks a session seeded.
        """
        with _world_write_lock(self.url, world_id):
            with session_scope(self.url) as session:
                self._lock_world(session, world_id)
                active = self._active_session(session, world_id)
                if active is not None:
                    return _session_dict(active)
                row = self._create_session_locked(session, world_id, root_world_id=root_world_id)
                return _session_dict(row)

    def begin_epoch(
        self,
        world_id: str,
        *,
        root_world_id: str | None = None,
        cutoff_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Close the active session and open a new epoch over the old save.

        ``cutoff_sequence`` is the old session's head at the moment the save
        was written; events appended after that cutoff belong to the future
        and must never leak into the resumed timeline.  The cutoff is
        validated against the closed session: ``0 <= cutoff <= head``.
        """
        with _world_write_lock(self.url, world_id):
            with session_scope(self.url) as session:
                self._lock_world(session, world_id)
                active = self._active_session(session, world_id)
                if active is None:
                    # No session yet: same as a first ensure but inlined in this
                    # transaction (never call ensure_session here — it would open
                    # a nested scope while we already hold the World lock).
                    row = self._create_session_locked(
                        session, world_id, root_world_id=root_world_id
                    )
                    return _session_dict(row)
                if cutoff_sequence is None:
                    cutoff_sequence = active.head_sequence
                cutoff = int(cutoff_sequence)
                if cutoff < 0 or cutoff > int(active.head_sequence):
                    raise ValueError(
                        f"begin_epoch cutoff {cutoff} 超出 [0, {active.head_sequence}]"
                    )
                active.status = "closed"
                active.closed_at = utcnow()
                new_row = self._create_session_locked(
                    session,
                    world_id,
                    root_world_id=root_world_id or active.root_world_id,
                    parent_session_id=active.id,
                    parent_world_id=active.world_id,
                    source_sequence=cutoff,
                    session_epoch=int(active.session_epoch) + 1,
                )
                return _session_dict(new_row)

    def fork_session(
        self,
        target_world_id: str,
        source_session_id: str,
        *,
        cutoff_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Create an active session in ``target_world_id`` forked from a
        source session (cross-world lineage, e.g. a branch clone).

        The new session's parent points at the source session; it inherits
        ``root_world_id`` from the source, and ``source_sequence`` is pinned
        to the fork cutoff so later projections see the ancestor prefix only
        up to the fork point.  The target world must exist and must not
        already have an active session.  Context events are never copied.

        Concurrency contract: the source world id is resolved in a
        read-only scope first, then *both* process-level write locks are
        acquired in sorted world-id order (never source-then-target, which
        could deadlock against an appender in the other order) and both
        World rows are row-locked in the same sorted order inside one
        transaction.  The source session and its head are re-read under
        those locks so an in-flight ``append`` to the source can never race
        the fork cutoff: either the append commits before the fork reads
        the new head, or the fork reads the older head and the append lands
        after the fork point.  ``source == target`` is rejected outright —
        a fork must cross worlds (use ``begin_epoch`` to continue a world).
        """
        # Read-only resolution of the source world before taking any lock,
        # so we know exactly which two worlds must be locked in order.
        with session_scope(self.url) as session:
            source_probe = session.get(ContextSession, source_session_id)
            if source_probe is None:
                raise ValueError(f"源 context session 不存在: {source_session_id}")
            source_world_id = source_probe.world_id
        if source_world_id == target_world_id:
            raise ValueError("fork_session 禁止 source 与 target 同世界（同世界请用 begin_epoch）")

        first, second = sorted((source_world_id, target_world_id))
        with _world_write_lock(self.url, first), _world_write_lock(self.url, second):
            with session_scope(self.url) as session:
                # Row-lock both worlds in the same sorted order inside one
                # transaction, then re-read the source under those locks.
                self._lock_world(session, first)
                self._lock_world(session, second)
                active = self._active_session(session, target_world_id)
                if active is not None:
                    raise ValueError(f"目标世界已有 active context session: {target_world_id}")
                source = session.get(ContextSession, source_session_id)
                if source is None:
                    raise ValueError(f"源 context session 不存在: {source_session_id}")
                cutoff = (
                    int(source.head_sequence) if cutoff_sequence is None else int(cutoff_sequence)
                )
                if cutoff < 0 or cutoff > int(source.head_sequence):
                    raise ValueError(f"fork cutoff {cutoff} 超出 [0, {source.head_sequence}]")
                max_epoch = (
                    session.query(func.max(ContextSession.session_epoch))
                    .filter_by(world_id=target_world_id)
                    .scalar()
                    or 0
                )
                row = self._create_session_locked(
                    session,
                    target_world_id,
                    root_world_id=source.root_world_id,
                    parent_session_id=source.id,
                    parent_world_id=source.world_id,
                    source_sequence=cutoff,
                    session_epoch=int(max_epoch) + 1,
                    cross_world_parent=True,
                )
                return _session_dict(row)

    def close_session(self, world_id: str) -> dict[str, Any] | None:
        with _world_write_lock(self.url, world_id):
            with session_scope(self.url) as session:
                self._lock_world(session, world_id)
                active = self._active_session(session, world_id)
                if active is None:
                    return None
                active.status = "closed"
                active.closed_at = utcnow()
                return _session_dict(active)

    def session_for_world(self, world_id: str) -> dict[str, Any] | None:
        with session_scope(self.url) as session:
            active = self._active_session(session, world_id)
            return _session_dict(active) if active is not None else None

    def seed_digest_for_world(self, world_id: str) -> str:
        session_row = self.session_for_world(world_id)
        return str((session_row or {}).get("seed_digest") or "")

    # -- appends ----------------------------------------------------------

    def append(
        self,
        session_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        world_id: str | None = None,
        root_world_id: str | None = None,
        turn_id: str | None = None,
        step: int | None = None,
        source_kind: str = "",
        source_id: str = "",
        source_version: str = "",
        audience: str = "model_private",
        sensitivity: str = "private",
        surface_op: str = "append",
        source_sequences: Sequence[int] | Sequence[dict[str, Any]] | None = None,
    ) -> int:
        """Append one immutable event, returning its session sequence.

        The session's world is resolved first (cheap read), then the write
        lock for that world is held and the ContextSession row is re-read
        under the lock (``populate_existing``), so a stale head cached in
        the identity map can never advance the wrong sequence.
        """
        if world_id is None:
            with session_scope(self.url) as session:
                session_row = session.get(ContextSession, session_id)
                if session_row is None:
                    raise ValueError(f"context session 不存在: {session_id}")
                world_id = session_row.world_id
        with _world_write_lock(self.url, world_id):
            with session_scope(self.url) as session:
                session_row = session.get(ContextSession, session_id)
                if session_row is None:
                    raise ValueError(f"context session 不存在: {session_id}")
                world_id = world_id or session_row.world_id
                self._lock_world(session, world_id)
                # Re-read under the lock with a forced refresh: the identity map
                # may hold a stale head from a previous read in this transaction.
                locked = session.get(
                    ContextSession, session_id, populate_existing=True, with_for_update=True
                )
                if locked is None or locked.status != "active":
                    raise ValueError(f"context session 已关闭或不存在: {session_id}")
                if locked.world_id != world_id:
                    raise ValueError("context session 不属于传入 world_id")
                sequence = int(locked.head_sequence) + 1
                normalised_refs: list[dict[str, Any]] = []
                if surface_op == "replace":
                    normalised_refs = _normalise_source_sequences(source_sequences, locked.id)
                    self._validate_checkpoint_sources(session, locked, normalised_refs, payload)
                event = ModelContextEvent(
                    id=new_id("cev"),
                    session_id=locked.id,
                    world_id=world_id,
                    root_world_id=root_world_id or locked.root_world_id,
                    turn_id=turn_id,
                    step=step,
                    sequence=sequence,
                    event_type=event_type,
                    source_kind=source_kind,
                    source_id=source_id,
                    source_version=source_version,
                    content_digest=payload_digest(payload),
                    audience=audience,
                    sensitivity=sensitivity,
                    surface_op=surface_op,
                    source_sequences=normalised_refs,
                    payload=dict(payload),
                )
                session.add(event)
                locked.head_sequence = sequence
                session.flush()
                return sequence

    def _lineage_session_ids(self, session, current: ContextSession) -> set[str]:
        """Current session id plus every ancestor session id (parent chain)."""
        ids: set[str] = {current.id}
        cursor_id = current.parent_session_id
        guard = 0
        while cursor_id is not None and guard < 64:
            row = session.get(ContextSession, cursor_id)
            if row is None:
                break
            ids.add(row.id)
            cursor_id = row.parent_session_id
            guard += 1
        return ids

    def _validate_checkpoint_sources(
        self,
        session,
        locked: ContextSession,
        refs: Sequence[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        """Fail-closed validation for a ``replace`` checkpoint append.

        A replace checkpoint must carry a valid ``replacement`` message (a
        checkpoint that only masks would silently drop history), must
        reference at least one source, and every source event must exist and
        belong to the current lineage (current session or an ancestor) so a
        bare/foreign reference can never mask an unrelated event.
        """
        if not isinstance((payload or {}).get("replacement"), dict):
            raise ValueError("replace checkpoint 必须包含 replacement message")
        if not refs:
            raise ValueError("replace checkpoint 必须至少引用一个 source")
        lineage = self._lineage_session_ids(session, locked)
        for ref in refs:
            ref_sid = str(ref.get("session_id") or "")
            ref_seq = int(ref.get("sequence") or 0)
            if ref_sid not in lineage:
                raise ValueError(f"checkpoint source 不属于当前 lineage: {ref_sid}")
            exists = (
                session.query(ModelContextEvent.id)
                .filter_by(session_id=ref_sid, sequence=ref_seq)
                .first()
            )
            if exists is None:
                raise ValueError(f"checkpoint source 事件不存在: {ref_sid}#{ref_seq}")

    def safe_append(
        self,
        session_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[int | None, str | None]:
        """Fail-safe append: never raises; returns (sequence, None) or
        (None, metadata-only error string).  The payload is never included in
        the error text or any log written by callers."""
        try:
            sequence = self.append(session_id, event_type=event_type, payload=payload, **kwargs)
            return sequence, None
        except Exception as exc:  # noqa: BLE001 - caller decides the policy
            return None, f"context_event_append_failed: {type(exc).__name__}"

    # -- legacy seed ------------------------------------------------------

    def seed_legacy(
        self,
        world_id: str,
        messages: Sequence[dict[str, Any]],
        *,
        root_world_id: str | None = None,
        source_kind: str = LEGACY_SAVE_KIND,
    ) -> int:
        """Import an old world/save exactly once, one event per message.

        Runs inside a single World-lock transaction: batch insert of the
        normalized messages plus the seed-digest marker commit atomically, so
        a concurrent seed can never double-append.  Idempotent on the same
        digest; a different digest fails closed with ``ValueError``.
        """
        digest = messages_digest(messages)
        normalized = self._normalize_messages(messages)
        with _world_write_lock(self.url, world_id):
            with session_scope(self.url) as session:
                self._lock_world(session, world_id)
                active = self._active_session(session, world_id)
                if active is None:
                    active = self._create_session_locked(
                        session, world_id, root_world_id=root_world_id
                    )
                if active.seed_digest:
                    if active.seed_digest == digest:
                        return int(active.head_sequence)
                    raise ValueError("context session 已由不同的历史 seed，拒绝覆盖")
                base = int(active.head_sequence)
                rows: list[ModelContextEvent] = []
                for index, message in enumerate(normalized):
                    event_type = infer_event_type(message)
                    rows.append(
                        ModelContextEvent(
                            id=new_id("cev"),
                            session_id=active.id,
                            world_id=world_id,
                            root_world_id=root_world_id or active.root_world_id,
                            turn_id=None,
                            step=None,
                            sequence=base + index + 1,
                            event_type=event_type,
                            source_kind=source_kind,
                            source_id="legacy-save",
                            source_version="1",
                            content_digest=payload_digest(message),
                            audience="model_private",
                            sensitivity="private"
                            if event_type in {EVENT_TOOL_CALL, EVENT_TOOL_RESULT}
                            else "public",
                            surface_op="append",
                            source_sequences=[],
                            payload=dict(message),
                        )
                    )
                session.add_all(rows)
                active.head_sequence = base + len(rows)
                active.seed_digest = digest
                session.flush()
                return int(active.head_sequence)

    # -- reading (never exposes payload) ----------------------------------

    def head_sequence(self, session_id: str) -> int:
        with session_scope(self.url) as session:
            row = session.get(ContextSession, session_id)
            return int(row.head_sequence) if row is not None else 0

    def event_metadata(
        self,
        session_id: str,
        *,
        limit: int = 200,
        after_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        """Metadata-only view for diagnostics/APIs: no payload, ever."""
        with session_scope(self.url) as session:
            query = session.query(ModelContextEvent).filter_by(session_id=session_id)
            if after_sequence is not None:
                query = query.filter(ModelContextEvent.sequence > after_sequence)
            rows = query.order_by(ModelContextEvent.sequence.asc()).limit(limit).all()
            return [
                {
                    "id": row.id,
                    "sequence": row.sequence,
                    "turn_id": row.turn_id,
                    "step": row.step,
                    "event_type": row.event_type,
                    "source_kind": row.source_kind,
                    "source_id": row.source_id,
                    "source_version": row.source_version,
                    "content_digest": row.content_digest,
                    "audience": row.audience,
                    "sensitivity": row.sensitivity,
                    "surface_op": row.surface_op,
                    "source_sequences": list(row.source_sequences or []),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

    def load_event_payloads(
        self,
        session_id: str,
        *,
        to_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        """Server-internal event dump for the pure projector (includes payload)."""
        with session_scope(self.url) as session:
            query = session.query(ModelContextEvent).filter_by(session_id=session_id)
            if to_sequence is not None:
                query = query.filter(ModelContextEvent.sequence <= to_sequence)
            rows = query.order_by(ModelContextEvent.sequence.asc()).all()
            return [
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "world_id": row.world_id,
                    "sequence": row.sequence,
                    "turn_id": row.turn_id,
                    "event_type": row.event_type,
                    "source_kind": row.source_kind,
                    "source_id": row.source_id,
                    "content_digest": row.content_digest,
                    "surface_op": row.surface_op,
                    "source_sequences": list(row.source_sequences or []),
                    "payload": dict(row.payload or {}),
                }
                for row in rows
            ]

    def _session_chain(self, session_id: str) -> list[dict[str, Any]]:
        """Ancestor→current session chain; raises on cycle / excessive depth."""
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor_id: str | None = session_id
        depth = 0
        while cursor_id is not None:
            if cursor_id in seen:
                raise ValueError("context session lineage 存在环")
            seen.add(cursor_id)
            depth += 1
            if depth > 32:
                raise ValueError("context session lineage 过深")
            with session_scope(self.url) as session:
                row = session.get(ContextSession, cursor_id)
            if row is None:
                raise ValueError(f"context session 缺失: {cursor_id}")
            chain.append(_session_dict(row))
            cursor_id = row.parent_session_id
        chain.reverse()
        return chain

    def _visible_turn_ids(
        self,
        event_turns: set[tuple[str, str]],
        include_turn_id: str | None,
    ) -> set[tuple[str, str]]:
        """(world_id, turn_id) pairs whose events may enter the projection.

        Only ``completed`` turns are visible by default; a failed, cancelled
        or interrupted turn's messages never leak into later surfaces.  The
        current *in-flight* (``active``) turn is visible only when explicitly
        requested via ``include_turn_id``; passing a failed/cancelled/
        interrupted turn id never grants visibility (fail closed).  Branch
        clones may reuse the same turn id in different worlds, so the key is
        the (world_id, turn_id) pair, never a bare turn id.
        """
        visible: set[tuple[str, str]] = set()
        if not event_turns and not include_turn_id:
            return visible
        turn_ids = {turn_id for _, turn_id in event_turns}
        if include_turn_id:
            turn_ids.add(include_turn_id)
        with session_scope(self.url) as session:
            rows = (
                session.query(Turn.world_id, Turn.id, Turn.status)
                .filter(Turn.id.in_(turn_ids))
                .all()
            )
        status_by_key = {(world_id, turn_id): status for world_id, turn_id, status in rows}
        for key in event_turns:
            if str(status_by_key.get(key, "")) == TURN_STATUS_COMMITTED:
                visible.add(key)
        if include_turn_id:
            # Resolve the world(s) for the include turn; only an in-flight
            # turn may be explicitly surfaced in its own world.
            for world_id in {w for w, turn_id in event_turns if turn_id == include_turn_id}:
                if str(status_by_key.get((world_id, include_turn_id), "")) == TURN_STATUS_ACTIVE:
                    visible.add((world_id, include_turn_id))
        return visible

    def project(
        self,
        session_id: str,
        *,
        include_turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Project the full visible surface for one session (server-internal).

        Message events from failed/cancelled/interrupted turns are excluded;
        the current active turn is included only when ``include_turn_id`` is
        given.  Non-surface events (request envelopes, checkpoints) never
        produce a message in the result.
        """
        sessions = self._session_chain(session_id)
        events_by_session: dict[str, list[dict[str, Any]]] = {}
        event_turns: set[tuple[str, str]] = set()
        for index, session_row in enumerate(sessions):
            if session_row["id"] == session_id:
                # Current session: full range.
                to_seq = None
            else:
                # Ancestor cutoff is the *next* generation's source_sequence
                # (where this session forks into the child), never this
                # session's own source_sequence (which is where *it* forked
                # from its parent).
                to_seq = int(sessions[index + 1]["source_sequence"])
            events = self.load_event_payloads(session_row["id"], to_sequence=to_seq)
            events_by_session[session_row["id"]] = events
            for event in events:
                turn_id = event.get("turn_id")
                if turn_id:
                    event_turns.add((str(event.get("world_id") or ""), str(turn_id)))
        visible_turns = self._visible_turn_ids(event_turns, include_turn_id)
        return ContextProjector.project_timeline(
            sessions, events_by_session, visible_turn_ids=visible_turns
        )

    # -- sync from existing messages (shadow mode) ------------------------

    def sync_messages(
        self,
        session_id: str,
        messages: Sequence[dict[str, Any]],
        *,
        turn_id: str | None = None,
        step: int | None = None,
        source_kind: str = TURN_KIND,
    ) -> tuple[str, list[int]]:
        """Append only when the current projection is a prefix of ``messages``.

        Returns ``("appended", [sequences])`` on success, or
        ``("mismatch", [])`` when the projection diverges from the actual
        message list (fail closed; no full-snapshot fallback is written).
        """
        projected = self.project(session_id)
        normalized = self._normalize_messages(messages)
        prefix_len = self._common_prefix_len(projected, normalized)
        if prefix_len < len(projected):
            return "mismatch", []
        if prefix_len == len(normalized):
            return "noop", []
        sequences: list[int] = []
        for message in normalized[prefix_len:]:
            event_type = infer_event_type(message)
            sequence = self.append(
                session_id,
                event_type=event_type,
                payload=dict(message),
                turn_id=turn_id,
                step=step,
                source_kind=source_kind,
                source_id="turn",
                source_version="1",
                audience="model_private",
                sensitivity="private"
                if event_type in {EVENT_TOOL_RESULT, EVENT_TOOL_CALL}
                else "public",
            )
            sequences.append(sequence)
        return "appended", sequences

    @staticmethod
    def _normalize_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        from .persistence import normalize_tool_message_history

        return normalize_tool_message_history([dict(m) for m in messages])

    @staticmethod
    def _common_prefix_len(left: list[dict], right: list[dict]) -> int:
        prefix = 0
        for left_item, right_item in zip(left, right, strict=False):
            if payload_digest(left_item) != payload_digest(right_item):
                break
            prefix += 1
        return prefix

    # -- request envelope (digest + section metadata only) ----------------

    def record_request_envelope(
        self,
        session_id: str,
        *,
        prepared: Any,
        turn_id: str | None = None,
    ) -> int | None:
        """Record one digest-only request envelope event; no prompt content."""
        envelope = getattr(prepared, "request_envelope", None)
        request_id = str((envelope or {}).get("request_id") or "")
        payload: dict[str, Any] = {
            "request_id": request_id,
            "profile": (envelope or {}).get("profile") or "",
            "step": (envelope or {}).get("step"),
            "model": (envelope or {}).get("model") or "",
            "message_digest": (envelope or {}).get("message_digest") or "",
            "tool_catalog_digest": (envelope or {}).get("tool_catalog_digest") or "",
            "context_section_digests": (envelope or {}).get("context_section_digests") or {},
            "sections": [
                {
                    "id": str(section.get("id") or ""),
                    "audience": str(section.get("audience") or ""),
                    "chars": int(section.get("chars") or 0),
                    "estimated_tokens": int(section.get("estimated_tokens") or 0),
                    "digest": str(section.get("digest") or ""),
                }
                for section in ((envelope or {}).get("sections") or [])
            ],
        }
        step = payload["step"]
        return self.append(
            session_id,
            event_type=EVENT_REQUEST_ENVELOPE,
            payload=payload,
            turn_id=turn_id,
            step=int(step) if isinstance(step, int) else None,
            source_kind="model_request",
            source_id=request_id,
            source_version="1",
            audience="model_private",
            sensitivity="private",
        )

    # -- shadow compare ---------------------------------------------------

    def shadow_compare(
        self,
        session_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compare projected vs current message digests; never mutates."""
        projected = self.project(session_id)
        projected_digest = messages_digest(projected)
        current_digest = messages_digest(messages)
        return {
            "projected_digest": projected_digest,
            "current_digest": current_digest,
            "match": projected_digest == current_digest,
            "projected_count": len(projected),
            "current_count": len(messages),
        }

    # -- reference-aware GC -----------------------------------------------

    def reference_aware_gc(self, world_id: str) -> dict[str, int]:
        """Delete only closed, childless, non-current sessions.

        Explicitly invoked; there is no automatic scheduled deletion in H2.
        Events are CASCADE-deleted with their session.  Returns a count of
        removed sessions and events (metadata only).
        """
        removed_sessions = 0
        removed_events = 0
        with _world_write_lock(self.url, world_id):
            with session_scope(self.url) as session:
                # Children reference their parent by id; gather all referenced
                # session ids first so we never delete a referenced ancestor.
                referenced: set[str] = {
                    row[0]
                    for row in session.query(ContextSession.parent_session_id)
                    .filter(ContextSession.parent_session_id.isnot(None))
                    .all()
                }
                current_ids = {
                    row[0]
                    for row in session.query(ContextSession.id).filter_by(status="active").all()
                }
                candidates = (
                    session.query(ContextSession)
                    .filter_by(world_id=world_id, status="closed")
                    .all()
                )
                for row in candidates:
                    if row.id in referenced:
                        continue
                    if row.id in current_ids:
                        continue
                    removed_events += (
                        session.query(ModelContextEvent).filter_by(session_id=row.id).delete()
                    )
                    session.delete(row)
                    removed_sessions += 1
                session.flush()
        return {"sessions": removed_sessions, "events": removed_events}


def _normalise_source_sequences(
    source_sequences: Sequence[int] | Sequence[dict[str, Any]] | None,
    current_session_id: str,
) -> list[dict[str, Any]]:
    """Normalize checkpoint source references to the structured form.

    A bare int is shorthand for ``this session's sequence`` and is persisted
    as ``{"session_id": <current>, "sequence": n}`` so a checkpoint can
    never accidentally mask an ancestor's same-numbered event and so
    diagnostics always show fully structured refs.  Every reference must
    have a positive sequence and a non-empty session id.
    """
    if not source_sequences:
        return []
    normalised: list[dict[str, Any]] = []
    for reference in source_sequences:
        if isinstance(reference, dict):
            session_id = str(reference.get("session_id") or "")
            sequence = int(reference.get("sequence") or 0)
            if not session_id:
                raise ValueError("checkpoint source 引用缺少 session_id")
        else:
            session_id = current_session_id
            sequence = int(reference)
        if sequence <= 0:
            raise ValueError(f"checkpoint source sequence 必须 > 0: {sequence}")
        normalised.append({"session_id": session_id, "sequence": sequence})
    return normalised


# --------------------------------------------------------------------------
# Pure projector: no DB access, no mutation of the event store.
# --------------------------------------------------------------------------


class ContextProjector:
    """Pure projection from ancestor→current sessions + their events.

    Callers (e.g. :meth:`ContextEventStore.project`) assemble the inputs; the
    projection itself is a pure function suitable for golden tests.
    """

    @staticmethod
    def project_timeline(
        sessions: Sequence[dict[str, Any]],
        events_by_session: dict[str, list[dict[str, Any]]],
        *,
        visible_turn_ids: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Project the model-visible message surface (item/ref model).

        Ancestor sessions contribute events up to their ``source_sequence``;
        the current session contributes everything.  ``replace`` checkpoints
        mask only the explicit source references — each reference is pinned
        to ``(session_id, sequence)`` so a checkpoint can never mask an
        ancestor session's same-numbered event; raw events stay in the log.
        Sibling branches are isolated because they are never part of the
        parent chain.

        A ``replace`` checkpoint behaves like an *item + ref* rewrite: it
        must match **every** referenced source, inserts its ``replacement``
        message at the earliest matched position, masks all matched
        positions, and registers its own ``(session_id, sequence)`` at that
        position so a later checkpoint can reference this one (nested
        replacement).  If any source is missing the checkpoint fails closed:
        nothing is masked and the replacement is never appended out of band.

        Non-surface events (request envelopes) never produce a message; a
        checkpoint never emits its own bare payload.  ``visible_turn_ids``
        is a strict ``(world_id, turn_id)`` set: message events whose pair is
        not in the set are dropped (failed/cancelled/interrupted turns) —
        there is no bare-turn-id fallback, so a branch clone reusing a turn
        id in another world can never leak.  Events without a turn id (seeds)
        are always kept.  ``None`` keeps everything (pure-function testing
        convenience; the store always passes a set).
        """
        if not sessions:
            return []
        result: list[dict[str, Any]] = []
        # (session_id, sequence) → result index: every projected message and
        # every checkpoint (at the position its replacement landed) is
        # addressable, so checkpoints can reference earlier checkpoints.
        seq_to_index: dict[tuple[str, int], int] = {}
        masked_indexes: set[int] = set()
        # position → replacement message: a checkpoint with a ``replacement``
        # payload projects that message in place of the earliest matched
        # source.  A later checkpoint referencing this checkpoint overwrites
        # the same position (nested replacement).
        replacements: dict[int, dict[str, Any]] = {}

        for session_row in sessions:
            session_id = str(session_row.get("id") or "")
            events = events_by_session.get(session_id) or []
            for event in events:
                event_type = str(event.get("event_type") or "")
                sequence = int(event.get("sequence") or 0)
                op = str(event.get("surface_op") or "append")
                if op == "replace":
                    matched: list[int] = []
                    for reference in event.get("source_sequences") or []:
                        ref_session, ref_seq = _resolve_source_reference(reference, session_id)
                        index = seq_to_index.get((ref_session, ref_seq))
                        if index is None:
                            # Fail closed: a missing source invalidates the
                            # whole checkpoint — mask nothing, append nothing.
                            matched = []
                            break
                        matched.append(index)
                    if not matched:
                        continue
                    replacement = (event.get("payload") or {}).get("replacement")
                    if not isinstance(replacement, dict):
                        continue
                    position = min(matched)
                    masked_indexes.update(matched)
                    replacements[position] = dict(replacement)
                    # The checkpoint itself becomes addressable at the
                    # position its replacement landed, so a later checkpoint
                    # can reference it (nested replacement).
                    seq_to_index[(session_id, sequence)] = position
                    continue
                if event_type not in SURFACE_EVENT_TYPES:
                    # Metadata-only events (request envelopes, …) never
                    # produce a message in the projection.
                    continue
                if visible_turn_ids is not None:
                    turn_id = event.get("turn_id")
                    if turn_id:
                        world_id = str(event.get("world_id") or "")
                        if (world_id, str(turn_id)) not in visible_turn_ids:
                            continue
                seq_to_index[(session_id, sequence)] = len(result)
                result.append(dict(event.get("payload") or {}))
        final: list[dict[str, Any]] = []
        for index, message in enumerate(result):
            if index in replacements:
                final.append(replacements[index])
            elif index not in masked_indexes:
                final.append(message)
        return final

    @staticmethod
    def digest(messages: Sequence[dict[str, Any]]) -> str:
        return messages_digest(messages)


def _resolve_source_reference(reference: Any, current_session_id: str) -> tuple[str, int]:
    """Resolve one checkpoint source reference to (session_id, sequence)."""
    if isinstance(reference, dict):
        session_id = str(reference.get("session_id") or current_session_id)
        sequence = int(reference.get("sequence") or 0)
        return session_id, sequence
    return current_session_id, int(reference)
