"""Add per-world Skill content pins (world_skill_pins).

H3 world freeze: a world pins its resolved Skill Catalog exactly once and
never hot-reloads edited skill files afterwards; branches inherit the source
world's pins by copy.  The table shape follows 0008's adopt-or-create
contract so ``Base.metadata.create_all`` desktop builds upgrade cleanly:

- Table missing: create it (normal fresh upgrade path).
- Table already exists with the exact expected shape: validate and no-op.
- Table exists with any other shape: fail closed; never invent a schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260820_0009"
down_revision = "20260818_0008"
branch_labels = None
depends_on = None

TABLE = "world_skill_pins"
COLUMNS = (
    "id",
    "world_id",
    "skill_id",
    "skill_version",
    "content_digest",
    "trust",
    "residency",
    "content",
    "pinned_at",
)
UNIQUE = ("uq_world_skill_pin",)


def _validate_adopt_shape(bind) -> None:
    """Fail closed unless ``world_skill_pins`` has exactly the expected shape."""
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
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("skill_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("content_digest", sa.String(80), nullable=False),
        sa.Column("trust", sa.String(32), nullable=False, server_default="core"),
        sa.Column("residency", sa.String(32), nullable=False, server_default="core"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "skill_id", name="uq_world_skill_pin"),
    )
    op.create_index("ix_world_skill_pins_world_id", TABLE, ["world_id"])


def downgrade() -> None:
    op.drop_table(TABLE)
