"""Add account display names and per-account module display order."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261119_0022"
down_revision = "20261013_0021"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "display_name" not in columns:
        op.add_column(
            "users",
            sa.Column("display_name", sa.String(40), nullable=False, server_default=""),
        )
    if "module_order" not in columns:
        op.add_column(
            "users",
            sa.Column("module_order", JSON_VALUE, nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "module_order" in columns:
        op.drop_column("users", "module_order")
    if "display_name" in columns:
        op.drop_column("users", "display_name")
