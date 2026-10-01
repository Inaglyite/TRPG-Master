"""Align PostgreSQL character card payloads with the ORM JSONB variant.

0017 has shipped: preserve its history and convert existing cards in place.
SQLite keeps its JSON column without rebuilding the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261002_0018"
down_revision = "20260923_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "character_library_entries",
            "card_json",
            existing_type=sa.JSON(),
            type_=JSONB(),
            existing_nullable=False,
            postgresql_using="card_json::jsonb",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "character_library_entries",
            "card_json",
            existing_type=JSONB(),
            type_=sa.JSON(),
            existing_nullable=False,
            postgresql_using="card_json::json",
        )
