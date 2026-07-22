"""Opt-in integration checks against a real PostgreSQL database."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

from src.auth import create_user
from src.config import PROJECT_ROOT
from src.database import World, WorldMember, WorldState, get_engine, new_id, session_scope
from src.multiplayer import (
    MultiplayerError,
    accept_invite,
    claim_investigator,
    create_invite,
    reserve_room_action,
)
from src.runtime import RuntimeContext

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
