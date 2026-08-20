"""Add frozen Skill manifest metadata sidecar (world_skill_pin_manifests).

H3 world freeze completion: content+version+digest alone left old worlds
re-reading the *current* catalog for opening/model_invocable/activation
decisions — a hot-update channel.  This sidecar table freezes the full
SkillEntry manifest JSON (plus catalog order) per pin, 1:1 with
``world_skill_pins``.  A sidecar (not a new column) keeps the 0009 table
shape immutable.

Contract (mirrors 0008/0009):

- ``world_skill_pins`` missing: fail closed (0009 must have run first).
- Sidecar already exists (``Base.metadata.create_all`` desktop builds):
  strictly validate the expected shape, then no-op.
- Sidecar exists with any other shape: fail closed; never invent a schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260821_0011"
down_revision = "20260820_0010"
branch_labels = None
depends_on = None

PINS = "world_skill_pins"
TABLE = "world_skill_pin_manifests"
COLUMNS = ("pin_id", "entry_snapshot")


def _validate_adopt_shape(bind) -> None:
    """Fail closed unless ``world_skill_pin_manifests`` has exactly the expected shape."""
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
    actual_columns = set(columns)
    missing = sorted(set(COLUMNS) - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - set(COLUMNS))
    if extra:
        raise RuntimeError(f"无法接管已存在的 {TABLE}：存在未知列 {', '.join(extra)}")

    for column_name in COLUMNS:
        if columns[column_name].get("nullable", True):
            raise RuntimeError(f"无法接管已存在的 {TABLE}：{column_name} 必须 NOT NULL")
    snapshot_type = str(columns["entry_snapshot"].get("type") or "").upper()
    if snapshot_type not in {"JSON", "JSONB"}:
        raise RuntimeError(
            f"无法接管已存在的 {TABLE}：entry_snapshot 必须是 JSON/JSONB"
        )

    primary_key = tuple(inspector.get_pk_constraint(TABLE).get("constrained_columns") or ())
    if primary_key != ("pin_id",):
        raise RuntimeError(f"无法接管已存在的 {TABLE}：pin_id 必须是唯一主键")

    foreign_keys = inspector.get_foreign_keys(TABLE)
    matching_foreign_keys = [
        foreign_key
        for foreign_key in foreign_keys
        if tuple(foreign_key.get("constrained_columns") or ()) == ("pin_id",)
        and foreign_key.get("referred_table") == PINS
        and tuple(foreign_key.get("referred_columns") or ()) == ("id",)
        and str((foreign_key.get("options") or {}).get("ondelete") or "").upper()
        == "CASCADE"
    ]
    if len(foreign_keys) != 1 or len(matching_foreign_keys) != 1:
        raise RuntimeError(
            f"无法接管已存在的 {TABLE}：pin_id 必须外键引用 {PINS}.id ON DELETE CASCADE"
        )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if PINS not in tables:
        raise RuntimeError(f"无法接管未版本化数据库：缺少表 {PINS}（请先完成 0009）")
    if TABLE in tables:
        _validate_adopt_shape(bind)
        return

    op.create_table(
        TABLE,
        sa.Column(
            "pin_id",
            sa.String(48),
            sa.ForeignKey(PINS + ".id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "entry_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
