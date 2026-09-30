"""Add character library entries (character_library_entries).

角色库：玩家可复用的角色资料卡，按 owner 隔离（空串 = 本地模式）。
Table shape follows the 0009 adopt-or-create contract so desktop builds that
ran ``Base.metadata.create_all`` before Alembic upgrade cleanly:

- Table missing: create it (normal fresh upgrade path).
- Table already exists with the exact expected shape: validate and no-op.
- Table exists with any other shape: fail closed; never invent a schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_0017"
down_revision = "20260914_0016"
branch_labels = None
depends_on = None

TABLE = "character_library_entries"
COLUMNS = ("id", "owner_user_id", "name", "card_json", "created_at", "updated_at")


def _validate_adopt_shape(bind) -> None:
    """Fail closed unless ``character_library_entries`` has the expected shape."""
    inspector = sa.inspect(bind)
    actual_columns = {column["name"] for column in inspector.get_columns(TABLE)}
    missing = sorted(set(COLUMNS) - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - set(COLUMNS))
    if extra:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：存在未知列 {', '.join(extra)}")


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        _validate_adopt_shape(bind)
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("owner_user_id", sa.String(48), nullable=False, server_default=""),
        sa.Column("name", sa.String(80), nullable=False, server_default=""),
        sa.Column("card_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_character_library_entries_owner_user_id", TABLE, ["owner_user_id"]
    )
    op.create_index("ix_character_library_entries_name", TABLE, ["name"])


def downgrade() -> None:
    op.drop_table(TABLE)
