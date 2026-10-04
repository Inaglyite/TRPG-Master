"""Archive migration adopts only an exact known schema and preserves old data."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src.app.config import PROJECT_ROOT
from src.storage.database import Base


def config(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'archive.db'}"
    monkeypatch.setenv("TRPG_DATABASE_URL", url)
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return cfg, create_engine(url)


def test_0018_upgrade_preserves_users_and_creates_archive_idempotently(tmp_path, monkeypatch):
    cfg, engine = config(tmp_path, monkeypatch)
    try:
        command.upgrade(cfg, "20261002_0018")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, status, created_at, updated_at) VALUES ('user', 'archive-test', 'not-a-real-hash', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            before = conn.execute(text("SELECT * FROM users")).all()
        for _ in range(2):
            command.upgrade(cfg, "head")
            with engine.connect() as conn:
                assert conn.execute(text("SELECT * FROM users")).all() == before
                assert (
                    conn.execute(text("SELECT COUNT(*) FROM branch_history_entries")).scalar_one()
                    == 0
                )
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "20261004_0019"
                )
        assert (
            inspect(engine).get_foreign_keys("branch_history_entries")[0]["options"]["ondelete"]
            == "CASCADE"
        )
    finally:
        engine.dispose()


def test_create_all_database_archive_is_adopted_without_replacement(tmp_path, monkeypatch):
    cfg, engine = config(tmp_path, monkeypatch)
    try:
        Base.metadata.create_all(engine)
        command.stamp(cfg, "20261002_0018")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO worlds (id, module_name, module_id, module_version, status, root_world_id, metadata_json, created_at, updated_at) VALUES ('branch', 'test', '', '', 'active', '', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO branch_history_entries (id, world_id, ordinal, source_key, message, audience, created_at) VALUES ('entry', 'branch', 1, 'source:message', '{\"text\":\"旧资料\"}', '{\"kind\":\"public\"}', CURRENT_TIMESTAMP)"
                )
            )
            before = conn.execute(text("SELECT * FROM branch_history_entries")).all()
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT * FROM branch_history_entries")).all() == before
    finally:
        engine.dispose()


def test_unknown_partial_archive_table_refuses_upgrade(tmp_path, monkeypatch):
    cfg, engine = config(tmp_path, monkeypatch)
    try:
        command.upgrade(cfg, "20261002_0018")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE branch_history_entries (id VARCHAR(48) PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="列形状不符"):
            command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20261002_0018"
            )
    finally:
        engine.dispose()
