from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
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
    RoomAction,
    SaveSlot,
    Snapshot,
    Turn,
    World,
    WorldMember,
    WorldState,
    get_engine,
    initialize_database,
    new_id,
    session_scope,
)
from src.database_store import DatabaseWorldStore
from src.database_turn_journal import DatabaseTurnJournal
from src.engine import GameEngine
from src.multiplayer import MultiplayerError, reserve_room_action
from src.turn_journal import TurnJournal
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


def test_multiplayer_pc_projection_commits_atomically_to_world_turn_and_save(
    tmp_path: Path,
):
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    world_id = "world-roster-atomic"
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="module-a"))
    store = DatabaseWorldStore(url, world_id, tmp_path / "worlds" / world_id)
    store.initialize(
        {
            "schema_version": 0,
            "revision": 0,
            "active_investigator_id": "inv-a",
            "investigators": {
                "inv-a": {
                    "investigator_id": "inv-a",
                    "name": "Alice",
                    "hp": 10,
                }
            },
            "pc": {
                "investigator_id": "inv-a",
                "name": "Alice",
                "hp": 10,
            },
        }
    )
    with patch.dict(
        os.environ,
        {
            "TRPG_DATABASE_URL": url,
            "TRPG_WRITE_COMPAT_EXPORTS": "0",
        },
    ):
        journal = DatabaseTurnJournal(
            tmp_path / "worlds" / world_id,
            world_id=world_id,
            module_name="module-a",
        )
        engine = GameEngine.__new__(GameEngine)
        engine.context = SimpleNamespace(world_store=store)
        engine.turn_journal = journal
        engine.messages = []
        engine._active_turn_id = journal.begin(
            kind="action",
            player_input="承受伤害",
        )
        engine._turn_performance = None
        engine._turn_diagnostics = []
        engine._turn_lore_diagnostics = {}
        engine._turn_mutations = SimpleNamespace(snapshot=lambda: {})
        engine.cb = SimpleNamespace(on_performance=lambda _metrics: None)

        with store.turn_cache():
            store.update(lambda state: state["pc"].update({"hp": 6}))
            engine._complete_turn_record(
                narrative="Alice 受伤。",
                choices=[],
                executed_tools=[],
                lore_entry_ids=[],
            )

    with session_scope(url) as session:
        world_state = session.get(WorldState, world_id).state
        turn = session.query(Turn).filter_by(world_id=world_id).one()
        turn_snapshot = session.get(Snapshot, turn.snapshot_id).state
        save = session.query(SaveSlot).filter_by(
            world_id=world_id,
            slot_key="slot_000",
        ).one()
        save_snapshot = session.get(Snapshot, save.snapshot_id).state
    for state in (world_state, turn_snapshot, save_snapshot):
        assert state["pc"]["hp"] == 6
        assert state["investigators"]["inv-a"]["hp"] == 6


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
        "room_actions",
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


