"""Immutable readable branch history, separate from transport outbox."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261004_0019"
down_revision = "20261002_0018"
branch_labels = None
depends_on = None
TABLE = "branch_history_entries"
COLUMNS = {
    "id",
    "world_id",
    "ordinal",
    "source_key",
    "message",
    "audience",
    "submitted_by",
    "created_at",
}
UNIQUES = {
    "uq_branch_history_ordinal": {"world_id", "ordinal"},
    "uq_branch_history_source": {"world_id", "source_key"},
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE in inspector.get_table_names():
        columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
        if set(columns) != COLUMNS:
            raise RuntimeError(f"无法接管 {TABLE}：列形状不符")
        expected_types = {
            "id": (sa.String, 48),
            "world_id": (sa.String, 160),
            "source_key": (sa.String, 512),
            "submitted_by": (sa.String, 48),
            "ordinal": (sa.BigInteger, None),
            "message": (sa.JSON, None),
            "audience": (sa.JSON, None),
            "created_at": (sa.DateTime, None),
        }
        for name, (column_type, length) in expected_types.items():
            actual = columns[name]
            if (
                not isinstance(actual["type"], column_type)
                or (length is not None and actual["type"].length != length)
                or (name != "id" and actual["nullable"] != (name == "submitted_by"))
            ):
                raise RuntimeError(f"无法接管 {TABLE}：{name} 类型或空值约束不符")
        if inspector.get_pk_constraint(TABLE)["constrained_columns"] != ["id"]:
            raise RuntimeError(f"无法接管 {TABLE}：主键不符")
        uniques = {
            row["name"]: set(row["column_names"]) for row in inspector.get_unique_constraints(TABLE)
        }
        if uniques != UNIQUES:
            raise RuntimeError(f"无法接管 {TABLE}：唯一约束缺失或形状不符")
        fks = inspector.get_foreign_keys(TABLE)
        if not any(
            fk["constrained_columns"] == ["world_id"]
            and fk["referred_table"] == "worlds"
            and fk["referred_columns"] == ["id"]
            and fk.get("options", {}).get("ondelete", "").upper() == "CASCADE"
            for fk in fks
        ):
            raise RuntimeError(f"无法接管 {TABLE}：世界外键不符")
        if len(fks) != 1 or not any(
            index["column_names"] == ["world_id"] and not index["unique"]
            for index in inspector.get_indexes(TABLE)
        ):
            raise RuntimeError(f"无法接管 {TABLE}：外键或世界索引形状不符")
        return
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("message", json_type, nullable=False),
        sa.Column("audience", json_type, nullable=False),
        sa.Column("submitted_by", sa.String(48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "ordinal", name="uq_branch_history_ordinal"),
        sa.UniqueConstraint("world_id", "source_key", name="uq_branch_history_source"),
    )
    op.create_index("ix_branch_history_entries_world_id", TABLE, ["world_id"])


def downgrade() -> None:
    op.drop_table(TABLE)
