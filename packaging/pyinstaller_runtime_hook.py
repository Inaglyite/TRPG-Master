"""Run the packaged database migrations before importing ``server.py``.

PyInstaller executes this hook inside the frozen backend process.  A migration
failure is intentionally fatal: serving against an old schema is more
dangerous than refusing to start.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

# The last revision whose *table/column fingerprint* is represented below.
# Later data/default-only revisions are still applied by ``upgrade head``.
BASELINE_SCHEMA_REVISION = "20260722_0004"
LATER_TABLES = {"world_invites", "world_investigators", "room_actions"}


def packaged_resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()


def packaged_database_url(runtime_root: Path | None = None) -> str:
    configured = os.environ.get("TRPG_DATABASE_URL", "").strip()
    if configured:
        return configured
    root = Path(
        runtime_root
        or os.environ.get("TRPG_RUNTIME_ROOT")
        or Path(sys.executable).resolve().parent
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{root / 'trpg-master.db'}"


def _validate_columns(db_inspector, metadata, table: str, *, omit: set[str] | None = None) -> None:
    expected = set(metadata.tables[table].columns.keys()) - (omit or set())
    actual = {column["name"] for column in db_inspector.get_columns(table)}
    missing = sorted(expected - actual)
    if missing:
        raise RuntimeError(
            f"无法接管未版本化数据库：表 {table} 缺少列 {', '.join(missing)}"
        )


def detect_unversioned_revision(database_url: str) -> str | None:
    """Return a safe baseline revision, or ``None`` when no stamp is needed.

    Early desktop builds used ``Base.metadata.create_all`` and therefore have a
    complete application schema but no ``alembic_version`` row.  We only stamp
    such a database after its tables and columns match a known revision.
    Unknown/partial schemas fail closed.
    """

    from src.database import Base

    engine = create_engine(database_url)
    try:
        db_inspector = inspect(engine)
        tables = set(db_inspector.get_table_names())
        if "alembic_version" in tables:
            with engine.connect() as connection:
                versions = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars().all()
            if versions:
                return None
            tables.remove("alembic_version")
        if not tables:
            return None

        metadata = Base.metadata
        expected_tables = set(metadata.tables)
        base_tables = expected_tables - LATER_TABLES
        missing_base = sorted(base_tables - tables)
        if missing_base:
            raise RuntimeError(
                "无法接管未版本化数据库：缺少基础表 " + ", ".join(missing_base)
            )
        for table in sorted(base_tables):
            _validate_columns(db_inspector, metadata, table)

        has_invites = "world_invites" in tables
        has_investigators = "world_investigators" in tables
        if has_invites != has_investigators:
            raise RuntimeError(
                "无法接管未版本化数据库：多人成员表只存在一部分"
            )

        revision = "20260722_0001"
        has_character_ref = False
        if has_invites:
            _validate_columns(db_inspector, metadata, "world_invites")
            investigator_columns = {
                column["name"]
                for column in db_inspector.get_columns("world_investigators")
            }
            has_character_ref = "character_ref" in investigator_columns
            _validate_columns(
                db_inspector,
                metadata,
                "world_investigators",
                omit=set() if has_character_ref else {"character_ref"},
            )
            revision = "20260722_0003" if has_character_ref else "20260722_0002"

        if "room_actions" in tables:
            if not has_invites or not has_character_ref:
                raise RuntimeError(
                    "无法接管未版本化数据库：room_actions 与成员表版本不一致"
                )
            _validate_columns(db_inspector, metadata, "room_actions")
            revision = BASELINE_SCHEMA_REVISION
        return revision
    finally:
        engine.dispose()


def _alembic_config(resource_root: Path, database_url: str):
    from alembic.config import Config

    ini_path = resource_root / "alembic.ini"
    migrations_path = resource_root / "migrations"
    if not ini_path.is_file() or not (migrations_path / "env.py").is_file():
        raise RuntimeError("安装包缺少 Alembic 数据库迁移资源")
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(migrations_path))
    # ConfigParser treats '%' as interpolation.  The SQLAlchemy URL itself
    # remains unescaped when read back by Alembic.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def migration_head(resource_root: Path) -> str:
    from alembic.script import ScriptDirectory

    config = _alembic_config(Path(resource_root).resolve(), "sqlite://")
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"数据库迁移必须只有一个 head，当前为：{', '.join(heads)}")
    return heads[0]


def run_packaged_migrations(
    *,
    resource_root: Path | None = None,
    runtime_root: Path | None = None,
    database_url: str | None = None,
) -> str:
    from alembic import command

    resources = Path(resource_root or packaged_resource_root()).resolve()
    url = database_url or packaged_database_url(runtime_root)
    config = _alembic_config(resources, url)
    baseline = detect_unversioned_revision(url)

    # migrations/env.py honors TRPG_DATABASE_URL.  Temporarily remove it so
    # the absolute URL already installed in this Config is the single source
    # of truth, including URLs containing percent-encoded credentials.
    previous_url = os.environ.pop("TRPG_DATABASE_URL", None)
    try:
        if baseline:
            command.stamp(config, baseline)
        command.upgrade(config, "head")
    finally:
        if previous_url is None:
            os.environ.pop("TRPG_DATABASE_URL", None)
        else:
            # The caller's environment is process-global.  Restore it exactly;
            # bootstrap_packaged_database installs the migrated URL explicitly
            # after this helper returns.
            os.environ["TRPG_DATABASE_URL"] = previous_url
    return url


def bootstrap_packaged_database() -> None:
    url = run_packaged_migrations()
    # server.py and src.database must use the exact database just migrated.
    os.environ["TRPG_DATABASE_URL"] = url
    print("[bootstrap] database migrations are current", file=sys.stderr)


if (
    getattr(sys, "frozen", False)
    and os.environ.get("TRPG_SKIP_PACKAGED_MIGRATIONS") != "1"
):
    bootstrap_packaged_database()
