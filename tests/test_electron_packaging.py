from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from src.database import ACTIVE_TURN_WORLD_INDEX, Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION_HOOK = _load_module(
    "trpg_packaged_migration_hook",
    PROJECT_ROOT / "packaging" / "pyinstaller_runtime_hook.py",
)
BUNDLE_VERIFY = _load_module(
    "trpg_bundle_verify",
    PROJECT_ROOT / "packaging" / "verify_backend_bundle.py",
)
MIGRATION_0011 = _load_module(
    "trpg_skill_pin_manifest_migration",
    PROJECT_ROOT / "migrations" / "versions" / "20260821_0011_skill_pin_manifests.py",
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


def test_packaged_migrations_create_fresh_database_at_head(
    tmp_path: Path, monkeypatch
) -> None:
    database_url = _sqlite_url(tmp_path / "fresh.db")
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)

    MIGRATION_HOOK.run_packaged_migrations(
        resource_root=PROJECT_ROOT,
        database_url=database_url,
    )

    assert _revision(database_url) == MIGRATION_HOOK.migration_head(PROJECT_ROOT)
    engine = sa.create_engine(database_url)
    try:
        indexes = {
            index["name"]: index for index in sa.inspect(engine).get_indexes("turns")
        }
        active_turn_index = indexes[ACTIVE_TURN_WORLD_INDEX]
        assert active_turn_index["unique"] == 1
        assert str(active_turn_index["dialect_options"]["sqlite_where"]) == "status = 'active'"
    finally:
        engine.dispose()
    assert "TRPG_DATABASE_URL" not in __import__("os").environ
    engine = sa.create_engine(database_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert {"users", "world_investigators", "room_actions"} <= tables
    finally:
        engine.dispose()


def test_packaged_migration_helper_restores_existing_database_url(
    tmp_path: Path, monkeypatch
) -> None:
    original_url = _sqlite_url(tmp_path / "original.db")
    target_url = _sqlite_url(tmp_path / "target.db")
    monkeypatch.setenv("TRPG_DATABASE_URL", original_url)

    MIGRATION_HOOK.run_packaged_migrations(
        resource_root=PROJECT_ROOT,
        database_url=target_url,
    )

    assert __import__("os").environ["TRPG_DATABASE_URL"] == original_url
    assert _revision(target_url) == MIGRATION_HOOK.migration_head(PROJECT_ROOT)


def test_packaged_migrations_safely_adopt_create_all_database(
    tmp_path: Path, monkeypatch
) -> None:
    database_url = _sqlite_url(tmp_path / "unversioned.db")
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)

    MIGRATION_HOOK.run_packaged_migrations(
        resource_root=PROJECT_ROOT,
        database_url=database_url,
    )

    assert _revision(database_url) == MIGRATION_HOOK.migration_head(PROJECT_ROOT)
    engine = sa.create_engine(database_url)
    try:
        indexes = {
            index["name"]: index for index in sa.inspect(engine).get_indexes("turns")
        }
        active_turn_index = indexes[ACTIVE_TURN_WORLD_INDEX]
        assert active_turn_index["unique"] == 1
        assert str(active_turn_index["dialect_options"]["sqlite_where"]) == "status = 'active'"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("case", "definition", "expected"),
    [
        (
            "not_null",
            "pin_id VARCHAR NOT NULL PRIMARY KEY, entry_snapshot JSON, "
            "FOREIGN KEY(pin_id) REFERENCES world_skill_pins(id) ON DELETE CASCADE",
            "entry_snapshot 必须 NOT NULL",
        ),
        (
            "json",
            "pin_id VARCHAR NOT NULL PRIMARY KEY, entry_snapshot TEXT NOT NULL, "
            "FOREIGN KEY(pin_id) REFERENCES world_skill_pins(id) ON DELETE CASCADE",
            "entry_snapshot 必须是 JSON/JSONB",
        ),
        (
            "primary_key",
            "pin_id VARCHAR NOT NULL, entry_snapshot JSON NOT NULL, "
            "FOREIGN KEY(pin_id) REFERENCES world_skill_pins(id) ON DELETE CASCADE",
            "pin_id 必须是唯一主键",
        ),
        (
            "cascade",
            "pin_id VARCHAR NOT NULL PRIMARY KEY, entry_snapshot JSON NOT NULL, "
            "FOREIGN KEY(pin_id) REFERENCES world_skill_pins(id)",
            "ON DELETE CASCADE",
        ),
    ],
)
def test_skill_pin_manifest_adoption_rejects_weakened_constraints(
    tmp_path: Path, case: str, definition: str, expected: str
) -> None:
    """0011 must not stamp a create_all-like table that weakens pin integrity."""
    engine = sa.create_engine(_sqlite_url(tmp_path / f"{case}.db"))
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE world_skill_pins (id VARCHAR PRIMARY KEY)"))
            connection.execute(
                sa.text(f"CREATE TABLE world_skill_pin_manifests ({definition})")
            )
        with pytest.raises(RuntimeError, match=expected):
            MIGRATION_0011._validate_adopt_shape(engine)
    finally:
        engine.dispose()


