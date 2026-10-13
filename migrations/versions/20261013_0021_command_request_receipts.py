"""Preserve originating requests without copying completed work into branch queues."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261013_0021"
down_revision = "20261013_0020"
branch_labels = None
depends_on = None
TABLE = "command_request_receipts"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
        if set(columns) != {"command_row_id", "context_json"} or (
            not isinstance(columns["command_row_id"]["type"], sa.String)
            or columns["command_row_id"]["type"].length != 48
            or columns["command_row_id"]["nullable"]
            or not isinstance(columns["context_json"]["type"], sa.JSON)
            or columns["context_json"]["nullable"]
        ):
            raise RuntimeError(f"无法接管 {TABLE}：列形状不符")
        fks = inspector.get_foreign_keys(TABLE)
        if inspector.get_pk_constraint(TABLE)["constrained_columns"] != ["command_row_id"] or (
            len(fks) != 1
            or fks[0]["constrained_columns"] != ["command_row_id"]
            or fks[0]["referred_table"] != "game_commands"
            or fks[0]["referred_columns"] != ["id"]
            or fks[0].get("options", {}).get("ondelete", "").upper() != "CASCADE"
        ):
            raise RuntimeError(f"无法接管 {TABLE}：主键或命令外键不符")
        if inspector.get_indexes(TABLE) or inspector.get_unique_constraints(TABLE):
            raise RuntimeError(f"无法接管 {TABLE}：存在未知索引或唯一约束")
        return
    op.create_table(
        TABLE,
        sa.Column(
            "command_row_id",
            sa.String(48),
            sa.ForeignKey("game_commands.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("context_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
