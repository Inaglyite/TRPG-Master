"""Persist multiplayer room action idempotency across process restarts."""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0004"
down_revision = "20260722_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "room_actions" in existing:
        return
    op.create_table(
        "room_actions",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("world_id", sa.String(length=160), nullable=False),
        sa.Column("action_id", sa.String(length=160), nullable=False),
        sa.Column("submitted_by", sa.String(length=48), nullable=False),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="accepted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("world_id", "action_id", name="uq_room_action_id"),
    )
    op.create_index("ix_room_actions_world_id", "room_actions", ["world_id"])
    op.create_index("ix_room_actions_submitted_by", "room_actions", ["submitted_by"])
    op.create_index("ix_room_actions_status", "room_actions", ["status"])


def downgrade() -> None:
    op.drop_table("room_actions")
