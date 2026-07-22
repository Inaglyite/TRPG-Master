from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from starlette.websockets import WebSocketDisconnect

from src.auth import (
    authenticate,
    authorize_world,
    create_login_session,
    create_user,
    resolve_session,
    revoke_session,
)
from src.database import (
    Base,
    SaveSlot,
    World,
    WorldMember,
    WorldState,
    get_engine,
    initialize_database,
    new_id,
    session_scope,
)
from src.database_store import DatabaseWorldStore
from src.world_store import StaleRevisionError
from tools.import_worlds_to_database import import_world


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def seed_world(url: str, world_id: str = "world-a") -> DatabaseWorldStore:
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="module-a"))
    store = DatabaseWorldStore(url, world_id, Path("/unused") / world_id)
    store.initialize({"schema_version": 0, "revision": 0, "pc": {"hp": 10}})
    return store


def test_database_world_store_revision_and_json_state(tmp_path: Path):
    url = sqlite_url(tmp_path)
    store = seed_world(url)
    snapshot = store.update(lambda state: state["pc"].update({"hp": 8}), expected_revision=0)
    assert snapshot.revision == 1
    assert store.load()["pc"]["hp"] == 8
    with pytest.raises(StaleRevisionError):
        store.update(lambda state: state, expected_revision=0)
    with session_scope(url) as session:
        assert session.get(WorldState, "world-a").state["pc"]["hp"] == 8


def test_argon2_session_is_hashed_revocable_and_authorized(tmp_path: Path):
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    user = create_user(url, "Keeper_01", "a sufficiently long password")
    assert authenticate(url, "keeper_01", "wrong password") is None
    assert authenticate(url, "keeper_01", "a sufficiently long password").id == user.id
    token = create_login_session(url, user)
    assert resolve_session(url, token).id == user.id
    with session_scope(url) as session:
        world = World(id="private-world", module_name="module-a", created_by=user.id)
        session.add(world)
        session.add(
            WorldMember(id=new_id("member"), world_id=world.id, user_id=user.id, role="owner")
        )
    assert authorize_world(url, user.id, "private-world", "manage") == "owner"
    revoke_session(url, token)
    assert resolve_session(url, token) is None


def test_alembic_upgrade_creates_complete_schema(tmp_path: Path):
    url = sqlite_url(tmp_path)
    env = {**os.environ, "TRPG_DATABASE_URL": url}
    subprocess.run(
        [str(Path(".venv/bin/alembic")), "upgrade", "head"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    tables = set(inspect(get_engine(url)).get_table_names())
    assert {
        "users",
        "sessions",
        "worlds",
        "world_members",
        "world_invites",
        "world_investigators",
        "world_states",
        "snapshots",
        "turns",
        "turn_events",
        "model_calls",
        "save_slots",
        "player_notes",
        "audit_events",
        "alembic_version",
    } <= tables


def test_legacy_world_import_is_idempotent(tmp_path: Path):
    world_dir = tmp_path / "worlds" / "legacy-world"
    slot = world_dir / "saves" / "slot_000"
    slot.mkdir(parents=True)
    (world_dir / "world.json").write_text(json.dumps({"module_name": "legacy-module"}))
    state = {"schema_version": 0, "revision": 4, "pc": {"hp": 7}}
    (world_dir / "world_state.json").write_text(json.dumps(state))
    (slot / "messages.json").write_text(json.dumps([{"role": "assistant", "content": "old"}]))
    (slot / "snapshot.json").write_text(json.dumps(state))
    (slot / "meta.json").write_text(json.dumps({"label": "旧档"}))
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    assert import_world(world_dir, url, None, replace=False)["status"] == "imported"
    assert import_world(world_dir, url, None, replace=False)["status"] == "skipped"
    with session_scope(url) as session:
        assert session.get(WorldState, "legacy-world").state["pc"]["hp"] == 7
        save = session.query(SaveSlot).filter_by(world_id="legacy-world").one()
        assert save.metadata_json["label"] == "旧档"


def test_http_accounts_and_world_ownership_gate_websocket(tmp_path: Path):
    import server

    url = sqlite_url(tmp_path)
    initialize_database(url)
    with (
        patch.dict(
            os.environ,
            {
                "TRPG_DATABASE_URL": url,
                "TRPG_REQUIRE_AUTH": "1",
                "TRPG_ALLOW_REGISTRATION": "1",
                "TRPG_ALLOWED_ORIGINS": "https://testserver",
                "TRPG_WRITE_COMPAT_EXPORTS": "0",
            },
        ),
        patch.object(server, "DATABASE_URL", url),
        patch("src.engine.API_KEY", "test-api-key"),
    ):
        with TestClient(server.app, base_url="https://testserver") as owner_client:
            response = owner_client.post(
                "/api/auth/register",
                json={
                    "username": "owner01",
                    "password": "owner password 123",
                },
            )
            assert response.status_code == 201
            created = owner_client.post(
                "/api/worlds",
                json={"module": "mansion_of_madness"},
                headers={"origin": "https://testserver"},
            )
            assert created.status_code == 201
            world_id = created.json()["world_id"]
            owner_cookie = owner_client.cookies.get("trpg_session")
            with owner_client.websocket_connect(
                f"/ws?world_id={world_id}",
                headers={
                    "origin": "https://testserver",
                    "cookie": f"trpg_session={owner_cookie}",
                },
            ) as websocket:
                assert websocket.receive_json()["type"] == "module_list"

        with TestClient(server.app, base_url="https://testserver") as stranger_client:
            assert (
                stranger_client.post(
                    "/api/auth/register",
                    json={
                        "username": "stranger01",
                        "password": "stranger password 123",
                    },
                ).status_code
                == 201
            )
            stranger_cookie = stranger_client.cookies.get("trpg_session")
            with pytest.raises(WebSocketDisconnect) as denied:
                with stranger_client.websocket_connect(
                    f"/ws?world_id={world_id}",
                    headers={
                        "origin": "https://testserver",
                        "cookie": f"trpg_session={stranger_cookie}",
                    },
                ):
                    pass
            assert denied.value.code in {4403, 1000}
