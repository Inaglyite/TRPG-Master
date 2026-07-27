"""Initial PostgreSQL control plane and JSON gameplay data plane.

This revision deliberately declares its schema instead of importing the live
ORM metadata.  Alembic revisions are historical artifacts: importing ``Base``
would silently make this same revision create future tables and would make an
old production database impossible to reproduce or test reliably.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260722_0001"
down_revision = None
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_status", "users", ["status"], unique=False)

    op.create_table(
        "worlds",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("module_name", sa.String(length=160), nullable=False),
        sa.Column("module_id", sa.String(length=160), nullable=False),
        sa.Column("module_version", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("metadata_json", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worlds_module_name", "worlds", ["module_name"], unique=False)
    op.create_index("ix_worlds_created_by", "worlds", ["created_by"], unique=False)
    op.create_index("ix_worlds_status", "worlds", ["status"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("user_id", sa.String(length=48), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)

    op.create_table(
        "world_members",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=48), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("world_id", "user_id", name="uq_world_member"),
    )
    op.create_index("ix_world_members_world_id", "world_members", ["world_id"], unique=False)
    op.create_index("ix_world_members_user_id", "world_members", ["user_id"], unique=False)

    op.create_table(
        "world_states",
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("state", JSON_VALUE, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("world_id"),
    )

    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("source_turn_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("state", JSON_VALUE, nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snapshots_world_id", "snapshots", ["world_id"], unique=False)
    op.create_index(
        "ix_snapshots_source_turn_id", "snapshots", ["source_turn_id"], unique=False
    )

    op.create_table(
        "turns",
        sa.Column("pk", sa.String(length=48), nullable=False),
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("parent_turn_id", sa.String(length=80), nullable=True),
        sa.Column("origin_world_id", sa.String(length=160), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("owner_token", sa.String(length=80), nullable=False),
        sa.Column("player_input", sa.Text(), nullable=True),
        sa.Column("record", JSON_VALUE, nullable=False),
        sa.Column("messages", JSON_VALUE, nullable=False),
        sa.Column("snapshot_id", sa.String(length=48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pk"),
        sa.UniqueConstraint("world_id", "id", name="uq_world_turn"),
    )
    op.create_index("ix_turns_id", "turns", ["id"], unique=False)
    op.create_index("ix_turns_world_id", "turns", ["world_id"], unique=False)
    op.create_index(
        "ix_turns_parent_turn_id", "turns", ["parent_turn_id"], unique=False
    )
    op.create_index("ix_turns_status", "turns", ["status"], unique=False)
    op.create_index("ix_turns_snapshot_id", "turns", ["snapshot_id"], unique=False)
    op.create_index("ix_turns_completed_at", "turns", ["completed_at"], unique=False)
    op.create_index(
        "ix_turns_world_completed",
        "turns",
        ["world_id", "completed_at"],
        unique=False,
    )

    op.create_table(
        "turn_events",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("turn_pk", sa.String(length=48), nullable=False),
        sa.Column("turn_id", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["turn_pk"], ["turns.pk"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_pk", "sequence", name="uq_turn_event_sequence"),
    )
    op.create_index("ix_turn_events_turn_pk", "turn_events", ["turn_pk"], unique=False)
    op.create_index("ix_turn_events_turn_id", "turn_events", ["turn_id"], unique=False)
    op.create_index(
        "ix_turn_events_event_type", "turn_events", ["event_type"], unique=False
    )

    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("turn_pk", sa.String(length=48), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("prompt_profile", sa.String(length=40), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("details", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["turn_pk"], ["turns.pk"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_calls_turn_pk", "model_calls", ["turn_pk"], unique=False)
    op.create_index("ix_model_calls_model", "model_calls", ["model"], unique=False)

    op.create_table(
        "save_slots",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("slot_key", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("metadata_json", JSON_VALUE, nullable=False),
        sa.Column("messages", JSON_VALUE, nullable=False),
        sa.Column("snapshot_id", sa.String(length=48), nullable=False),
        sa.Column("world_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("world_id", "slot_key", name="uq_world_save_slot"),
    )
    op.create_index("ix_save_slots_world_id", "save_slots", ["world_id"], unique=False)
    op.create_index(
        "ix_save_slots_snapshot_id", "save_slots", ["snapshot_id"], unique=False
    )

    op.create_table(
        "player_notes",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=48), nullable=True),
        sa.Column("owner_key", sa.String(length=48), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("world_id", "owner_key", name="uq_world_player_note"),
    )
    op.create_index("ix_player_notes_world_id", "player_notes", ["world_id"], unique=False)
    op.create_index("ix_player_notes_user_id", "player_notes", ["user_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("user_id", sa.String(length=48), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("world_id", sa.String(length=160), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("details", JSON_VALUE, nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"], unique=False)
    op.create_index(
        "ix_audit_events_event_type", "audit_events", ["event_type"], unique=False
    )
    op.create_index("ix_audit_events_world_id", "audit_events", ["world_id"], unique=False)
    op.create_index(
        "ix_audit_events_created_at", "audit_events", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("player_notes")
    op.drop_table("save_slots")
    op.drop_table("model_calls")
    op.drop_table("turn_events")
    op.drop_table("turns")
    op.drop_table("snapshots")
    op.drop_table("world_states")
    op.drop_table("world_members")
    op.drop_table("sessions")
    op.drop_table("worlds")
    op.drop_table("users")
