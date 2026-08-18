"""Add model-context event timeline (context_sessions + model_context_events).

H2 append-only context events: every world gets one active context session
(partial unique index, mirroring the single-active-turn invariant), events are
immutable appends on a per-session sequence, and ``begin_epoch`` closes the
current session so a resumed old save opens a new epoch whose parent points at
the old session (parent FK is RESTRICT so reference-aware GC stays explicit).

Fresh-vs-adopt contract (mirrors 0007's single-active-turn adoption):

- Neither table exists: create both (normal fresh upgrade path).
- Both tables already exist (``Base.metadata.create_all`` desktop builds):
  strictly validate the expected columns, unique constraints and the partial
  active index, then no-op — never rewrite an already-created shape.
- Only one table exists, or the shapes / same-named indexes do not match:
  fail closed; do not silently invent a schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260818_0008"
down_revision = "20260811_0007"
branch_labels = None
depends_on = None

SESSIONS = "context_sessions"
EVENTS = "model_context_events"
INDEX = "uq_context_sessions_one_active_per_world"

SESSIONS_COLUMNS = (
    "id",
    "world_id",
    "root_world_id",
    "session_epoch",
    "parent_session_id",
    "parent_world_id",
    "source_sequence",
    "head_sequence",
    "status",
    "seed_digest",
    "created_at",
    "closed_at",
)
EVENTS_COLUMNS = (
    "id",
    "session_id",
    "world_id",
    "root_world_id",
    "turn_id",
    "step",
    "sequence",
    "event_type",
    "source_kind",
    "source_id",
    "source_version",
    "content_digest",
    "audience",
    "sensitivity",
    "surface_op",
    "source_sequences",
    "payload",
    "created_at",
)
SESSIONS_UNIQUE = ("uq_context_session_world_epoch",)
EVENTS_UNIQUE = ("uq_context_event_sequence",)


def _index_named(bind, table: str, name: str) -> dict | None:
    return next(
        (index for index in sa.inspect(bind).get_indexes(table) if index.get("name") == name),
        None,
    )


def _normalise_predicate(value: object) -> str:
    predicate = str(value if value is not None else "").lower().replace('"', "")
    return (
        predicate.replace("::character varying", "")
        .replace("::varchar", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
    )


def _is_active_index(bind, index: dict) -> bool:
    if not index.get("unique") or index.get("column_names") != ["world_id"]:
        return False
    options = index.get("dialect_options") or {}
    predicate = options.get(f"{bind.dialect.name}_where")
    return _normalise_predicate(predicate) == "status='active'"


def _validate_adopt_shape(
    bind, table: str, expected_columns, unique_names: tuple[str, ...]
) -> None:
    """Fail closed unless ``table`` has exactly the expected shape."""
    inspector = sa.inspect(bind)
    actual_columns = {column["name"] for column in inspector.get_columns(table)}
    missing = sorted(set(expected_columns) - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - set(expected_columns))
    if extra:
        raise RuntimeError(f"无法接管已存在的 {table}：存在未知列 {', '.join(extra)}")
    actual_unique = {u["name"] for u in inspector.get_unique_constraints(table)}
    missing_unique = sorted(set(unique_names) - actual_unique)
    if missing_unique:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少唯一约束 {', '.join(missing_unique)}")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    has_sessions = SESSIONS in tables
    has_events = EVENTS in tables

    if has_sessions != has_events:
        raise RuntimeError("无法接管未版本化数据库：context 事件表只存在一部分")

    if has_sessions:
        # ``Base.metadata.create_all`` desktop builds may already carry the
        # exact tables/constraints.  Validate strictly, then no-op.
        _validate_adopt_shape(bind, SESSIONS, SESSIONS_COLUMNS, SESSIONS_UNIQUE)
        _validate_adopt_shape(bind, EVENTS, EVENTS_COLUMNS, EVENTS_UNIQUE)
        existing = _index_named(bind, SESSIONS, INDEX)
        if existing is None:
            raise RuntimeError(f"无法接管已存在的 {SESSIONS}：缺少索引 {INDEX}")
        if _is_active_index(bind, existing):
            return
        raise RuntimeError(f"索引 {INDEX} 已存在且形状不符；请由维护者核对后再标记/重试迁移。")

    op.create_table(
        SESSIONS,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("root_world_id", sa.String(160), nullable=False),
        sa.Column("session_epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "parent_session_id",
            sa.String(48),
            sa.ForeignKey(SESSIONS + ".id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("parent_world_id", sa.String(160), nullable=True),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("head_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("seed_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("world_id", "session_epoch", name="uq_context_session_world_epoch"),
    )
    op.create_index("ix_context_sessions_world_id", SESSIONS, ["world_id"])
    op.create_index("ix_context_sessions_root_world_id", SESSIONS, ["root_world_id"])
    op.create_index("ix_context_sessions_parent_session_id", SESSIONS, ["parent_session_id"])
    op.create_index("ix_context_sessions_status", SESSIONS, ["status"])
    op.create_index(
        INDEX,
        SESSIONS,
        ["world_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        EVENTS,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(48),
            sa.ForeignKey(SESSIONS + ".id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("world_id", sa.String(160), nullable=False),
        sa.Column("root_world_id", sa.String(160), nullable=False),
        sa.Column("turn_id", sa.String(80), nullable=True),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("source_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False, server_default="model_private"),
        sa.Column("sensitivity", sa.String(32), nullable=False, server_default="private"),
        sa.Column("surface_op", sa.String(16), nullable=False, server_default="append"),
        sa.Column(
            "source_sequences",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_context_event_sequence"),
    )
    op.create_index("ix_model_context_events_session_id", EVENTS, ["session_id"])
    op.create_index("ix_model_context_events_world_id", EVENTS, ["world_id"])
    op.create_index("ix_model_context_events_root_world_id", EVENTS, ["root_world_id"])
    op.create_index("ix_model_context_events_turn_id", EVENTS, ["turn_id"])
    op.create_index("ix_model_context_events_event_type", EVENTS, ["event_type"])
    op.create_index("ix_model_context_events_content_digest", EVENTS, ["content_digest"])


def downgrade() -> None:
    op.drop_table(EVENTS)
    op.drop_table(SESSIONS)
