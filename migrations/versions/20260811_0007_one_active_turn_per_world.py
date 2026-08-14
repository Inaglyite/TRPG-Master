"""Enforce the single-active-turn invariant at the database boundary.

Application-level checks make the normal path friendly, but they cannot close
the SQLite race between "no active row" and INSERT (and are only advisory for
other database writers).  A partial unique index is supported by both SQLite
and PostgreSQL and permits completed/interrupted history while allowing only
one ``status = 'active'`` row for each world.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "20260811_0007"
down_revision = "20260728_0006"
branch_labels = None
depends_on = None

TABLE = "turns"
INDEX = "uq_turns_one_active_per_world"
ACTIVE_STATUS = "active"


def _active_duplicates(bind) -> list[tuple[str, int]]:
    """Return a bounded diagnostic list without changing legacy turn rows."""
    rows = bind.execute(
        sa.text(
            """
            SELECT world_id, COUNT(*) AS active_count
            FROM turns
            WHERE status = :status
            GROUP BY world_id
            HAVING COUNT(*) > 1
            ORDER BY world_id
            LIMIT 10
            """
        ),
        {"status": ACTIVE_STATUS},
    ).all()
    return [(str(row[0]), int(row[1])) for row in rows]


def _index_named(bind) -> dict | None:
    return next(
        (
            index
            for index in sa.inspect(bind).get_indexes(TABLE)
            if index.get("name") == INDEX
        ),
        None,
    )


def _normalise_predicate(value: object) -> str:
    """Normalize the dialect-reflected form of ``status = 'active'``."""
    predicate = str(value if value is not None else "").lower().replace('"', "")
    # PostgreSQL reflects a VARCHAR comparison as, for example,
    # ``(status = 'active'::character varying)``.  The cast is immaterial to
    # this invariant, so remove it before requiring an otherwise exact match.
    predicate = re.sub(r"::[a-z_ ]+(?=[)\s]|$)", "", predicate)
    return re.sub(r"[\s()]", "", predicate)


def _is_expected_index(bind, index: dict) -> bool:
    if not index.get("unique") or index.get("column_names") != ["world_id"]:
        return False
    options = index.get("dialect_options") or {}
    predicate = options.get(f"{bind.dialect.name}_where")
    return _normalise_predicate(predicate) == "status='active'"


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return

    # Do not silently choose which in-flight action to interrupt.  If an old
    # installation already contains corrupt/overlapping active turns, leaving
    # it untouched and stopping the upgrade is safer than inventing history.
    duplicates = _active_duplicates(bind)
    if duplicates:
        detail = ", ".join(f"{world_id} ({count})" for world_id, count in duplicates)
        raise RuntimeError(
            "无法安装单世界活动回合约束：发现多个 active 回合（"
            f"{detail}）。请由维护者先显式恢复/中断这些回合后重试迁移；"
            "迁移没有修改任何回合记录。"
        )

    existing = _index_named(bind)
    if existing is not None:
        # ``Base.metadata.create_all`` is still used by a few older desktop
        # builds.  It may already have made precisely this index before the
        # database is stamped for Alembic; adopt that exact form.  A full
        # unique index or a different predicate is a name collision, so do
        # not overwrite it.
        if _is_expected_index(bind, existing):
            return
        raise RuntimeError(
            f"索引 {INDEX} 已存在，无法安全确认其 active 条件；"
            "请由维护者核对后再标记/重试迁移。"
        )

    op.create_index(
        INDEX,
        TABLE,
        ["world_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    if _index_named(bind) is not None:
        op.drop_index(INDEX, table_name=TABLE)