def test_initial_alembic_revision_is_frozen_before_multiplayer_tables(tmp_path: Path):
    """An old production database must see the historical 0001 schema only."""
    url = sqlite_url(tmp_path)
    env = {**os.environ, "TRPG_DATABASE_URL": url}
    root = Path(__file__).resolve().parent.parent
    alembic = str(root / ".venv/bin/alembic")
    subprocess.run(
        [alembic, "upgrade", "20260722_0001"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    tables_at_0001 = set(inspect(get_engine(url)).get_table_names())
    assert "world_members" in tables_at_0001
    assert {
        "world_invites",
        "world_investigators",
        "room_actions",
    }.isdisjoint(tables_at_0001)

    subprocess.run(
        [alembic, "upgrade", "head"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    tables_at_head = set(inspect(get_engine(url)).get_table_names())
    assert {
        "world_invites",
        "world_investigators",
        "room_actions",
    } <= tables_at_head


def test_room_action_migration_fails_closed_for_legacy_accepted_rows(tmp_path: Path):
    url = sqlite_url(tmp_path)
    env = {**os.environ, "TRPG_DATABASE_URL": url}
    root = Path(__file__).resolve().parent.parent
    alembic = str(root / ".venv/bin/alembic")
    subprocess.run(
        [alembic, "upgrade", "20260722_0004"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    owner = create_user(url, "legacy_owner", "legacy password 123")
    with session_scope(url) as session:
        session.add(
            World(id="legacy-action-world", module_name="legacy-module")
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="legacy-action-world",
                user_id=owner.id,
                role="owner",
            )
        )
        session.add(
            RoomAction(
                id=new_id("room_action"),
                world_id="legacy-action-world",
                action_id="already-executed",
                submitted_by=owner.id,
                action_type="action",
                status="accepted",
            )
        )
    subprocess.run(
        [alembic, "upgrade", "head"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with session_scope(url) as session:
        assert (
            session.query(RoomAction)
            .filter_by(world_id="legacy-action-world", action_id="already-executed")
            .one()
            .status
            == "completed"
        )
    with pytest.raises(MultiplayerError) as duplicate:
        reserve_room_action(
            url,
            "legacy-action-world",
            "already-executed",
            owner.id,
            "action",
        )
    assert duplicate.value.code == "duplicate_action"


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


def test_legacy_world_import_resumes_missing_artifacts_and_preserves_turn_order(
    tmp_path: Path,
):
    world_dir = tmp_path / "worlds" / "legacy-world"
    slot = world_dir / "saves" / "slot_001"
    slot.mkdir(parents=True)
    (world_dir / "world.json").write_text(json.dumps({"module_name": "legacy-module"}))
    state = {"schema_version": 0, "revision": 4, "pc": {"hp": 7}}
    (world_dir / "world_state.json").write_text(json.dumps(state))
    (slot / "messages.json").write_text(json.dumps([]))
    (slot / "snapshot.json").write_text(json.dumps(state))

    journal = TurnJournal(
        world_dir,
        world_id="legacy-world",
        module_name="legacy-module",
    )
    older = journal.begin(kind="action", player_input="先调查书桌")
    journal.complete(
        older,
        messages=[],
        world_state=state,
        narrative="先",
        choices=[],
    )
    newer = journal.begin(kind="action", player_input="再调查窗户")
    journal.complete(
        newer,
        messages=[],
        world_state=state,
        narrative="后",
        choices=[],
    )
    for turn_id, completed_at in (
        (older, "2026-01-01T00:00:00+00:00"),
        (newer, "2026-01-02T00:00:00+00:00"),
    ):
        path = world_dir / "turns" / turn_id / "record.json"
        record = json.loads(path.read_text())
        record["completed_at"] = completed_at
        path.write_text(json.dumps(record))

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    # Simulate a previously interrupted importer that committed only the world.
    with session_scope(url) as session:
        session.add(World(id="legacy-world", module_name="legacy-module"))
        session.add(
            WorldState(
                world_id="legacy-world",
                schema_version=0,
                revision=4,
                state=state,
            )
        )

    resumed = import_world(world_dir, url, None, replace=False)
    assert resumed["status"] == "imported"
    assert resumed["saves"] == 1
    assert resumed["turns"] == 2
    with session_scope(url) as session:
        newest = (
            session.query(Turn)
            .filter_by(world_id="legacy-world")
            .order_by(Turn.completed_at.desc())
            .first()
        )
        assert newest.id == newer


def test_legacy_world_import_rejects_broken_save_before_database_write(tmp_path: Path):
    world_dir = tmp_path / "worlds" / "broken-world"
    slot = world_dir / "saves" / "slot_001"
    slot.mkdir(parents=True)
    (world_dir / "world.json").write_text(json.dumps({"module_name": "legacy-module"}))
    (world_dir / "world_state.json").write_text(
        json.dumps({"schema_version": 0, "revision": 0})
    )
    (slot / "messages.json").write_text("{broken")
    (slot / "snapshot.json").write_text(json.dumps({"revision": 0}))
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))

    with pytest.raises(ValueError, match="slot_001"):
        import_world(world_dir, url, None, replace=False)
    with session_scope(url) as session:
        assert session.get(World, "broken-world") is None


def test_once_import_marker_never_replaces_newer_database_state(tmp_path: Path):
    world_dir = tmp_path / "worlds" / "legacy-once"
    world_dir.mkdir(parents=True)
    (world_dir / "world.json").write_text(
        json.dumps({"module_name": "legacy-module"})
    )
    source_state = {"schema_version": 0, "revision": 1, "pc": {"hp": 7}}
    state_path = world_dir / "world_state.json"
    state_path.write_text(json.dumps(source_state))
    url = sqlite_url(tmp_path)
    command = [
        str(Path(".venv/bin/python")),
        "tools/import_worlds_to_database.py",
        "--runtime-root",
        str(tmp_path),
        "--database-url",
        url,
        "--once",
        "--replace",
    ]
    root = Path(__file__).resolve().parent.parent
    subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    with session_scope(url) as session:
        row = session.get(WorldState, "legacy-once")
        row.state = {**row.state, "pc": {"hp": 99}}
        row.revision = 9
    # Compatibility exports can legitimately change after the database became
    # authoritative. The marker, not a mutable directory fingerprint, is final.
    state_path.write_text(
        json.dumps({"schema_version": 0, "revision": 2, "pc": {"hp": 1}})
    )
    second = subprocess.run(
        command,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(second.stdout)["status"] == "already_imported"
    with session_scope(url) as session:
        row = session.get(WorldState, "legacy-once")
        assert row.revision == 9
        assert row.state["pc"]["hp"] == 99


def test_legacy_import_rejects_corrupt_turn_record_instead_of_silently_skipping(
    tmp_path: Path,
):
    world_dir = tmp_path / "worlds" / "legacy-corrupt-turn"
    turn_dir = world_dir / "turns" / "turn_corrupt"
    turn_dir.mkdir(parents=True)
    (world_dir / "world.json").write_text(
        json.dumps({"module_name": "legacy-module"})
    )
    (world_dir / "world_state.json").write_text(
        json.dumps({"schema_version": 0, "revision": 0})
    )
    (turn_dir / "record.json").write_text("{broken")
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))

    with pytest.raises(ValueError, match="record.json"):
        import_world(world_dir, url, None, replace=False)
    with session_scope(url) as session:
        assert session.get(World, "legacy-corrupt-turn") is None


def test_legacy_import_refuses_to_create_a_second_owner(tmp_path: Path):
    world_dir = tmp_path / "worlds" / "owned-world"
    world_dir.mkdir(parents=True)
    (world_dir / "world.json").write_text(
        json.dumps({"module_name": "legacy-module"})
    )
    (world_dir / "world_state.json").write_text(
        json.dumps({"schema_version": 0, "revision": 0})
    )
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    first = create_user(url, "first_owner", "first password 123")
    second = create_user(url, "second_owner", "second password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="owned-world",
                module_name="legacy-module",
                created_by=first.id,
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id="owned-world",
                user_id=first.id,
                role="owner",
            )
        )

    with pytest.raises(ValueError, match="其他房主"):
        import_world(world_dir, url, second, replace=True)
    with session_scope(url) as session:
        owners = (
            session.query(WorldMember)
            .filter_by(world_id="owned-world", role="owner")
            .all()
        )
        assert [member.user_id for member in owners] == [first.id]


def test_replacing_legacy_artifacts_does_not_leave_orphan_snapshots(tmp_path: Path):
    world_dir = tmp_path / "worlds" / "legacy-replace"
    slot = world_dir / "saves" / "slot_001"
    slot.mkdir(parents=True)
    (world_dir / "world.json").write_text(
        json.dumps({"module_name": "legacy-module"})
    )
    state = {"schema_version": 0, "revision": 1, "pc": {"hp": 7}}
    (world_dir / "world_state.json").write_text(json.dumps(state))
    (slot / "messages.json").write_text(json.dumps([]))
    (slot / "snapshot.json").write_text(json.dumps(state))
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))

    import_world(world_dir, url, None, replace=True)
    import_world(world_dir, url, None, replace=True)
    with session_scope(url) as session:
        assert session.query(Snapshot).filter_by(world_id="legacy-replace").count() == 1


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
                "TRPG_ROOM_IDLE_SECONDS": "0",
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
            with pytest.raises(WebSocketDisconnect) as legacy_denied:
                with owner_client.websocket_connect(
                    f"/ws?world_id={world_id}",
                    headers={
                        "origin": "https://testserver",
                        "cookie": f"trpg_session={owner_cookie}",
                    },
                ):
                    pass
            assert legacy_denied.value.code == 4409
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
                    f"/ws/room?world_id={world_id}",
                    headers={
                        "origin": "https://testserver",
                        "cookie": f"trpg_session={stranger_cookie}",
                    },
                ):
                    pass
            assert denied.value.code in {4403, 1000}


def test_cloud_authoring_endpoints_require_configured_admin(tmp_path: Path):
    import server

    url = sqlite_url(tmp_path)
    initialize_database(url)
    env = {
        "TRPG_DATABASE_URL": url,
        "TRPG_REQUIRE_AUTH": "1",
        "TRPG_ALLOW_REGISTRATION": "1",
        "TRPG_ALLOWED_ORIGINS": "https://testserver",
        "TRPG_ADMIN_USERS": "",
    }
    with (
        patch.dict(os.environ, env),
        patch.object(server, "DATABASE_URL", url),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "module_author",
                "password": "author password 123",
            },
        )
        assert registered.status_code == 201
        assert client.get("/api/editor/projects").status_code == 403
        denied = client.post(
            "/api/modules/compile",
            json={},
            headers={"origin": "https://testserver"},
        )
        assert denied.status_code == 403

        with patch.dict(os.environ, {"TRPG_ADMIN_USERS": "module_author"}):
            assert client.get("/api/editor/projects").status_code == 200
