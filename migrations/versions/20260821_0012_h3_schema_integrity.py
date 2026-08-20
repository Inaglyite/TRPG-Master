"""Fail-closed integrity guard for the H3 Skill and memory schema.

The earlier H3 revisions deliberately support desktop databases created with
``Base.metadata.create_all`` before Alembic is first enabled.  Their initial
adopt-or-create checks predate the full H3 contract, however: 0009 only
checked columns plus its unique constraint and 0010 only checked columns plus
its key uniqueness/index.  A database with a weakened foreign key, nullable
JSON payload, wrong scalar type, or a missing query index could therefore be
stamped as current.

This revision is intentionally a *guard*, not a repair migration.  It makes no
schema or data changes.  At upgrade time it validates the complete currently
shipped H3 shape and fails closed if an existing database has drifted.  A
healthy fresh database and a healthy ``Base.metadata.create_all`` database both
pass unchanged.  Refusing an unknown shape is safer than guessing how to
rewrite persisted Skill authority or shadow-memory data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op

revision = "20260821_0012"
down_revision = "20260821_0011"
branch_labels = None
depends_on = None

WORLDS = "worlds"
USERS = "users"
PINS = "world_skill_pins"
MANIFESTS = "world_skill_pin_manifests"
CANDIDATES = "memory_fact_candidates"
FACTS = "memory_facts"

CURRENT_FACT_INDEX = "uq_memory_facts_current_per_subject_type"


# ``kind``, length and nullable are deliberately checked separately from
# server defaults.  SQLAlchemy's ORM-side defaults and Alembic's server
# defaults are both valid create_all/adopt representations, while a weakened
# type or nullable contract is not.
ColumnShape = tuple[str, int | None, bool]

PIN_COLUMNS: dict[str, ColumnShape] = {
    "id": ("string", 48, False),
    "world_id": ("string", 160, False),
    "skill_id": ("string", 120, False),
    "skill_version": ("string", 80, False),
    "content_digest": ("string", 80, False),
    "trust": ("string", 32, False),
    "residency": ("string", 32, False),
    "content": ("text", None, False),
    "pinned_at": ("datetime", None, False),
}

MANIFEST_COLUMNS: dict[str, ColumnShape] = {
    "pin_id": ("string", 48, False),
    "entry_snapshot": ("json", None, False),
}

CANDIDATE_COLUMNS: dict[str, ColumnShape] = {
    "id": ("string", 48, False),
    "world_id": ("string", 160, False),
    "root_world_id": ("string", 160, False),
    "source_turn_id": ("string", 80, False),
    "subject_id": ("string", 200, False),
    "subject_kind": ("string", 32, False),
    "fact_type": ("string", 64, False),
    "value": ("json", None, False),
    "digest": ("string", 64, False),
    "audience": ("string", 32, False),
    "owner_user_id": ("string", 48, True),
    "tier": ("integer", None, True),
    "provenance": ("json", None, False),
    "status": ("string", 20, False),
    "created_at": ("datetime", None, False),
}

FACT_COLUMNS: dict[str, ColumnShape] = {
    "id": ("string", 48, False),
    "world_id": ("string", 160, False),
    "root_world_id": ("string", 160, False),
    "source_turn_id": ("string", 80, False),
    "subject_id": ("string", 200, False),
    "subject_kind": ("string", 32, False),
    "fact_type": ("string", 64, False),
    "value": ("json", None, False),
    "digest": ("string", 64, False),
    "audience": ("string", 32, False),
    "owner_user_id": ("string", 48, True),
    "tier": ("integer", None, True),
    "provenance": ("json", None, False),
    "revision": ("bigint", None, False),
    "supersedes_id": ("string", 48, True),
    "status": ("string", 20, False),
    "created_at": ("datetime", None, False),
    "decided_at": ("datetime", None, False),
}


def _error(table: str, detail: str) -> RuntimeError:
    return RuntimeError(f"无法接管已存在的 {table}：{detail}")


def _require_table(inspector, table: str) -> None:
    if table not in set(inspector.get_table_names()):
        raise _error(table, "缺少表")


def _type_matches(actual: object, kind: str, length: int | None, dialect: str) -> bool:
    if kind == "string":
        return (
            isinstance(actual, sa.String)
            and not isinstance(actual, sa.Text)
            and getattr(actual, "length", None) == length
        )
    if kind == "text":
        return isinstance(actual, sa.Text)
    if kind == "json":
        # PostgreSQL reports JSONB while SQLite reports JSON.  Both are the
        # declared JSON value boundary; TEXT is deliberately not accepted.
        return isinstance(actual, sa.JSON) or actual.__class__.__name__.upper() == "JSONB"
    if kind == "integer":
        return isinstance(actual, sa.Integer) and not isinstance(actual, sa.BigInteger)
    if kind == "bigint":
        return isinstance(actual, sa.BigInteger)
    if kind == "datetime":
        if not isinstance(actual, sa.DateTime):
            return False
        # SQLite cannot reflect timezone metadata.  PostgreSQL can, so retain
        # the stronger check there without rejecting a legitimate SQLite
        # create_all database.
        return dialect == "sqlite" or getattr(actual, "timezone", None) is True
    raise AssertionError(f"unknown H3 column kind: {kind}")


def _validate_columns(inspector, table: str, expected: dict[str, ColumnShape], dialect: str) -> None:
    actual = {column["name"]: column for column in inspector.get_columns(table)}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        raise _error(table, "缺少列 " + ", ".join(missing))
    if extra:
        raise _error(table, "存在未知列 " + ", ".join(extra))
    for name, (kind, length, nullable) in expected.items():
        column = actual[name]
        if bool(column.get("nullable", True)) != nullable:
            requirement = "可为 NULL" if nullable else "必须 NOT NULL"
            raise _error(table, f"{name} {requirement}")
        if not _type_matches(column.get("type"), kind, length, dialect):
            expected_name = f"{kind}({length})" if length is not None else kind
            raise _error(table, f"{name} 类型必须是 {expected_name}")


def _validate_primary_key(inspector, table: str, columns: tuple[str, ...]) -> None:
    primary_key = tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ())
    if primary_key != columns:
        raise _error(table, f"主键必须为 {', '.join(columns)}")


def _validate_unique_constraints(
    inspector, table: str, expected: dict[str, tuple[str, ...]]
) -> None:
    actual = {
        str(constraint.get("name")): tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table)
    }
    if actual != expected:
        raise _error(table, "唯一约束与 H3 规范不一致")


def _foreign_key_shape(foreign_key: dict) -> tuple[tuple[str, ...], str, tuple[str, ...], str]:
    return (
        tuple(foreign_key.get("constrained_columns") or ()),
        str(foreign_key.get("referred_table") or ""),
        tuple(foreign_key.get("referred_columns") or ()),
        str((foreign_key.get("options") or {}).get("ondelete") or "").upper(),
    )


def _validate_foreign_keys(
    inspector,
    table: str,
    expected: Iterable[tuple[tuple[str, ...], str, tuple[str, ...], str]],
) -> None:
    actual_shapes = sorted(_foreign_key_shape(item) for item in inspector.get_foreign_keys(table))
    expected_shapes = sorted(expected)
    if actual_shapes != expected_shapes:
        raise _error(table, "外键或 ON DELETE 规则与 H3 规范不一致")


def _index_named(inspector, table: str, name: str) -> dict | None:
    return next((index for index in inspector.get_indexes(table) if index.get("name") == name), None)


def _has_predicate(index: dict) -> bool:
    options = index.get("dialect_options") or {}
    return any(key.endswith("_where") and value is not None for key, value in options.items())


def _validate_plain_index(
    inspector, table: str, name: str, columns: tuple[str, ...], *, unique: bool = False
) -> None:
    index = _index_named(inspector, table, name)
    if index is None:
        raise _error(table, f"缺少索引 {name}")
    if tuple(index.get("column_names") or ()) != columns or bool(index.get("unique")) != unique:
        raise _error(table, f"索引 {name} 形状不符")
    if _has_predicate(index):
        raise _error(table, f"索引 {name} 不得带条件谓词")


def _normalise_predicate(value: object) -> str:
    text = str(value if value is not None else "").lower()
    text = re.sub(r"::(?:character varying|varchar|text)", "", text)
    return re.sub(r"[\s\"`()]", "", text)


def _validate_current_fact_index(inspector, dialect: str) -> None:
    index = _index_named(inspector, FACTS, CURRENT_FACT_INDEX)
    if index is None:
        raise _error(FACTS, f"缺少索引 {CURRENT_FACT_INDEX}")
    if not index.get("unique") or tuple(index.get("column_names") or ()) != (
        "world_id",
        "subject_id",
        "fact_type",
    ):
        raise _error(FACTS, f"索引 {CURRENT_FACT_INDEX} 形状不符")
    predicate = (index.get("dialect_options") or {}).get(f"{dialect}_where")
    if _normalise_predicate(predicate) != "status='accepted'":
        raise _error(FACTS, f"索引 {CURRENT_FACT_INDEX} 必须仅约束 status='accepted'")


def _validate_world_root(inspector, dialect: str) -> None:
    _require_table(inspector, WORLDS)
    columns = {column["name"]: column for column in inspector.get_columns(WORLDS)}
    root = columns.get("root_world_id")
    if root is None:
        raise _error(WORLDS, "缺少列 root_world_id")
    if root.get("nullable", True):
        raise _error(WORLDS, "root_world_id 必须 NOT NULL")
    if not _type_matches(root.get("type"), "string", 160, dialect):
        raise _error(WORLDS, "root_world_id 类型必须是 string(160)")
    _validate_plain_index(
        inspector,
        WORLDS,
        "ix_worlds_root_world_id",
        ("root_world_id",),
    )


def _validate_world_skill_pins(inspector, dialect: str) -> None:
    _require_table(inspector, PINS)
    _validate_columns(inspector, PINS, PIN_COLUMNS, dialect)
    _validate_primary_key(inspector, PINS, ("id",))
    _validate_unique_constraints(
        inspector,
        PINS,
        {"uq_world_skill_pin": ("world_id", "skill_id")},
    )
    _validate_foreign_keys(
        inspector,
        PINS,
        [(("world_id",), WORLDS, ("id",), "CASCADE")],
    )
    _validate_plain_index(inspector, PINS, "ix_world_skill_pins_world_id", ("world_id",))


def _validate_skill_pin_manifests(inspector, dialect: str) -> None:
    _require_table(inspector, MANIFESTS)
    _validate_columns(inspector, MANIFESTS, MANIFEST_COLUMNS, dialect)
    _validate_primary_key(inspector, MANIFESTS, ("pin_id",))
    _validate_unique_constraints(inspector, MANIFESTS, {})
    _validate_foreign_keys(
        inspector,
        MANIFESTS,
        [(("pin_id",), PINS, ("id",), "CASCADE")],
    )


def _validate_memory_candidates(inspector, dialect: str) -> None:
    _require_table(inspector, CANDIDATES)
    _validate_columns(inspector, CANDIDATES, CANDIDATE_COLUMNS, dialect)
    _validate_primary_key(inspector, CANDIDATES, ("id",))
    _validate_unique_constraints(
        inspector,
        CANDIDATES,
        {
            "uq_memory_candidate_dedupe": (
                "world_id",
                "source_turn_id",
                "subject_id",
                "fact_type",
                "digest",
            )
        },
    )
    _validate_foreign_keys(
        inspector,
        CANDIDATES,
        [
            (("world_id",), WORLDS, ("id",), "CASCADE"),
            (("owner_user_id",), USERS, ("id",), "SET NULL"),
        ],
    )
    for name, columns in {
        "ix_memory_fact_candidates_world_id": ("world_id",),
        "ix_memory_fact_candidates_root_world_id": ("root_world_id",),
        "ix_memory_fact_candidates_source_turn_id": ("source_turn_id",),
        "ix_memory_fact_candidates_subject_id": ("subject_id",),
        "ix_memory_fact_candidates_owner_user_id": ("owner_user_id",),
        "ix_memory_fact_candidates_status": ("status",),
    }.items():
        _validate_plain_index(inspector, CANDIDATES, name, columns)


def _validate_memory_facts(inspector, dialect: str) -> None:
    _require_table(inspector, FACTS)
    _validate_columns(inspector, FACTS, FACT_COLUMNS, dialect)
    _validate_primary_key(inspector, FACTS, ("id",))
    _validate_unique_constraints(
        inspector,
        FACTS,
        {
            "uq_memory_fact_digest": (
                "world_id",
                "subject_id",
                "fact_type",
                "digest",
            )
        },
    )
    _validate_foreign_keys(
        inspector,
        FACTS,
        [
            (("world_id",), WORLDS, ("id",), "CASCADE"),
            (("owner_user_id",), USERS, ("id",), "SET NULL"),
            (("supersedes_id",), FACTS, ("id",), "RESTRICT"),
        ],
    )
    for name, columns in {
        "ix_memory_facts_world_id": ("world_id",),
        "ix_memory_facts_root_world_id": ("root_world_id",),
        "ix_memory_facts_source_turn_id": ("source_turn_id",),
        "ix_memory_facts_subject_id": ("subject_id",),
        "ix_memory_facts_owner_user_id": ("owner_user_id",),
        "ix_memory_facts_supersedes_id": ("supersedes_id",),
        "ix_memory_facts_status": ("status",),
    }.items():
        _validate_plain_index(inspector, FACTS, name, columns)
    _validate_current_fact_index(inspector, dialect)


def _validate_h3_schema(bind) -> None:
    """Validate every live H3 integrity boundary without modifying the DB.

    Kept importable for migration/package regression tests.  The caller may
    pass either an Engine or Alembic Connection.
    """
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name
    _require_table(inspector, USERS)
    _validate_world_root(inspector, dialect)
    _validate_world_skill_pins(inspector, dialect)
    _validate_skill_pin_manifests(inspector, dialect)
    _validate_memory_candidates(inspector, dialect)
    _validate_memory_facts(inspector, dialect)


def upgrade() -> None:
    _validate_h3_schema(op.get_bind())


def downgrade() -> None:
    # The revision deliberately introduced no new physical object.  Downgrade
    # only removes the Alembic marker; it must not weaken an existing schema.
    pass