def test_packaged_migrations_upgrade_unversioned_revision_0002(
    tmp_path: Path, monkeypatch
) -> None:
    database_url = _sqlite_url(tmp_path / "old.db")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)
    command.upgrade(config, "20260722_0002")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE alembic_version"))
    engine.dispose()

    MIGRATION_HOOK.run_packaged_migrations(
        resource_root=PROJECT_ROOT,
        database_url=database_url,
    )

    assert _revision(database_url) == MIGRATION_HOOK.migration_head(PROJECT_ROOT)
    engine = sa.create_engine(database_url)
    try:
        columns = {
            column["name"]
            for column in sa.inspect(engine).get_columns("world_investigators")
        }
        assert "character_ref" in columns
        assert "room_actions" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_packaged_migrations_reject_unknown_partial_schema(
    tmp_path: Path, monkeypatch
) -> None:
    database_url = _sqlite_url(tmp_path / "partial.db")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
    engine.dispose()
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)

    try:
        MIGRATION_HOOK.run_packaged_migrations(
            resource_root=PROJECT_ROOT,
            database_url=database_url,
        )
    except RuntimeError as error:
        assert "无法接管未版本化数据库" in str(error)
    else:
        raise AssertionError("partial unversioned schema must fail closed")


def _fake_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "trpg-server"
    required_files = [
        "trpg-server.exe",
        "_internal/alembic.ini",
        "_internal/migrations/env.py",
        "_internal/migrations/versions/20260722_0001_database_control_plane.py",
        "_internal/migrations/versions/20260722_0004_room_action_idempotency.py",
        "_internal/mod/.keep",
        "_internal/rules/.keep",
        "_internal/skills/.keep",
        "_internal/tools/.keep",
        "_internal/characters/default/.keep",
    ]
    for relative in required_files:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return bundle


def test_backend_bundle_verifier_blocks_private_runtime_data(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path)
    assert BUNDLE_VERIFY.bundle_violations(bundle) == []

    leaked = bundle / "_internal" / "saves" / "case" / "messages.json"
    leaked.parent.mkdir(parents=True)
    leaked.write_text('{"secret":"keeper-only"}', encoding="utf8")
    violations = BUNDLE_VERIFY.bundle_violations(bundle)
    assert any("private runtime directory" in item for item in violations)


def test_pyinstaller_spec_uses_tracked_read_only_manifest_and_runtime_hook() -> None:
    text = (PROJECT_ROOT / "packaging" / "trpg-server.spec").read_text(
        encoding="utf8"
    )
    assert "git" in text and "ls-files" in text
    assert "--others" in text and "--exclude-standard" in text
    assert '"characters/default"' in text
    assert '"migrations"' in text
    assert '"schemas"' in text
    assert '"saves"' not in text
    assert '"profiles"' not in text
    assert 'runtime_hooks=[str(ROOT / "packaging" / "pyinstaller_runtime_hook.py")]' in text


def test_electron_setup_uses_scoped_ipc_and_source_backend_is_on_demand() -> None:
    main = (PROJECT_ROOT / "frontend" / "electron" / "main.cjs").read_text(
        encoding="utf8"
    )
    launcher = (PROJECT_ROOT / "start_desktop.sh").read_text(encoding="utf8")
    package = (PROJECT_ROOT / "frontend" / "package.json").read_text(
        encoding="utf8"
    )
    assert "__electron_save_env" not in main
    assert "Access-Control-Allow-Origin" not in main
    assert "setupServer.listen" not in main
    assert 'ipcMain.handle("trpg:save-local-config"' in main
    assert "event.sender !== activeWebContents" in main
    assert 'args: ["--backend-only"]' in main
    assert "sourceBackendLauncher" in main
    assert "await startSourceBackend(sourceBackendLauncher)" in main
    assert 'process.kill(-child.pid, signal)' in main
    assert 'processGroup: process.platform !== "win32"' in main
    assert 'signalBackendProcess(child, processGroup, "SIGKILL")' in main
    assert 'TRPG_SOURCE_BACKEND_LAUNCHER="$SCRIPT_DIR/start_desktop.sh"' in launcher
    assert 'if [ "$BACKEND_ONLY" = true ]; then' in launcher
    assert launcher.index('if [ "$BACKEND_ONLY" = true ]; then') < launcher.index(
        "# ---- Frontend dependencies and build ----"
    )
    assert 'set -m\n    "$SCRIPT_DIR/start_desktop.sh" --backend-only' in launcher
    assert "SERVER_PROCESS_GROUP=true" in launcher
    assert 'terminate_child "$SERVER_PID" "后端服务" "$SERVER_PROCESS_GROUP"' in launcher
    assert "start_browser_backend || exit 1" in launcher
    assert 'URL="http://localhost:8765/?mode=local"' in launcher
    assert '"dist": "node electron/reject-linux-package.cjs"' in package
    assert '"linux"' not in package


