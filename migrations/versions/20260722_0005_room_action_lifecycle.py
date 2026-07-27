"""Make interrupted room actions retryable and use explicit running leases."""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0005"
down_revision = "20260722_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "room_actions" not in existing:
        return
    # Version 0004 wrote every accepted action as "accepted" forever, including
    # actions whose side effects and turns had already committed. Their outcome
    # is unknowable during upgrade, so preserve at-most-once semantics and fail
    # closed: legacy IDs remain non-retryable.
    op.execute(
        sa.text("UPDATE room_actions SET status = 'completed' WHERE status = 'accepted'")
    )
    with op.batch_alter_table("room_actions") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=20),
            server_default="running",
            existing_nullable=False,
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "room_actions" not in existing:
        return
    with op.batch_alter_table("room_actions") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=20),
            server_default="accepted",
            existing_nullable=False,
        )
