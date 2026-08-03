"""Converge invitation token uniqueness across Alembic and create_all schemas."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260728_0006"
down_revision = "20260722_0005"
branch_labels = None
depends_on = None

TABLE = "world_invites"
COLUMN = "token_hash"
INDEX = "ix_world_invites_token_hash"
SQLITE_UNIQUE_NAME = "uq_world_invites_token_hash"
SQLITE_NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}


def _matching_unique_constraints(bind) -> list[dict]:
    return [
        constraint
        for constraint in sa.inspect(bind).get_unique_constraints(TABLE)
        if constraint.get("column_names") == [COLUMN]
    ]


def _token_index(bind) -> dict | None:
    return next(
        (
            index
            for index in sa.inspect(bind).get_indexes(TABLE)
            if index.get("name") == INDEX
        ),
        None,
    )


def _drop_legacy_unique_constraints(bind) -> None:
    constraints = _matching_unique_constraints(bind)
    if not constraints:
        return
    if bind.dialect.name == "sqlite":
        # SQLite's automatically named UNIQUE constraint has no reflected
        # name. A batch naming convention gives it a deterministic name while
        # Alembic rebuilds the table.
        with op.batch_alter_table(
            TABLE,
            naming_convention=SQLITE_NAMING_CONVENTION,
        ) as batch:
            batch.drop_constraint(SQLITE_UNIQUE_NAME, type_="unique")
        return
    for constraint in constraints:
        name = constraint.get("name")
        if not name:
            raise RuntimeError(
                f"{TABLE}.{COLUMN} 的 UNIQUE 约束没有可迁移的名称"
            )
        op.drop_constraint(name, TABLE, type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    _drop_legacy_unique_constraints(bind)
    index = _token_index(bind)
    if index is not None and not index.get("unique"):
        op.drop_index(INDEX, table_name=TABLE)
        index = None
    if index is None:
        op.create_index(INDEX, TABLE, [COLUMN], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    index = _token_index(bind)
    if index is not None:
        op.drop_index(INDEX, table_name=TABLE)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            batch.create_unique_constraint(SQLITE_UNIQUE_NAME, [COLUMN])
    else:
        op.create_unique_constraint(SQLITE_UNIQUE_NAME, TABLE, [COLUMN])
    op.create_index(INDEX, TABLE, [COLUMN], unique=False)
