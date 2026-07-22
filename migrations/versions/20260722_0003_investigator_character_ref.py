"""Persist the validated character reference for each investigator claim."""

import sqlalchemy as sa
from alembic import op

from src.database import JSON_VALUE

revision = "20260722_0003"
down_revision = "20260722_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("world_investigators")
    }
    if "character_ref" not in columns:
        with op.batch_alter_table("world_investigators") as batch:
            batch.add_column(
                sa.Column("character_ref", JSON_VALUE, nullable=False, server_default="{}")
            )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("world_investigators")
    }
    if "character_ref" in columns:
        with op.batch_alter_table("world_investigators") as batch:
            batch.drop_column("character_ref")
