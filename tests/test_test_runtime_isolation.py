"""Guard pytest's implicit runtime against writes to a developer checkout."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app.engine import GameEngine
from src.storage.database import database_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_server_runtime_and_database_are_test_isolated(
    isolated_test_runtime_root: Path,
) -> None:
    """A bare TestClient/server import must not inherit project-root storage."""
    import server

    expected_database_url = f"sqlite:///{isolated_test_runtime_root / 'trpg-master.db'}"

    assert isolated_test_runtime_root != PROJECT_ROOT
    assert server.RUNTIME_ROOT == isolated_test_runtime_root
    assert server._active_context.runtime_root == isolated_test_runtime_root
    assert server.DATABASE_URL == expected_database_url
    assert database_url() == expected_database_url
    assert str(PROJECT_ROOT / "trpg-master.db") not in server.DATABASE_URL

    with TestClient(server.app) as client:
        assert client.get("/api/health").status_code == 200

    with patch("src.app.engine.OpenAI", return_value=object()):
        engine = GameEngine()
    assert engine.context.runtime_root == isolated_test_runtime_root
    assert engine.turn_journal.database_url == expected_database_url
