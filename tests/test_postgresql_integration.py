"""Opt-in integration checks against a real PostgreSQL database."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

from src.auth import create_user
from src.config import PROJECT_ROOT
from src.database import (
    SaveSlot,
    Snapshot,
    World,
    WorldMember,
    WorldState,
    get_engine,
    new_id,
    session_scope,
)
from src.multiplayer import (
    MultiplayerError,
    accept_invite,
    claim_investigator,
    create_invite,
    reserve_room_action,
)
from src.persistence import save_game
from src.runtime import RuntimeContext
from tools.import_worlds_to_database import import_world

POSTGRES_URL = os.environ.get("TRPG_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set TRPG_TEST_POSTGRES_URL to run PostgreSQL integration tests",
)


def test_postgresql_jsonb_membership_and_room_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPG_DATABASE_URL", POSTGRES_URL)
    suffix = secrets.token_hex(5)
    owner = create_user(POSTGRES_URL, f"pg_owner_{suffix}", "owner password 123")
    player = create_user(POSTGRES_URL, f"pg_player_{suffix}", "player password 123")
    world_id = f"pg-world-{suffix}"

    context = RuntimeContext.create(
        world_id,
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    with session_scope(POSTGRES_URL) as session:
        world = session.get(World, world_id)
        assert world is not None
        world.created_by = owner.id
        world.metadata_json = {"name": "PostgreSQL 联调房", "max_players": 2}
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id=world_id,
                user_id=owner.id,
                role="owner",
            )
        )

    context.world_store.update(
        lambda state: state.update(
            {
                "flags": {"postgres_roundtrip": True},
                "nested_payload": {"items": [1, {"name": "深层 JSONB"}]},
            }
        )
    )
    stored = context.world_store.load()
    assert stored["flags"]["postgres_roundtrip"] is True
    assert stored["nested_payload"]["items"][1]["name"] == "深层 JSONB"
    save_game(
        [{"role": "assistant", "content": "PostgreSQL 存档"}],
        "slot_001",
        context=context,
    )
    with session_scope(POSTGRES_URL) as session:
        save = (
            session.query(SaveSlot)
            .filter_by(world_id=world_id, slot_key="slot_001")
            .one()
        )
        assert session.get(Snapshot, save.snapshot_id) is not None

    invite = create_invite(POSTGRES_URL, world_id, owner.id, max_uses=1)
    joined = accept_invite(POSTGRES_URL, invite["token"], player.id)
    assert joined == {"world_id": world_id, "role": "player", "already_member": False}
    claim = claim_investigator(
        POSTGRES_URL,
        world_id,
        f"character-{suffix}",
        player.id,
        character_ref={"type": "inline", "data": {"name": "PG 调查员"}},
    )
    assert claim["user_id"] == player.id

    reserve_room_action(POSTGRES_URL, world_id, "same-action", player.id, "action")
    with pytest.raises(MultiplayerError) as duplicate:
        reserve_room_action(POSTGRES_URL, world_id, "same-action", player.id, "action")
    assert duplicate.value.code == "duplicate_action"

    engine = get_engine(POSTGRES_URL)
    assert engine.dialect.name == "postgresql"
    columns = {column["name"]: column for column in inspect(engine).get_columns("world_states")}
    assert isinstance(columns["state"]["type"], JSONB)
    with session_scope(POSTGRES_URL) as session:
        row = session.get(WorldState, world_id)
        assert row is not None
        assert row.revision >= 1


def test_postgresql_migration_schema_matches_orm() -> None:
    env = {
        **os.environ,
        "TRPG_DATABASE_URL": POSTGRES_URL,
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_postgresql_legacy_import_with_save_and_owner(tmp_path: Path) -> None:
    suffix = secrets.token_hex(5)
    owner = create_user(
        POSTGRES_URL,
        f"pg_import_owner_{suffix}",
        "owner password 123",
    )
    world_dir = tmp_path / f"pg-import-{suffix}"
    slot_dir = world_dir / "saves" / "slot_001"
    slot_dir.mkdir(parents=True)
    state = {"schema_version": 0, "revision": 3, "pc": {"hp": 7}}
    (world_dir / "world.json").write_text(
        json.dumps({"module_name": "mansion_of_madness"})
    )
    (world_dir / "world_state.json").write_text(json.dumps(state))
    (slot_dir / "messages.json").write_text("[]")
    (slot_dir / "snapshot.json").write_text(json.dumps(state))

    result = import_world(
        world_dir,
        POSTGRES_URL,
        owner,
        replace=False,
    )
    assert result["status"] == "imported"
    with session_scope(POSTGRES_URL) as session:
        world = session.get(World, world_dir.name)
        assert world is not None
        assert world.created_by == owner.id
        save = (
            session.query(SaveSlot)
            .filter_by(world_id=world_dir.name, slot_key="slot_001")
            .one()
        )
        assert session.get(Snapshot, save.snapshot_id) is not None
