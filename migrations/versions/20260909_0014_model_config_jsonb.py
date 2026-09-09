"""Align PostgreSQL model config payloads with the ORM's JSONB variant.

0013 has already reached staging; keep its history intact and convert existing
rows in place. SQLite continues to use JSON without a table rebuild.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260909_0014"
down_revision = "20260907_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "model_service_configs",
            "payload_json",
            existing_type=sa.JSON(),
            type_=JSONB(),
            existing_nullable=False,
            postgresql_using="payload_json::jsonb",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "model_service_configs",
            "payload_json",
            existing_type=JSONB(),
            type_=sa.JSON(),
            existing_nullable=False,
            postgresql_using="payload_json::json",
        )
