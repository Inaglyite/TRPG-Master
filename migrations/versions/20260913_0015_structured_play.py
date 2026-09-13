"""Structured play platform tables (protocol v1, M1).

Adds the five tables frozen in docs/STRUCTURED_PLAY_PROTOCOL_V1.md §8 —
``player_requests``、``game_commands``、``check_requests``、``event_outbox``、
``keeper_control`` — plus ``world_members.can_keeper`` (keeper capability is a
separate grant from the room-management role).

All tables follow the adopt-or-create contract used since 0008: desktop builds
create them via ``Base.metadata.create_all``; the migration validates the exact
shape and no-ops, and fails closed on any mismatch.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260913_0015"
down_revision = "20260909_0014"
branch_labels = None
depends_on = None

TABLES = (
    "player_requests",
    "game_commands",
    "check_requests",
    "event_outbox",
    "keeper_control",
)

EXPECTED_COLUMNS = {
    "player_requests": (
        "id",
        "world_id",
        "request_id",
        "request_type",
        "investigator_id",
        "submitted_by",
        "payload",
        "payload_digest",
        "status",
        "outcome",
        "detail",
        "created_at",
        "updated_at",
    ),
    "game_commands": (
        "id",
        "world_id",
        "command_id",
        "kind",
        "payload",
        "payload_digest",
        "principal",
        "controller_epoch",
        "status",
        "result",
        "error_code",
        "cause_id",
        "revision",
        "created_at",
    ),
    "check_requests": (
        "id",
        "world_id",
        "check_request_id",
        "investigator_id",
        "skill",
        "difficulty",
        "bonus_penalty",
        "attempt",
        "known_cost",
        "visibility",
        "status",
        "conditions",
        "result",
        "related_request_id",
        "rule_version",
        "created_by",
        "created_at",
        "resolved_at",
        "updated_at",
    ),
    "event_outbox": (
        "id",
        "world_id",
        "sequence",
        "revision",
        "event_type",
        "payload",
        "audience",
        "cause_request_id",
        "published_at",
        "created_at",
    ),
    "keeper_control": (
        "world_id",
        "controller_kind",
        "controller_id",
        "epoch",
        "updated_at",
    ),
}

EXPECTED_UNIQUE = {
    "player_requests": ("uq_player_request_id",),
    "game_commands": ("uq_game_command_id",),
    "check_requests": ("uq_check_request_id",),
    "event_outbox": ("uq_event_outbox_sequence",),
    "keeper_control": (),
}


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _validate_adopt_shape(bind, table: str) -> None:
    """Fail closed unless ``table`` has exactly the expected shape."""
    inspector = sa.inspect(bind)
    actual_columns = {column["name"] for column in inspector.get_columns(table)}
    expected = set(EXPECTED_COLUMNS[table])
    missing = sorted(expected - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - expected)
    if extra:
        raise RuntimeError(f"无法接管已存在的 {table}：存在未知列 {', '.join(extra)}")
    actual_unique = {u["name"] for u in inspector.get_unique_constraints(table)}
    missing_unique = sorted(set(EXPECTED_UNIQUE[table]) - actual_unique)
    if missing_unique:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少唯一约束 {', '.join(missing_unique)}")


def _create_player_requests() -> None:
    op.create_table(
        "player_requests",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("request_type", sa.String(32), nullable=False),
        sa.Column("investigator_id", sa.String(160), nullable=False, server_default=""),
        sa.Column(
            "submitted_by",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("outcome", sa.String(20), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "request_id", name="uq_player_request_id"),
    )
    op.create_index("ix_player_requests_world_id", "player_requests", ["world_id"])
    op.create_index("ix_player_requests_investigator_id", "player_requests", ["investigator_id"])
    op.create_index("ix_player_requests_submitted_by", "player_requests", ["submitted_by"])
    op.create_index("ix_player_requests_status", "player_requests", ["status"])


def _create_game_commands() -> None:
    op.create_table(
        "game_commands",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("principal", _json(), nullable=False),
        sa.Column("controller_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="committed"),
        sa.Column("result", _json(), nullable=False),
        sa.Column("error_code", sa.String(60), nullable=False, server_default=""),
        sa.Column("cause_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "command_id", name="uq_game_command_id"),
    )
    op.create_index("ix_game_commands_world_id", "game_commands", ["world_id"])
    op.create_index("ix_game_commands_kind", "game_commands", ["kind"])
    op.create_index("ix_game_commands_cause_id", "game_commands", ["cause_id"])


def _create_check_requests() -> None:
    op.create_table(
        "check_requests",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_request_id", sa.String(160), nullable=False),
        sa.Column("investigator_id", sa.String(160), nullable=False),
        sa.Column("skill", sa.String(60), nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False, server_default="regular"),
        sa.Column("bonus_penalty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Text(), nullable=False, server_default=""),
        sa.Column("known_cost", sa.Text(), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="public"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("conditions", _json(), nullable=False),
        sa.Column("result", _json(), nullable=False),
        sa.Column("related_request_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("rule_version", sa.String(40), nullable=False, server_default="coc7"),
        sa.Column(
            "created_by",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "check_request_id", name="uq_check_request_id"),
    )
    op.create_index("ix_check_requests_world_id", "check_requests", ["world_id"])
    op.create_index("ix_check_requests_investigator_id", "check_requests", ["investigator_id"])
    op.create_index("ix_check_requests_status", "check_requests", ["status"])


def _create_event_outbox() -> None:
    op.create_table(
        "event_outbox",
        # SQLite 仅 INTEGER PRIMARY KEY 才是 rowid 别名（自增）；Postgres 用 BIGINT。
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("audience", _json(), nullable=False),
        sa.Column("cause_request_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "sequence", name="uq_event_outbox_sequence"),
    )
    op.create_index("ix_event_outbox_world_id", "event_outbox", ["world_id"])
    op.create_index("ix_event_outbox_event_type", "event_outbox", ["event_type"])
    op.create_index("ix_event_outbox_cause_request_id", "event_outbox", ["cause_request_id"])


def _create_keeper_control() -> None:
    op.create_table(
        "keeper_control",
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("controller_kind", sa.String(20), nullable=False, server_default="none"),
        sa.Column("controller_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


_CREATORS = {
    "player_requests": _create_player_requests,
    "game_commands": _create_game_commands,
    "check_requests": _create_check_requests,
    "event_outbox": _create_event_outbox,
    "keeper_control": _create_keeper_control,
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    member_columns = {column["name"] for column in inspector.get_columns("world_members")}
    if "can_keeper" not in member_columns:
        op.add_column(
            "world_members",
            sa.Column(
                "can_keeper",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    tables = set(inspector.get_table_names())
    for table in TABLES:
        if table in tables:
            _validate_adopt_shape(bind, table)
        else:
            _CREATORS[table]()


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("world_members", "can_keeper")
