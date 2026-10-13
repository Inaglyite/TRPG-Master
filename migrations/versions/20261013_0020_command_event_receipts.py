"""Durable emitted events for complete structured command receipts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261013_0020"
down_revision = "20261004_0019"
branch_labels = None
depends_on = None
TABLE = "command_event_receipts"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
        expected = {
            "command_row_id": (sa.String, 48, False),
            "ordinal": (sa.Integer, None, False),
            "event_type": (sa.String, 80, False),
            "message_id": (sa.String, 160, True),
            "event_json": (sa.JSON, None, False),
        }
        if set(columns) != set(expected):
            raise RuntimeError(f"无法接管 {TABLE}：列形状不符")
        for name, (kind, length, nullable) in expected.items():
            c = columns[name]
            if (
                not isinstance(c["type"], kind)
                or c["nullable"] != nullable
                or (length is not None and c["type"].length != length)
            ):
                raise RuntimeError(f"无法接管 {TABLE}：{name} 类型或空值约束不符")
        if inspector.get_pk_constraint(TABLE)["constrained_columns"] != [
            "command_row_id",
            "ordinal",
        ]:
            raise RuntimeError(f"无法接管 {TABLE}：主键不符")
        fks = inspector.get_foreign_keys(TABLE)
        if len(fks) != 1 or not (
            fks[0]["constrained_columns"] == ["command_row_id"]
            and fks[0]["referred_table"] == "game_commands"
            and fks[0]["referred_columns"] == ["id"]
            and fks[0].get("options", {}).get("ondelete", "").upper() == "CASCADE"
        ):
            raise RuntimeError(f"无法接管 {TABLE}：命令外键不符")
        indexes = {
            tuple(i["column_names"]) for i in inspector.get_indexes(TABLE) if not i["unique"]
        }
        if indexes != {("event_type",), ("message_id",)} or inspector.get_unique_constraints(TABLE):
            raise RuntimeError(f"无法接管 {TABLE}：索引或唯一约束不符")
        return
    op.create_table(
        TABLE,
        sa.Column(
            "command_row_id",
            sa.String(48),
            sa.ForeignKey("game_commands.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("message_id", sa.String(160), nullable=True),
        sa.Column("event_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    )
    op.create_index("ix_command_event_receipts_event_type", TABLE, ["event_type"])
    op.create_index("ix_command_event_receipts_message_id", TABLE, ["message_id"])


def downgrade() -> None:
    op.drop_table(TABLE)
