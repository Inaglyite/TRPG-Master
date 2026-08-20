"""Database engine, ORM models, and transaction boundary.

PostgreSQL is the production database.  SQLite is retained as an embedded
desktop/test backend; both use the same repositories and schema.
"""

from __future__ import annotations

import os
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.types import JSON

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")
ACTIVE_TURN_WORLD_INDEX = "uq_turns_one_active_per_world"
MEMORY_FACT_CURRENT_INDEX = "uq_memory_facts_current_per_subject_type"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user: Mapped[User] = relationship()


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    module_name: Mapped[str] = mapped_column(String(160), index=True)
    module_id: Mapped[str] = mapped_column(String(160), default="")
    module_version: Mapped[str] = mapped_column(String(80), default="")
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    # Denormalized timeline root: the world whose ancestor chain contains this
    # world.  Root worlds reference themselves; branches point at their tree's
    # root.  Backfilled from ``metadata_json["branch"]["parent_world_id"]``.
    # ``server_default`` (not a Python-side default) keeps ORM inserts working
    # on a fresh create_all schema without naming the column explicitly.
    root_world_id: Mapped[str] = mapped_column(
        String(160), nullable=False, server_default="", index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldMember(Base):
    __tablename__ = "world_members"
    __table_args__ = (UniqueConstraint("world_id", "user_id", name="uq_world_member"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="player")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldInvite(Base):
    __tablename__ = "world_invites"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    invited_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="player")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldInvestigator(Base):
    __tablename__ = "world_investigators"
    __table_args__ = (
        UniqueConstraint("world_id", "character_key", name="uq_world_character_key"),
        UniqueConstraint("world_id", "controller_user_id", name="uq_world_controller"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    character_key: Mapped[str] = mapped_column(String(200))
    character_ref: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    controller_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoomAction(Base):
    __tablename__ = "room_actions"
    __table_args__ = (UniqueConstraint("world_id", "action_id", name="uq_room_action_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[str] = mapped_column(String(160))
    submitted_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldState(Base):
    __tablename__ = "world_states"

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    source_turn_id: Mapped[str | None] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="save")
    revision: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        Index("ix_turns_world_completed", "world_id", "completed_at"),
        # A World row lock serializes normal PostgreSQL writes, but SQLite
        # ignores ``FOR UPDATE`` and a second backend can still race between
        # the application-level read and insert.  The partial unique index is
        # the final authority on both backends: a world may have many finished
        # turns, but never more than one active one.
        Index(
            ACTIVE_TURN_WORLD_INDEX,
            "world_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        UniqueConstraint("world_id", "id", name="uq_world_turn"),
    )

    pk: Mapped[str] = mapped_column(String(48), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), index=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    parent_turn_id: Mapped[str | None] = mapped_column(String(80), index=True)
    origin_world_id: Mapped[str | None] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40), default="action")
    status: Mapped[str] = mapped_column(String(20), index=True)
    owner_token: Mapped[str] = mapped_column(String(80), default="")
    player_input: Mapped[str | None] = mapped_column(Text)
    record: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("snapshots.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class TurnEvent(Base):
    __tablename__ = "turn_events"
    __table_args__ = (UniqueConstraint("turn_pk", "sequence", name="uq_turn_event_sequence"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    turn_pk: Mapped[str] = mapped_column(ForeignKey("turns.pk", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str] = mapped_column(String(80), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelCall(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    turn_pk: Mapped[str] = mapped_column(ForeignKey("turns.pk", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(160), default="", index=True)
    prompt_profile: Mapped[str] = mapped_column(String(40), default="")
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SaveSlot(Base):
    __tablename__ = "save_slots"
    __table_args__ = (UniqueConstraint("world_id", "slot_key", name="uq_world_save_slot"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    slot_key: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20), default="manual")
    label: Mapped[str] = mapped_column(String(200), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshots.id", ondelete="RESTRICT"), index=True
    )
    world_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerNote(Base):
    __tablename__ = "player_notes"
    __table_args__ = (UniqueConstraint("world_id", "owner_key", name="uq_world_player_note"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_key: Mapped[str] = mapped_column(String(48), default="__local__")
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    world_id: Mapped[str | None] = mapped_column(String(160), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


ACTIVE_CONTEXT_SESSION_INDEX = "uq_context_sessions_one_active_per_world"


class ContextSession(Base):
    """One append-only model-context timeline for a world.

    A world normally keeps one active session; ``begin_epoch`` closes it and
    opens a new epoch whose parent points at the old session, so resuming from
    an old save can never see events written after that save's cutoff.  The
    partial unique index (mirroring the single-active-turn invariant) is the
    final authority on both backends.
    """

    __tablename__ = "context_sessions"
    __table_args__ = (
        UniqueConstraint("world_id", "session_epoch", name="uq_context_session_world_epoch"),
        Index(
            ACTIVE_CONTEXT_SESSION_INDEX,
            "world_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    root_world_id: Mapped[str] = mapped_column(String(160), index=True)
    session_epoch: Mapped[int] = mapped_column(BigInteger)
    parent_session_id: Mapped[str | None] = mapped_column(
        # RESTRICT: never delete an ancestor while a descendant still points
        # at it.  The reference-aware GC only deletes sessions with no
        # remaining children.
        ForeignKey("context_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    parent_world_id: Mapped[str | None] = mapped_column(String(160))
    source_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    head_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    seed_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelContextEvent(Base):
    """One immutable event on a model-context timeline.

    ``payload`` holds the model-visible message for that event; it is never
    returned by ordinary APIs or diagnostics.  A ``replace`` checkpoint masks
    the explicit ``source_sequences`` in the projection without deleting the
    raw events.
    """

    __tablename__ = "model_context_events"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_context_event_sequence"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("context_sessions.id", ondelete="CASCADE"), index=True
    )
    world_id: Mapped[str] = mapped_column(String(160), index=True)
    root_world_id: Mapped[str] = mapped_column(String(160), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(80), index=True)
    step: Mapped[int | None] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    source_kind: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(200), default="")
    source_version: Mapped[str] = mapped_column(String(80), default="")
    content_digest: Mapped[str] = mapped_column(String(64), index=True)
    audience: Mapped[str] = mapped_column(String(32), default="model_private")
    sensitivity: Mapped[str] = mapped_column(String(32), default="private")
    surface_op: Mapped[str] = mapped_column(String(16), default="append")
    source_sequences: Mapped[list[Any]] = mapped_column(JSON_VALUE, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldSkillPin(Base):
    """Frozen per-world Skill content snapshot (H3 world freeze).

    Pins are written exactly once per world (the first time its catalog is
    resolved) and never updated afterwards: a running world never hot-reloads
    edited skill files, and ``reset`` does not re-pin.  Branches inherit the
    source world's pins by copy at branch-creation time.
    """

    __tablename__ = "world_skill_pins"
    __table_args__ = (UniqueConstraint("world_id", "skill_id", name="uq_world_skill_pin"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[str] = mapped_column(String(120))
    skill_version: Mapped[str] = mapped_column(String(80), default="")
    content_digest: Mapped[str] = mapped_column(String(80))
    trust: Mapped[str] = mapped_column(String(32), default="core")
    residency: Mapped[str] = mapped_column(String(32), default="core")
    content: Mapped[str] = mapped_column(Text)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldSkillPinManifest(Base):
    """Frozen SkillEntry manifest metadata for one pin (1:1 sidecar).

    Kept in its own table so ``world_skill_pins`` (0009) stays immutable.
    ``entry_snapshot`` holds the full ``SkillEntry.model_dump`` plus an
    ``"order"`` key (catalog position at pin time); an existing world's
    core/opening/on_demand/activation behavior is governed by this snapshot
    only, never by the current on-disk catalog.  Rows are immutable.
    """

    __tablename__ = "world_skill_pin_manifests"

    pin_id: Mapped[str] = mapped_column(
        ForeignKey("world_skill_pins.id", ondelete="CASCADE"), primary_key=True
    )
    entry_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)


class MemoryFactCandidate(Base):
    """A proposed structured fact awaiting trusted-engine acceptance.

    The model / engine may only propose here; nothing in this table is
    authoritative.  Acceptance moves the fact to :class:`MemoryFact` via an
    explicit ``source_turn_id`` + ``provenance`` handshake.
    """

    __tablename__ = "memory_fact_candidates"
    __table_args__ = (
        UniqueConstraint(
            "world_id",
            "source_turn_id",
            "subject_id",
            "fact_type",
            "digest",
            name="uq_memory_candidate_dedupe",
        ),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    root_world_id: Mapped[str] = mapped_column(String(160), index=True)
    source_turn_id: Mapped[str] = mapped_column(String(80), index=True)
    subject_id: Mapped[str] = mapped_column(String(200), index=True)
    subject_kind: Mapped[str] = mapped_column(String(32), default="npc")
    fact_type: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSON_VALUE, default=dict)
    digest: Mapped[str] = mapped_column(String(64))
    audience: Mapped[str] = mapped_column(String(32), default="public")
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    tier: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[list[Any]] = mapped_column(JSON_VALUE, default=list)
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryFact(Base):
    """An accepted structured fact (authoritative memory).

    Only the trusted engine may write here, via an explicit
    ``source_turn_id`` + ``provenance`` handshake.  The partial unique index
    enforces at most one *current* fact per (world, subject, fact_type); a
    conflicting re-accept supersedes the previous fact (``revision``+1,
    ``supersedes_id`` chain) rather than silently overwriting.
    """

    __tablename__ = "memory_facts"
    __table_args__ = (
        Index(
            MEMORY_FACT_CURRENT_INDEX,
            "world_id",
            "subject_id",
            "fact_type",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
        UniqueConstraint(
            "world_id",
            "subject_id",
            "fact_type",
            "digest",
            name="uq_memory_fact_digest",
        ),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"), index=True)
    root_world_id: Mapped[str] = mapped_column(String(160), index=True)
    source_turn_id: Mapped[str] = mapped_column(String(80), index=True)
    subject_id: Mapped[str] = mapped_column(String(200), index=True)
    subject_kind: Mapped[str] = mapped_column(String(32), default="npc")
    fact_type: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSON_VALUE, default=dict)
    digest: Mapped[str] = mapped_column(String(64))
    audience: Mapped[str] = mapped_column(String(32), default="public")
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    tier: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[list[Any]] = mapped_column(JSON_VALUE, default=list)
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_facts.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="accepted", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_ENGINES: dict[str, Engine] = {}
_ENGINE_LOCK = threading.Lock()


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def database_url(runtime_root: Path | None = None) -> str:
    configured = os.environ.get("TRPG_DATABASE_URL", "").strip()
    if configured:
        return configured
    root = Path(runtime_root or os.environ.get("TRPG_RUNTIME_ROOT") or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{root / 'trpg-master.db'}"


def get_engine(url: str) -> Engine:
    with _ENGINE_LOCK:
        engine = _ENGINES.get(url)
        if engine is None:
            kwargs: dict[str, Any] = {"pool_pre_ping": True}
            if url.startswith("sqlite:"):
                kwargs["connect_args"] = {"check_same_thread": False}
            elif url.startswith("postgresql"):
                # The small Azure host runs production and staging beside the
                # database. Bound every process so workers, migrations and
                # backups retain connection headroom below PostgreSQL's limit.
                kwargs.update(
                    pool_size=_bounded_env_int("TRPG_DB_POOL_SIZE", 3, 1, 10),
                    max_overflow=_bounded_env_int("TRPG_DB_MAX_OVERFLOW", 2, 0, 10),
                    pool_timeout=_bounded_env_int("TRPG_DB_POOL_TIMEOUT", 10, 1, 60),
                    pool_recycle=1800,
                )
            engine = create_engine(url, **kwargs)
            if url.startswith("sqlite:"):
                # SQLite parses foreign keys but does not enforce them unless
                # every connection explicitly enables the pragma. Desktop and
                # test storage must keep the same CASCADE/RESTRICT guarantees
                # as PostgreSQL rather than silently accepting orphan rows.
                @event.listens_for(engine, "connect")
                def _enable_sqlite_foreign_keys(
                    dbapi_connection,
                    _connection_record,
                ) -> None:
                    cursor = dbapi_connection.cursor()
                    try:
                        cursor.execute("PRAGMA foreign_keys=ON")
                    finally:
                        cursor.close()

            _ENGINES[url] = engine
        return engine


def initialize_database(url: str) -> Engine:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(url: str) -> Iterator[Session]:
    engine = get_engine(url)
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
