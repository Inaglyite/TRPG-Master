"""Character JSONB alignment must not rebuild or alter SQLite card data."""

import json

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src.app.config import PROJECT_ROOT


def test_sqlite_character_card_survives_jsonb_alignment(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cards.db'}"
    monkeypatch.setenv("TRPG_DATABASE_URL", url)
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "20260923_0017")
    engine = create_engine(url)
    payload = json.dumps({"name": "测试", "inventory": [{"name": "手电", "count": 2}]})
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO character_library_entries "
                "(id, owner_user_id, name, card_json, created_at, updated_at) "
                "VALUES ('card-test', '', '测试', :card, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"card": payload},
        )
        before = connection.execute(text("SELECT * FROM character_library_entries")).all()
    try:
        for operation, revision in (
            (command.upgrade, "20261002_0018"),
            (command.downgrade, "20260923_0017"),
            (command.upgrade, "20261002_0018"),
        ):
            operation(cfg, revision)
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT * FROM character_library_entries")).all()
                    == before
                )
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == revision
                )
            columns = {
                c["name"]: c for c in inspect(engine).get_columns("character_library_entries")
            }
            assert str(columns["card_json"]["type"]) == "JSON"
    finally:
        engine.dispose()
