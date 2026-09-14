"""打包升级证据：支持的旧库可升级且数据保留、新结构与 can_keeper 正确建立、重复启动幂等。

覆盖交付记录第 4 项要求的四条属性，其中第 4 条直接引用既有用例（不重复实现）：

1. 支持的旧库（无版本号的 0002 库）升级后**数据保留** —— 本文件
   `test_supported_old_database_upgrade_retains_data_and_builds_structured_schema`
2. 结构化表与 `world_members.can_keeper` **正确建立** —— 同一用例
3. **重复启动**幂等（第二次接管不再改写结构、不报错、数据仍在）—— 本文件
   `test_repeated_packaged_startup_is_idempotent`
4. 未知残缺 schema **仍被拒绝**（fail-closed 未被 `LATER_TABLES` 补全放松）——
   `tests/test_electron_packaging.py::test_packaged_migrations_reject_unknown_partial_schema`
   与 `..._adopt_create_all_database` 一族的 guard 用例

背景：`packaging/pyinstaller_runtime_hook.py` 的 `LATER_TABLES` 原本停在 0013，未纳入
0015（结构化协议）新增的 5 张表与 `world_members.can_keeper`，导致旧的无版本号桌面库
会被「无法接管未版本化数据库」拒绝。修好后由本文件钉住上面四条属性。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 0015 建立的结构化表；旧库升级后必须齐全。
STRUCTURED_TABLES = {
    "player_requests",
    "game_commands",
    "check_requests",
    "event_outbox",
    "keeper_control",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION_HOOK = _load_module(
    "trpg_packaged_upgrade_evidence_hook",
    PROJECT_ROOT / "packaging" / "pyinstaller_runtime_hook.py",
)


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _revision(database_url: str) -> str:
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()


def _upgrade_to_0002(database_url: str) -> None:
    """升到 0002 再删掉版本号：模拟早期桌面版 create_all/旧迁移留下的无版本号库。"""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260722_0002")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("DROP TABLE alembic_version"))
    finally:
        engine.dispose()


def _seed_legacy_rows(database_url: str) -> None:
    """写进升级前就存在的用户数据（原生 SQL：0002 表结构缺后续列）。"""
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO users "
                    "(id, username, password_hash, status, created_at, updated_at) "
                    "VALUES ('u-legacy', 'legacy', 'hash', 'active', "
                    "'2026-01-01T00:00:00', '2026-01-01T00:00:00')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO worlds "
                    "(id, module_name, module_id, module_version, created_by, status, "
                    "metadata_json, created_at, updated_at) "
                    "VALUES ('world-legacy', 'mansion_of_madness', '', '', 'u-legacy', "
                    "'active', '{\"name\": \"旧存档\"}', "
                    "'2026-01-01T00:00:00', '2026-01-01T00:00:00')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO world_members (id, world_id, user_id, role, created_at) "
                    "VALUES ('m-legacy', 'world-legacy', 'u-legacy', 'owner', "
                    "'2026-01-01T00:00:00')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO turns "
                    "(pk, id, world_id, kind, status, owner_token, record, messages, created_at) "
                    "VALUES (1, 't-legacy', 'world-legacy', 'action', 'completed', 'tok', '{}', "
                    "'[]', '2026-01-01T00:00:00')"
                )
            )
    finally:
        engine.dispose()


def _legacy_rows_snapshot(database_url: str) -> dict[str, list[tuple]]:
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                "users": connection.execute(
                    sa.text("SELECT id, username FROM users ORDER BY id")
                ).fetchall(),
                "worlds": connection.execute(
                    sa.text("SELECT id, module_name, metadata_json FROM worlds ORDER BY id")
                ).fetchall(),
                "world_members": connection.execute(
                    sa.text("SELECT id, world_id, user_id, role FROM world_members ORDER BY id")
                ).fetchall(),
                "turns": connection.execute(
                    sa.text("SELECT id, world_id, status FROM turns ORDER BY id")
                ).fetchall(),
            }
    finally:
        engine.dispose()


def test_supported_old_database_upgrade_retains_data_and_builds_structured_schema(
    tmp_path: Path,
) -> None:
    database_url = _sqlite_url(tmp_path / "old-0002.db")
    _upgrade_to_0002(database_url)
    _seed_legacy_rows(database_url)
    before = _legacy_rows_snapshot(database_url)

    MIGRATION_HOOK.run_packaged_migrations(
        resource_root=PROJECT_ROOT,
        database_url=database_url,
    )

    # 升到 head，且升级前的世界/成员/回合数据逐字段保留。
    assert _revision(database_url) == MIGRATION_HOOK.migration_head(PROJECT_ROOT)
    assert _legacy_rows_snapshot(database_url) == before
    assert before["users"] == [("u-legacy", "legacy")]
    assert before["worlds"][0][2] == '{"name": "旧存档"}'

    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert STRUCTURED_TABLES <= tables, sorted(STRUCTURED_TABLES - tables)
        member_columns = {column["name"] for column in inspector.get_columns("world_members")}
        assert "can_keeper" in member_columns
        # 新列默认关闭：旧成员不会凭空获得 keeper 授权。
        with engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT can_keeper FROM world_members WHERE id = 'm-legacy'")
                ).scalar_one()
                in (0, False)
            )
    finally:
        engine.dispose()


def test_repeated_packaged_startup_is_idempotent(tmp_path: Path) -> None:
    database_url = _sqlite_url(tmp_path / "repeat.db")
    _upgrade_to_0002(database_url)
    _seed_legacy_rows(database_url)

    MIGRATION_HOOK.run_packaged_migrations(resource_root=PROJECT_ROOT, database_url=database_url)
    after_first = _legacy_rows_snapshot(database_url)
    engine = sa.create_engine(database_url)
    try:
        columns_first = sorted(
            column["name"] for column in sa.inspect(engine).get_columns("world_members")
        )
    finally:
        engine.dispose()

    # 第二次启动（已带头版本号）必须是无副作用的 no-op。
    MIGRATION_HOOK.run_packaged_migrations(resource_root=PROJECT_ROOT, database_url=database_url)

    assert _revision(database_url) == MIGRATION_HOOK.migration_head(PROJECT_ROOT)
    assert _legacy_rows_snapshot(database_url) == after_first
    engine = sa.create_engine(database_url)
    try:
        assert sorted(
            column["name"] for column in sa.inspect(engine).get_columns("world_members")
        ) == columns_first
    finally:
        engine.dispose()


def test_missing_base_table_is_still_rejected(tmp_path: Path) -> None:
    """对偶：`LATER_TABLES` 补全只豁免 0015 新表；真正残缺的基础库仍 fail-closed。"""
    database_url = _sqlite_url(tmp_path / "partial.db")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="无法接管未版本化数据库"):
        MIGRATION_HOOK.run_packaged_migrations(
            resource_root=PROJECT_ROOT,
            database_url=database_url,
        )

# 基线指纹修订（与 pyinstaller_runtime_hook.BASELINE_SCHEMA_REVISION 一致）。
BASELINE_REVISION = "20260722_0004"


def _tables_at(tmp_path: Path, revision: str) -> set[str]:
    """把库升到指定修订后列出表名（用真实迁移链推导，不靠解析源码）。"""
    database_url = _sqlite_url(tmp_path / f"at-{revision}.db")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)
    engine = sa.create_engine(database_url)
    try:
        return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_later_tables_covers_every_table_added_after_the_baseline(tmp_path: Path) -> None:
    """基线之后新增的表必须列入 LATER_TABLES，且不得有冗余项。

    这张名单决定「旧的无版本号桌面库能否被接管」：漏一个就会被
    「无法接管未版本化数据库」拒绝、打包版启动不了（0015、0016 都真实踩过）。
    这里从真实迁移链推导，新增迁移无需人工维护本测试。
    """
    baseline_tables = _tables_at(tmp_path, BASELINE_REVISION)
    head_tables = _tables_at(tmp_path, MIGRATION_HOOK.migration_head(PROJECT_ROOT))
    expected = head_tables - baseline_tables
    assert "interaction_threads" in expected, "守卫失效：0016 的表没被算进新增表"
    missing = sorted(expected - MIGRATION_HOOK.LATER_TABLES)
    assert missing == [], f"这些表没进 LATER_TABLES：{missing}"
    stale = sorted(MIGRATION_HOOK.LATER_TABLES - head_tables)
    assert stale == [], f"LATER_TABLES 里有已不存在的表（名单过期）：{stale}"
