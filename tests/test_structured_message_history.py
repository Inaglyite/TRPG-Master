from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from test_structured_material_library import library as library

from src.storage.database import EventOutbox, SaveSlot, WorldMember, WorldState, session_scope
from src.storage.persistence import load_game_artifacts, save_game
from src.structured.branch import restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.validation import validate_event
from src.web.structured_history_http import read_message_history, register_structured_history_routes


def publish(context, service, key, text, audience):
    with session_scope(context.database_url) as session:
        revision = session.get(WorldState, context.world_id).revision
    return service.execute_command(
        world_id=context.world_id,
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="publish_message",
        command_id=key,
        expected_revision=revision,
        payload={"speaker": {"kind": "keeper"}, "text": text, "audience": audience},
    )


def test_new_member_and_reconnect_restore_only_recipient_visible_narrative(library):
    context, deps, service = library
    publish(context, service, "public", "公开钟声", {"kind": "public"})
    publish(
        context,
        service,
        "private",
        "甲的私信",
        {"kind": "investigators", "investigator_ids": ["inv-alice"]},
    )
    publish(context, service, "secret", "主持幕后秘密", {"kind": "keeper"})
    with session_scope(context.database_url) as session:
        session.query(WorldMember).filter_by(user_id="u-bob").one().role = "viewer"
        before = copy.deepcopy(session.get(WorldState, context.world_id).state)
        revision_before = session.get(WorldState, context.world_id).revision
        events_before = session.query(EventOutbox).count()
    from src.structured.gateway import StructuredGateway

    gateway = StructuredGateway(context.database_url)
    for user, expected in [
        ("u-bob", ["公开钟声"]),
        ("u-alice", ["公开钟声", "甲的私信"]),
        ("u-keeper", ["公开钟声", "甲的私信", "主持幕后秘密"]),
    ]:
        snapshot = gateway.snapshot_envelope(world_id=context.world_id, user_id=user)
        validate_event(snapshot)
        assert [
            message["text"] for message in snapshot["payload"]["message_history"]["messages"]
        ] == expected
        assert "command_id" not in str(snapshot["payload"]["message_history"])
    assert read_message_history(deps, context.world_id, "outsider", 999) is None
    # Bootstrap registry is separate from narrative reads; read-only pages emit
    # no new rows and preserve world revision/state exactly.
    with session_scope(context.database_url) as session:
        current = session.get(WorldState, context.world_id)
        assert current.revision == revision_before
        assert session.query(EventOutbox).count() == events_before
        assert current.state == before


def test_message_pagination_is_chronological_nonoverlapping_and_new_messages_do_not_shift_it(
    library,
):
    context, deps, service = library
    for i in range(53):
        publish(context, service, f"public-{i}", f"公开 {i}", {"kind": "public"})
    snapshot = service.session_snapshot(
        world_id=context.world_id, principal=Principal(kind="viewer", user_id="u-bob")
    )
    recent = snapshot["message_history"]
    assert [m["text"] for m in recent["messages"]] == [f"公开 {i}" for i in range(3, 53)]
    publish(context, service, "latest", "新的叙事", {"kind": "public"})
    older = read_message_history(deps, context.world_id, "u-bob", recent["next_before_sequence"])
    assert [m["text"] for m in older["messages"]] == ["公开 0", "公开 1", "公开 2"]
    assert older["next_before_sequence"] is None
    assert not set(m["message_id"] for m in older["messages"]) & set(
        m["message_id"] for m in recent["messages"]
    )


def test_sparse_public_messages_are_found_across_private_pages(library):
    context, deps, service = library
    publish(context, service, "public-first", "公开旧叙事", {"kind": "public"})
    for i in range(105):
        publish(context, service, f"secret-{i}", f"秘密 {i}", {"kind": "keeper"})
    page = read_message_history(deps, context.world_id, "u-bob", 10000)
    assert [m["text"] for m in page["messages"]] == ["公开旧叙事"]
    assert page["next_before_sequence"] is None


def test_load_does_not_restore_narrative_from_the_rolled_back_future_at_the_same_revision(library):
    context, deps, service = library
    publish(context, service, "before-save", "存档之前的叙事", {"kind": "public"})
    save_game([], "slot_001", context=context)
    publish(context, service, "after-save", "读档应消除的未来叙事", {"kind": "public"})
    restore_structured_save(context, "slot_001")
    page = read_message_history(deps, context.world_id, "u-bob", 999)
    assert [message["text"] for message in page["messages"]] == ["存档之前的叙事"]


def test_structured_save_cursor_is_the_committed_event_sequence_and_invalid_cursor_fails_closed(
    library,
):
    context, _, service = library
    publish(context, service, "first", "存档时的叙事", {"kind": "public"})
    save_game([], "slot_001", context=context)
    _, snapshot, metadata = load_game_artifacts("slot_001", context=context)
    with session_scope(context.database_url) as session:
        events = session.query(EventOutbox).filter_by(world_id=context.world_id).all()
        assert metadata["structured_event_cursor"] == {
            "world_id": context.world_id,
            "revision": snapshot["revision"],
            "sequence": max(event.sequence for event in events),
        }
        before = copy.deepcopy(session.get(WorldState, context.world_id).state)
        slot = (
            session.query(SaveSlot).filter_by(world_id=context.world_id, slot_key="slot_001").one()
        )
        slot.metadata_json = {
            **slot.metadata_json,
            "structured_event_cursor": {
                **metadata["structured_event_cursor"],
                "world_id": "another-world",
            },
        }
    with pytest.raises(StructuredError, match="事件游标") as error:
        restore_structured_save(context, "slot_001")
    assert error.value.code == "invalid_action"
    with session_scope(context.database_url) as session:
        assert session.get(WorldState, context.world_id).state == before
        assert session.query(EventOutbox).count() == len(events)


def test_history_http_auth_cursor_validation_cache_and_membership_revocation(library, monkeypatch):
    context, deps, service = library
    publish(context, service, "one", "公开叙事", {"kind": "public"})
    monkeypatch.setenv("TRPG_REQUIRE_AUTH", "1")
    router = APIRouter()
    app = FastAPI()
    register_structured_history_routes(router, deps)
    app.include_router(router)
    with TestClient(app) as client:
        url = f"/api/worlds/{context.world_id}/narrative-history"
        with patch("src.web.structured_history_http.request_user", return_value=None):
            assert client.get(url, params={"before_sequence": 999}).status_code == 401
        with patch(
            "src.web.structured_history_http.request_user", return_value=SimpleNamespace(id="u-bob")
        ):
            assert client.get(url, params={"before_sequence": -1}).status_code == 422
            assert client.get(url, params={"before_sequence": "invalid"}).status_code == 422
            assert client.get(url, params={"before_sequence": 10**100}).status_code == 422
            response = client.get(url, params={"before_sequence": 999})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["vary"] == "Cookie"
            with session_scope(context.database_url) as session:
                session.query(WorldMember).filter_by(user_id="u-bob").delete()
            assert client.get(url, params={"before_sequence": 999}).status_code == 404
