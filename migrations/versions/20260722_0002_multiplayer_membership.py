"""Add multiplayer invitations and investigator ownership."""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0002"
down_revision = "20260722_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "world_invites" not in existing:
        op.create_table(
            "world_invites",
            sa.Column("id", sa.String(length=48), primary_key=True),
            sa.Column("world_id", sa.String(length=160), nullable=False),
            sa.Column("invited_by", sa.String(length=48), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="player"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index("ix_world_invites_world_id", "world_invites", ["world_id"])
        op.create_index("ix_world_invites_invited_by", "world_invites", ["invited_by"])
        op.create_index("ix_world_invites_token_hash", "world_invites", ["token_hash"])
        op.create_index("ix_world_invites_expires_at", "world_invites", ["expires_at"])

    if "world_investigators" not in existing:
        op.create_table(
            "world_investigators",
            sa.Column("id", sa.String(length=48), primary_key=True),
            sa.Column("world_id", sa.String(length=160), nullable=False),
            sa.Column("character_key", sa.String(length=200), nullable=False),
            sa.Column("controller_user_id", sa.String(length=48), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["world_id"], ["worlds.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["controller_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("world_id", "character_key", name="uq_world_character_key"),
            sa.UniqueConstraint("world_id", "controller_user_id", name="uq_world_controller"),
        )
        op.create_index("ix_world_investigators_world_id", "world_investigators", ["world_id"])
        op.create_index(
            "ix_world_investigators_controller_user_id",
            "world_investigators",
            ["controller_user_id"],
        )
        op.create_index("ix_world_investigators_status", "world_investigators", ["status"])


def downgrade() -> None:
    op.drop_table("world_investigators")
    op.drop_table("world_invites")
