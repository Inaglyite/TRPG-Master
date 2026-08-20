"""Add structured memory (worlds.root_world_id + memory fact tables).

H3 structured-memory half-side: a world's timeline root is denormalized onto
``worlds.root_world_id`` (backfilled from the branch parent chain), plus two
tables that split the candidate→accepted-fact boundary:

- ``memory_fact_candidates``: model/engine *proposals* only — never authority.
- ``memory_facts``: trusted-engine *accepted* facts.  A partial unique index
  enforces at most one current fact per (world, subject, fact_type); a
  conflicting re-accept supersedes rather than overwrites.

Both tables follow 0008/0009's adopt-or-create contract so
``Base.metadata.create_all`` desktop builds upgrade cleanly (validate the
exact shape then no-op; a mismatched shape fails closed).  The
``worlds.root_world_id`` column is added idempotently and backfilled only
where currently empty — existing values are never overwritten.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260820_0010"
down_revision = "20260820_0009"
branch_labels = None
depends_on = None

WORLDS = "worlds"
ROOT_COLUMN = "root_world_id"
CANDIDATES = "memory_fact_candidates"
FACTS = "memory_facts"
CURRENT_INDEX = "uq_memory_facts_current_per_subject_type"

CANDIDATE_COLUMNS = (
    "id",
    "world_id",
    "root_world_id",
    "source_turn_id",
    "subject_id",
    "subject_kind",
    "fact_type",
    "value",
    "digest",
    "audience",
    "owner_user_id",
    "tier",
    "provenance",
    "status",
    "created_at",
)

FACT_COLUMNS = (
    "id",
    "world_id",
    "root_world_id",
    "source_turn_id",
    "subject_id",
    "subject_kind",
    "fact_type",
    "value",
    "digest",
    "audience",
    "owner_user_id",
    "tier",
    "provenance",
    "revision",
    "supersedes_id",
    "status",
    "created_at",
    "decided_at",
)

CANDIDATE_UNIQUE = ("uq_memory_candidate_dedupe",)
FACT_UNIQUE = ("uq_memory_fact_digest",)

# ``index=True`` columns in the ORM (kept for query parity on fresh builds).
CANDIDATE_INDEX_COLUMNS = (
    "world_id",
    "root_world_id",
    "source_turn_id",
    "subject_id",
    "owner_user_id",
    "status",
)
FACT_INDEX_COLUMNS = (
    "world_id",
    "root_world_id",
    "source_turn_id",
    "subject_id",
    "owner_user_id",
    "supersedes_id",
    "status",
)


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _index_named(bind, table: str, name: str) -> dict | None:
    return next(
        (index for index in sa.inspect(bind).get_indexes(table) if index.get("name") == name),
        None,
    )


def _normalise_predicate(value: object) -> str:
    predicate = str(value if value is not None else "").lower().replace('"', "")
    return (
        predicate.replace("::character varying", "")
        .replace("::varchar", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
    )


def _is_current_index(bind, index: dict) -> bool:
    if (
        not index.get("unique")
        or index.get("column_names") != ["world_id", "subject_id", "fact_type"]
    ):
        return False
    options = index.get("dialect_options") or {}
    predicate = options.get(f"{bind.dialect.name}_where")
    return _normalise_predicate(predicate) == "status='accepted'"


def _validate_adopt_shape(
    bind, table: str, expected_columns: tuple[str, ...], unique_names: tuple[str, ...]
) -> None:
    """Fail closed unless ``table`` has exactly the expected shape."""
    inspector = sa.inspect(bind)
    actual_columns = {column["name"] for column in inspector.get_columns(table)}
    missing = sorted(set(expected_columns) - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - set(expected_columns))
    if extra:
        raise RuntimeError(f"无法接管已存在的 {table}：存在未知列 {', '.join(extra)}")
    actual_unique = {u["name"] for u in inspector.get_unique_constraints(table)}
    missing_unique = sorted(set(unique_names) - actual_unique)
    if missing_unique:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少唯一约束 {', '.join(missing_unique)}")


def _backfill_root_ids(bind) -> int:
    """Fill empty ``worlds.root_world_id`` from the branch parent chain.

    Walks ``metadata_json["branch"]["parent_world_id"]`` to the tree root,
    cycle-safe.  Only empty values are written; existing values survive.
    """
    rows = bind.execute(sa.text("SELECT id, metadata_json FROM worlds")).mappings().all()
    parent_by_id: dict[str, str | None] = {}
    for row in rows:
        meta = row["metadata_json"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = None
        parent: str | None = None
        if isinstance(meta, dict):
            branch = meta.get("branch")
            if isinstance(branch, dict):
                candidate = str(branch.get("parent_world_id") or "").strip()
                parent = candidate or None
        parent_by_id[str(row["id"])] = parent

    def root_of(world_id: str) -> str:
        seen: set[str] = set()
        current = world_id
        while current and current not in seen:
            seen.add(current)
            parent = parent_by_id.get(current)
            if not parent or parent not in parent_by_id:
                return current
            current = parent
        return world_id

    updated = 0
    for world_id in parent_by_id:
        result = bind.execute(
            sa.text(
                "UPDATE worlds SET root_world_id = :root "
                "WHERE id = :wid AND (root_world_id IS NULL OR root_world_id = '')"
            ),
            {"root": root_of(world_id), "wid": world_id},
        )
        updated += int(result.rowcount or 0)
    return updated


def _create_candidates() -> None:
    op.create_table(
        CANDIDATES,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("root_world_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("source_turn_id", sa.String(80), nullable=False),
        sa.Column("subject_id", sa.String(200), nullable=False),
        sa.Column("subject_kind", sa.String(32), nullable=False, server_default="npc"),
        sa.Column("fact_type", sa.String(64), nullable=False),
        sa.Column("value", _json(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False, server_default="public"),
        sa.Column(
            "owner_user_id",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.Column("provenance", _json(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "world_id",
            "source_turn_id",
            "subject_id",
            "fact_type",
            "digest",
            name="uq_memory_candidate_dedupe",
        ),
    )
    for column in CANDIDATE_INDEX_COLUMNS:
        op.create_index(f"ix_{CANDIDATES}_{column}", CANDIDATES, [column])


def _create_facts() -> None:
    op.create_table(
        FACTS,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("root_world_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("source_turn_id", sa.String(80), nullable=False),
        sa.Column("subject_id", sa.String(200), nullable=False),
        sa.Column("subject_kind", sa.String(32), nullable=False, server_default="npc"),
        sa.Column("fact_type", sa.String(64), nullable=False),
        sa.Column("value", _json(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False, server_default="public"),
        sa.Column(
            "owner_user_id",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.Column("provenance", _json(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "supersedes_id",
            sa.String(48),
            sa.ForeignKey(FACTS + ".id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="accepted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "world_id",
            "subject_id",
            "fact_type",
            "digest",
            name="uq_memory_fact_digest",
        ),
    )
    for column in FACT_INDEX_COLUMNS:
        op.create_index(f"ix_{FACTS}_{column}", FACTS, [column])
    op.create_index(
        CURRENT_INDEX,
        FACTS,
        ["world_id", "subject_id", "fact_type"],
        unique=True,
        sqlite_where=sa.text("status = 'accepted'"),
        postgresql_where=sa.text("status = 'accepted'"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) worlds.root_world_id — idempotent column add + safe backfill.
    world_columns = {column["name"] for column in inspector.get_columns(WORLDS)}
    if ROOT_COLUMN not in world_columns:
        op.add_column(
            WORLDS,
            sa.Column(ROOT_COLUMN, sa.String(160), nullable=False, server_default=""),
        )
        op.create_index("ix_worlds_root_world_id", WORLDS, [ROOT_COLUMN])
    _backfill_root_ids(bind)

    # 2) memory_fact_candidates — adopt-or-create.
    tables = set(inspector.get_table_names())
    if CANDIDATES in tables:
        _validate_adopt_shape(bind, CANDIDATES, CANDIDATE_COLUMNS, CANDIDATE_UNIQUE)
    else:
        _create_candidates()

    # 3) memory_facts — adopt-or-create, incl. the partial current index.
    if FACTS in tables:
        _validate_adopt_shape(bind, FACTS, FACT_COLUMNS, FACT_UNIQUE)
        existing = _index_named(bind, FACTS, CURRENT_INDEX)
        if existing is None:
            raise RuntimeError(f"无法接管已存在的 {FACTS}：缺少索引 {CURRENT_INDEX}")
        if not _is_current_index(bind, existing):
            raise RuntimeError(
                f"索引 {CURRENT_INDEX} 已存在且形状不符；请由维护者核对后再标记/重试迁移。"
            )
    else:
        _create_facts()


def downgrade() -> None:
    op.drop_table(FACTS)
    op.drop_table(CANDIDATES)
    op.drop_index("ix_worlds_root_world_id", table_name=WORLDS)
    op.drop_column(WORLDS, ROOT_COLUMN)
