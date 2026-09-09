"""Add per-account/world model service configs (model_service_configs).

用户模型配置（BYOK）：``world_id == ""`` 表示账号默认作用域，非空是世界/
房间覆盖。payload_json 内凭据为 Fernet 密文，主密钥独立于数据库。

Table shape follows the 0009 adopt-or-create contract so desktop builds that
ran ``Base.metadata.create_all`` before Alembic upgrade cleanly:

- Table missing: create it (normal fresh upgrade path).
- Table already exists with the exact expected shape: validate and no-op.
- Table exists with any other shape: fail closed; never invent a schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260907_0013"
down_revision = "20260821_0012"
branch_labels = None
depends_on = None

TABLE = "model_service_configs"
COLUMNS = ("id", "owner_user_id", "world_id", "payload_json", "revision", "updated_at")
UNIQUE = ("uq_model_service_config_scope",)


def _validate_adopt_shape(bind) -> None:
    """Fail closed unless ``model_service_configs`` has exactly the expected shape."""
    inspector = sa.inspect(bind)
    actual_columns = {column["name"] for column in inspector.get_columns(TABLE)}
    missing = sorted(set(COLUMNS) - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - set(COLUMNS))
    if extra:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：存在未知列 {', '.join(extra)}")
    actual_unique = {u["name"] for u in inspector.get_unique_constraints(TABLE)}
    missing_unique = sorted(set(UNIQUE) - actual_unique)
    if missing_unique:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：缺少唯一约束 {', '.join(missing_unique)}")


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        _validate_adopt_shape(bind)
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("world_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "world_id", name="uq_model_service_config_scope"),
    )
    op.create_index("ix_model_service_configs_owner_user_id", TABLE, ["owner_user_id"])
    op.create_index("ix_model_service_configs_world_id", TABLE, ["world_id"])


def downgrade() -> None:
    op.drop_table(TABLE)