def _alembic_config_for(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_migration_0008_fresh_creates_tables(tmp_path: Path, monkeypatch) -> None:
    """0008 on an empty database creates both tables with full shape."""
    database_url = _sqlite_url(tmp_path / "fresh0008.db")
    config = _alembic_config_for(database_url)
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)
    command.stamp(config, "20260811_0007")
    command.upgrade(config, "20260818_0008")

    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert "context_sessions" in tables
        assert "model_context_events" in tables
        session_columns = {c["name"] for c in inspector.get_columns("context_sessions")}
        assert {
            "id", "world_id", "root_world_id", "session_epoch", "parent_session_id",
            "parent_world_id", "source_sequence", "head_sequence", "status",
            "seed_digest", "created_at", "closed_at",
        } <= session_columns
        event_columns = {c["name"] for c in inspector.get_columns("model_context_events")}
        assert {"source_sequences", "payload"} <= event_columns
        session_uniques = {u["name"] for u in inspector.get_unique_constraints("context_sessions")}
        assert "uq_context_session_world_epoch" in session_uniques
        event_uniques = {u["name"] for u in inspector.get_unique_constraints("model_context_events")}
        assert "uq_context_event_sequence" in event_uniques
        indexes = {i["name"]: i for i in inspector.get_indexes("context_sessions")}
        assert "uq_context_sessions_one_active_per_world" in indexes
        active = indexes["uq_context_sessions_one_active_per_world"]
        assert active["unique"] == 1
        assert str(active["dialect_options"]["sqlite_where"]) == "status = 'active'"
    finally:
        engine.dispose()


def test_migration_0008_adopts_create_all_database(tmp_path: Path, monkeypatch) -> None:
    """0008 no-ops on an already-created (create_all) exact shape."""
    database_url = _sqlite_url(tmp_path / "adopt0008.db")
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()

    config = _alembic_config_for(database_url)
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)
    command.stamp(config, "20260811_0007")
    command.upgrade(config, "20260818_0008")

    assert _revision(database_url) == "20260818_0008"


def test_migration_0008_rejects_partial_context_schema(tmp_path: Path, monkeypatch) -> None:
    """0008 fails closed when only one context table exists."""
    database_url = _sqlite_url(tmp_path / "partial0008.db")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE context_sessions ("
                "id VARCHAR(48) PRIMARY KEY,"
                "world_id VARCHAR(160) NOT NULL,"
                "root_world_id VARCHAR(160) NOT NULL,"
                "session_epoch BIGINT NOT NULL,"
                "parent_session_id VARCHAR(48),"
                "parent_world_id VARCHAR(160),"
                "source_sequence BIGINT NOT NULL DEFAULT 0,"
                "head_sequence BIGINT NOT NULL DEFAULT 0,"
                "status VARCHAR(20) NOT NULL DEFAULT 'active',"
                "seed_digest VARCHAR(64) NOT NULL DEFAULT '',"
                "created_at DATETIME NOT NULL,"
                "closed_at DATETIME"
                ")"
            )
        )
    engine.dispose()

    config = _alembic_config_for(database_url)
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)
    command.stamp(config, "20260811_0007")
    try:
        command.upgrade(config, "20260818_0008")
    except RuntimeError as error:
        assert "只存在一部分" in str(error)
    else:
        raise AssertionError("partial context schema must fail closed")


def test_migration_0008_rejects_existing_tables_without_active_index(
    tmp_path: Path, monkeypatch
) -> None:
    """Existing context tables are adopted only as one exact schema unit."""
    database_url = _sqlite_url(tmp_path / "missing-context-index.db")
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DROP INDEX uq_context_sessions_one_active_per_world")
        )
    engine.dispose()

    config = _alembic_config_for(database_url)
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)
    command.stamp(config, "20260811_0007")
    try:
        command.upgrade(config, "20260818_0008")
    except RuntimeError as error:
        assert "缺少索引" in str(error)
    else:
        raise AssertionError("context schema without its active index must fail closed")
