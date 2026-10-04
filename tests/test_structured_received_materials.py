"""Recipient-only durable material catalogs, never the keeper author library."""

from __future__ import annotations

import copy

import pytest
from test_structured_commands import make_structured_world

from src.storage.database import WorldState, session_scope
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService
from src.structured.validation import validate_event


@pytest.fixture
def received_world(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["assets"] = {
            "asset_note": {"file": "private-note.png", "label": "只给爱丽丝的照片"},
            "author_only": {"file": "author-secret.webp", "label": "主持未出示的底牌"},
            "unsupported": {"file": "notes.txt", "label": "非图片"},
        }
        state["asset_grants"] = [
            {"asset_id": "asset_note", "investigator_id": "inv-alice", "by": "committed-1"},
            {"asset_id": "asset_note", "investigator_id": "inv-alice", "by": "committed-2"},
            {"asset_id": "unsupported", "investigator_id": "inv-alice"},
            {"asset_id": "missing", "investigator_id": "inv-alice"},
        ]
        row.state = state
    return context, StructuredPlayService(context.database_url)


def snapshot(context, service, principal):
    return service.session_snapshot(world_id=context.world_id, principal=principal)


def test_received_catalog_is_durable_recipient_only_and_schema_valid(received_world):
    context, service = received_world
    alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
    first = snapshot(context, service, alice)
    assert first["received_assets"] == [{"id": "asset_note", "label": "只给爱丽丝的照片"}]
    assert "private-note.png" not in str(first["received_assets"])
    assert "author_only" not in str(first)
    assert (
        snapshot(context, StructuredPlayService(context.database_url), alice)["received_assets"]
        == first["received_assets"]
    )
    validate_event(
        {
            "protocol_version": 1,
            "event_id": 0,
            "world_id": context.world_id,
            "sequence": 0,
            "revision": first["revision"],
            "cause_request_id": None,
            "type": "session_snapshot",
            "payload": first,
        }
    )


@pytest.mark.parametrize(
    "principal",
    [
        Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",)),
        Principal(kind="viewer", user_id="u-bob"),
        Principal(kind="player", user_id="u-owner"),
        Principal(kind="keeper", user_id="u-keeper"),
    ],
)
def test_other_player_viewer_owner_and_unclaimed_keeper_get_no_received_catalog(
    received_world, principal
):
    context, service = received_world
    assert snapshot(context, service, principal).get("received_assets") == []


def test_keeper_with_own_character_has_only_own_received_catalog(received_world):
    context, service = received_world
    keeper = Principal(kind="keeper", user_id="u-alice", investigator_ids=("inv-alice",))
    value = snapshot(context, service, keeper)
    assert value["received_assets"] == [{"id": "asset_note", "label": "只给爱丽丝的照片"}]
    assert "author_only" in str(value["keeper_assets"])
    assert "author_only" not in str(value["received_assets"])


def test_revoked_grants_clear_catalog_without_mutating_the_world(received_world):
    context, service = received_world
    alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
    assert snapshot(context, service, alice)["received_assets"]
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["asset_grants"] = []
        row.state = state
    assert snapshot(context, service, alice)["received_assets"] == []
    with session_scope(context.database_url) as session:
        assert session.get(WorldState, context.world_id).state["asset_grants"] == []


def test_agent_context_does_not_gain_ui_catalog(received_world):
    context, service = received_world
    assert "received_assets" not in snapshot(
        context, service, Principal(kind="agent", run_id="test")
    )
