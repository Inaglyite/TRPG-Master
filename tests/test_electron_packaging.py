from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from src.database import Base

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
