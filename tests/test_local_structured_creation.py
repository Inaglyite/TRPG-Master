"""Real local creation and durable idempotency; no model or user save changes."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai.model.route_service import RouteNotConfiguredError
from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.gameplay.characters import default_character_ref
from src.multiplayer.ws_router import WsMessageRouter
from src.storage.database import World, WorldMember, WorldState, session_scope
from src.storage.world_branches import WorldBranchService
from src.structured.bootstrap import LOCAL_OPERATOR_USER_ID
from src.structured.engine_gate import build_engine_client
from src.structured.errors import StructuredError
from src.structured.local_creation import (
    can_resume_structured,
    create_local_world,
    register_local_start,
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.delenv("TRPG_DATABASE_URL", raising=False)
    return RuntimeContext.create(
        "source-local", "猩红文档", project_root=PROJECT_ROOT, runtime_root=tmp_path
    )


def request(source, **changes):
    return {
        "request_id": "local-create-once",
        "source_world_id": source.world_id,
        "execution_profile": "structured_v1",
        "keeper_mode": "human",
        "character_ref": default_character_ref(source.module_name, context=source),
        **changes,
    }


def test_unconfigured_menu_client_does_not_construct_sdk_or_call_model(source, monkeypatch):
    sdk = Mock(side_effect=AssertionError("SDK must not be constructed without a key"))
    monkeypatch.setattr("src.structured.engine_gate.OpenAI", sdk)
    client = build_engine_client(source, api_key="", base_url="http://127.0.0.1:1", timeout=1)
    sdk.assert_not_called()
    with pytest.raises(RouteNotConfiguredError, match="人类主持"):
        _ = client.chat
    human = create_local_world(source, request(source)).context
    assert build_engine_client(human, api_key="", base_url="http://127.0.0.1:1", timeout=1) is None


def test_creation_has_selected_pc_authorized_local_keeper_and_does_not_touch_source(source):
    original = copy.deepcopy(source.world_store.load())
    result = create_local_world(source, request(source))
    assert result.context.world_id != source.world_id
    assert not result.replayed
    assert source.world_store.load() == original
    state = result.context.world_store.load()
    assert state["pc"]["character_source"]
    assert "item_registry" in state
    with session_scope(source.database_url) as session:
        world = session.get(World, result.context.world_id)
        assert world.metadata_json["execution_profile"] == "structured_v1"
        assert world.metadata_json["keeper_mode"] == "human"
        assert world.root_world_id == world.id
        member = (
            session.query(WorldMember)
            .filter_by(world_id=world.id, user_id=LOCAL_OPERATOR_USER_ID)
            .one()
        )
        assert member.can_keeper and member.role == "owner"


def test_same_request_replay_preserves_progress_even_after_reconnect(source):
    payload = request(source)
    first = create_local_world(source, payload)
    first.context.world_store.update(lambda state: state.update({"custom_progress": "keep"}))
    replay = create_local_world(first.context, payload)
    assert replay.replayed
    assert replay.context.world_id == first.context.world_id
    assert replay.context.world_store.load()["custom_progress"] == "keep"
    with session_scope(source.database_url) as session:
        assert session.query(World).count() == 2


def test_created_human_world_is_not_an_unused_placeholder(source):
    created = create_local_world(source, request(source)).context
    branches = WorldBranchService(source.project_root, source.runtime_root)
    assert not branches.is_tree_untouched(created.world_id)
    adventures = branches.list_adventures(
        module_name=source.module_name, active_world_id=created.world_id
    )
    assert any(entry["root_world_id"] == created.world_id for entry in adventures)
    assert can_resume_structured(created)
    assert not can_resume_structured(source)


def test_same_request_with_changed_mode_is_rejected_without_changes(source):
    create_local_world(source, request(source))
    with pytest.raises(StructuredError, match="更换内容"):
        create_local_world(source, request(source, keeper_mode="agent"))
    with session_scope(source.database_url) as session:
        assert session.query(World).count() == 2


@pytest.mark.parametrize(
    "change",
    [
        {"source_world_id": "stale"},
        {"request_id": "../bad"},
        {"keeper_mode": "pretend"},
        {"execution_profile": "pretend"},
        {"character_ref": {"source": "module", "file": "missing.json"}},
    ],
)
def test_invalid_creation_creates_no_new_world_and_preserves_source(source, change):
    original = copy.deepcopy(source.world_store.load())
    with pytest.raises(StructuredError):
        create_local_world(source, request(source, **change))
    assert source.world_store.load() == original
    with session_scope(source.database_url) as session:
        assert session.query(World).count() == 1
        assert session.query(WorldState).count() == 1


def wired_router(source, *, allowed=True, acquired=True):
    engine = SimpleNamespace(context=source, client=None)

    def switch(context):
        engine.context = context

    engine.switch_context = Mock(side_effect=switch)
    wire = SimpleNamespace(outbound=SimpleNamespace(send=AsyncMock()), send_snapshot=AsyncMock())
    hooks = SimpleNamespace(
        reserve=AsyncMock(return_value=acquired), release=Mock(), activate=Mock(), save=AsyncMock()
    )
    router = WsMessageRouter()
    register_local_start(
        router,
        engine,
        wire,
        allow_local=lambda: allowed,
        reserve=hooks.reserve,
        release=hooks.release,
        activate=hooks.activate,
        context_payload=lambda: {"type": "world_context", "world_id": engine.context.world_id},
        list_payload=lambda: {"type": "world_list"},
        save_panels=hooks.save,
    )
    return router, engine, wire, hooks


def test_nonlocal_creation_is_denied_before_lock_or_database_write(source):
    router, engine, wire, hooks = wired_router(source, allowed=False)
    asyncio.run(router.dispatch({"type": "local_start", **request(source)}))
    hooks.reserve.assert_not_called()
    engine.switch_context.assert_not_called()
    assert wire.outbound.send.call_args.args[0]["code"] == "not_authorized"
    with session_scope(source.database_url) as session:
        assert session.query(World).count() == 1


def test_human_creation_switches_context_before_receipt_and_never_dispatches_legacy_opening(source):
    router, engine, wire, hooks = wired_router(source)
    legacy = AsyncMock()
    router.add("start", legacy)
    asyncio.run(router.dispatch({"type": "local_start", **request(source)}))
    legacy.assert_not_called()
    hooks.release.assert_called_once()
    hooks.activate.assert_called_once_with(engine.context)
    messages = [call.args[0] for call in wire.outbound.send.call_args_list]
    assert [item["type"] for item in messages] == [
        "world_context",
        "world_list",
        "local_start_result",
    ]
    assert messages[0]["world_id"] == messages[2]["world_id"] == engine.context.world_id
    assert messages[2]["ok"] is True
    wire.send_snapshot.assert_awaited_once()
    hooks.save.assert_awaited_once()


def test_rejected_creation_releases_lease_and_leaves_active_source_unchanged(source):
    router, engine, wire, hooks = wired_router(source)
    asyncio.run(
        router.dispatch({"type": "local_start", **request(source, source_world_id="wrong")})
    )
    engine.switch_context.assert_not_called()
    hooks.release.assert_called_once()
    wire.send_snapshot.assert_not_called()
    assert wire.outbound.send.call_args.args[0]["code"] == "world_mismatch"
    assert engine.context is source


def test_busy_connection_does_not_create_or_emit_success(source):
    router, engine, wire, hooks = wired_router(source, acquired=False)
    asyncio.run(router.dispatch({"type": "local_start", **request(source)}))
    engine.switch_context.assert_not_called()
    wire.outbound.send.assert_not_called()
    hooks.release.assert_not_called()
    with session_scope(source.database_url) as session:
        assert session.query(World).count() == 1
