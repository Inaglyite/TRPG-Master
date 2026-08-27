"""H2 GameEngine shadow coordinator invariants.

These tests stay below transports: the context timeline is private shadow
state, ``messages`` remains authoritative, and a mismatch may never mint a
checkpoint that claims the two surfaces agree.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai.context import context_shadow as shadow_adapter
from src.ai.context.context_checkpoint import ContextCheckpoint
from src.ai.context.context_events import (
    EVENT_REQUEST_PATCH,
    ContextEventStore,
    messages_digest,
)
from src.ai.context.context_shadow import ContextShadowCoordinator
from src.ai.context.history_compactor import HistoryCompactor
from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.storage.database import (
    Base,
    ContextSession,
    ModelContextEvent,
    SaveSlot,
    Turn,
    World,
    get_engine,
    new_id,
    session_scope,
)
from src.storage.persistence import list_saves, load_game_artifacts


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'context-shadow.db'}"


def _coordinator(tmp_path: Path) -> ContextShadowCoordinator:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="world-shadow", module_name="module-a"))
    return ContextShadowCoordinator(
        SimpleNamespace(world_id="world-shadow", database_url=url)
    )


def _prepared(request_id: str, messages: list[dict], *, step: int = 1):
    return SimpleNamespace(
        messages=messages,
        request_envelope={
            "request_id": request_id,
            "profile": "story:test",
            "step": step,
            "model": "test-model",
            "message_digest": messages_digest(messages),
            "tool_catalog_digest": "0" * 64,
            "context_section_digests": {},
            "sections": [],
        },
    )


def _game_engine(tmp_path: Path, world_id: str = "engine-shadow") -> GameEngine:
    context = RuntimeContext.create(
        world_id,
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    with patch("src.app.engine.OpenAI", return_value=object()):
        engine = GameEngine(context)
    engine.prepare_session()
    return engine


def _patch_payload(store: ContextEventStore, session_id: str, request_id: str) -> dict:
    with session_scope(store.url) as session:
        row = (
            session.query(ModelContextEvent)
            .filter_by(
                session_id=session_id,
                event_type=EVENT_REQUEST_PATCH,
                source_id=request_id,
            )
            .one()
        )
        return dict(row.payload or {})


def test_mismatch_never_mints_checkpoint(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    original = [
        {"role": "system", "content": "keeper"},
        {"role": "user", "content": "open the door"},
    ]
    assert coordinator.ensure_bound(original)
    assert coordinator.current_checkpoint(original) is not None

    rewritten = [
        {"role": "system", "content": "different keeper"},
        {"role": "user", "content": "open the door"},
    ]
    assert coordinator.current_checkpoint(rewritten) is None
    assert coordinator.diagnostics[-1] == {
        "operation": "sync_messages",
        "error": "surface_mismatch",
    }


def test_request_patch_identity_and_system_replacement_replay(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    base = [
        {"role": "system", "content": "keeper"},
        {"role": "user", "content": "listen"},
    ]
    assert coordinator.ensure_bound(base)
    session_id = coordinator.store.session_for_world("world-shadow")["id"]

    assert coordinator.record_request(
        _prepared("request-identity", base),
        base_messages=base,
    )
    identity = _patch_payload(coordinator.store, session_id, "request-identity")
    assert identity["mode"] == "identity"
    assert "messages" not in identity
    assert coordinator.store.replay_request_messages(session_id, "request-identity") == base

    overlaid = [
        {"role": "system", "content": "keeper\n\n---\n\ncombat rules"},
        base[1],
    ]
    assert coordinator.record_request(
        _prepared("request-overlay", overlaid, step=2),
        base_messages=base,
        system_overlay="combat rules",
    )
    patch = _patch_payload(coordinator.store, session_id, "request-overlay")
    assert patch["mode"] == "replace_indices"
    assert patch["replacements"] == [{"index": 0, "message": overlaid[0]}]
    assert "messages" not in patch
    assert coordinator.store.replay_request_messages(session_id, "request-overlay") == overlaid


def test_request_replace_all_replays_normalized_effective_messages(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    base = [
        {"role": "system", "content": "keeper"},
        {"role": "user", "content": "old history"},
        {"role": "assistant", "content": "old answer"},
    ]
    override = [
        {"role": "system", "content": "rewrite contract"},
        {"role": "user", "content": "rewrite payload"},
    ]
    assert coordinator.ensure_bound(base)
    session_id = coordinator.store.session_for_world("world-shadow")["id"]

    assert coordinator.record_request(
        _prepared("request-override", override),
        base_messages=base,
        messages_override=override,
    )
    patch = _patch_payload(coordinator.store, session_id, "request-override")
    assert patch["mode"] == "replace_all"
    assert patch["messages"] == override
    assert coordinator.store.replay_request_messages(session_id, "request-override") == override

    metadata = coordinator.store.event_metadata(session_id)
    assert all("payload" not in event for event in metadata)
    assert coordinator.store.project(session_id) == base


def test_failed_turn_surface_is_not_checkpointed_or_projected(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    base = [{"role": "system", "content": "keeper"}]
    assert coordinator.ensure_bound(base)
    with session_scope(coordinator.store.url) as session:
        session.add(
            Turn(
                pk=new_id("turnrow"),
                id="turn-failed",
                world_id="world-shadow",
                kind="action",
                status="active",
                record={},
            )
        )

    in_flight = [*base, {"role": "user", "content": "risky action"}]
    checkpoint = coordinator.current_checkpoint(in_flight, "turn-failed")
    assert checkpoint is not None
    assert coordinator.store.project(checkpoint.session_id) == base
    assert coordinator.store.project(
        checkpoint.session_id,
        include_turn_id="turn-failed",
    ) == in_flight

    with session_scope(coordinator.store.url) as session:
        session.query(Turn).filter_by(id="turn-failed").one().status = "failed"

    assert coordinator.store.project(checkpoint.session_id) == base
    assert coordinator.current_checkpoint(in_flight, "turn-failed") is None


def test_engine_manual_save_and_checkpoint_load_resume(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages.append({"role": "user", "content": "记住钟楼。"})
    engine.save("slot_001")

    saved_messages, _snapshot, metadata = load_game_artifacts(
        "slot_001", context=engine.context
    )
    checkpoint = ContextCheckpoint.from_mapping(metadata["context"])
    assert saved_messages == engine.messages
    assert checkpoint.surface_digest == messages_digest(engine.messages)
    assert "context" not in list_saves(context=engine.context)[0]

    engine.messages.append({"role": "assistant", "content": "未来消息。"})
    engine.save("slot_002")
    assert engine.load("slot_001") is not None
    active = shadow_adapter.for_engine(engine).store.session_for_world(engine.context.world_id)
    assert active["parent_session_id"] == checkpoint.session_id
    assert active["source_sequence"] == checkpoint.sequence
    assert shadow_adapter.for_engine(engine).store.project(active["id"]) == engine.messages


def test_engine_completed_turn_writes_private_checkpoint_but_public_views_strip_it(
    tmp_path: Path,
) -> None:
    engine = _game_engine(tmp_path)
    engine._stream_llm = lambda *_args, **_kwargs: (
        "你听见钟声。\n\n**你可以——**\n1. 继续",
        [],
    )
    engine.handle_action("查看钟楼")
    turn_id = engine.turn_journal.latest_completed_id()
    assert turn_id
    record = engine.turn_journal.read(turn_id)
    checkpoint = ContextCheckpoint.from_mapping(record["context"])

    with session_scope(engine.context.database_url) as session:
        slot = session.query(SaveSlot).filter_by(
            world_id=engine.context.world_id,
            slot_key="slot_000",
        ).one()
        assert slot.metadata_json["context"] == checkpoint.to_dict()
    assert "context" not in engine.turn_journal.public_history()[0]
    assert all("context" not in item for item in engine.list_saves())


def test_failed_engine_turn_restores_messages_and_hides_active_delta(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    before = copy.deepcopy(engine.messages)

    def fail_after_shadow_sync(*_args, **_kwargs):
        shadow = shadow_adapter.for_engine(engine)
        assert shadow.sync_turn(
            list(engine.messages),
            engine.active_turn_id,
            engine.__dict__.get("_turn_context_surface"),
        )
        raise RuntimeError("synthetic model failure")

    engine._stream_llm = fail_after_shadow_sync
    with pytest.raises(RuntimeError, match="synthetic model failure"):
        engine.handle_action("触碰陷阱")

    assert engine.messages == before
    shadow = shadow_adapter.for_engine(engine)
    active = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(active["id"]) == before
    with session_scope(engine.context.database_url) as session:
        failed = session.query(Turn).filter_by(world_id=engine.context.world_id).one()
        assert failed.status == "failed"


def test_cancelled_preflight_note_is_saved_after_turn_rollback(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    before = copy.deepcopy(engine.messages)
    engine._preflight_player_escalation = lambda _content: None

    engine.handle_action("朝法伦开枪")

    assert engine.active_turn_id is None
    assert engine.messages[:-1] == before
    assert "行动发生前取消" in engine.messages[-1]["content"]
    shadow = shadow_adapter.for_engine(engine)
    active = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(active["id"]) == engine.messages

    saved_messages, _snapshot, metadata = load_game_artifacts(
        "slot_000", context=engine.context
    )
    assert saved_messages == engine.messages
    checkpoint = ContextCheckpoint.from_mapping(metadata["context"])
    assert checkpoint.surface_digest == messages_digest(engine.messages)
    with session_scope(engine.context.database_url) as session:
        cancelled = session.query(Turn).filter_by(world_id=engine.context.world_id).one()
        assert cancelled.status == "cancelled"


def test_tier_reminder_is_append_only_context_injection(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages.extend(
        [
            {"role": "user", "content": "上一轮行动"},
            {"role": "assistant", "content": "上一轮结果"},
        ]
    )
    before = copy.deepcopy(engine.messages)
    engine._inject_tier_reminder()

    assert engine.messages[:-1] == before
    assert engine.messages[-1]["role"] == "user"
    assert engine.messages[-1]["content"].startswith(engine.CONTROL_MESSAGE_PREFIX)
    assert "[核心约束" in engine.messages[-1]["content"]


def test_compactor_compacts_via_replace_checkpoint_without_rebase(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    base = [
        {"role": "system", "content": "keeper"},
        {"role": "user", "content": "old action"},
        {"role": "assistant", "content": "old result"},
    ]
    assert coordinator.ensure_bound(base)
    previous = coordinator.store.session_for_world("world-shadow")
    engine = SimpleNamespace(
        context=coordinator.context,
        messages=copy.deepcopy(base),
        _context_shadow=coordinator,
        _summary_token_estimate=0,
    )

    changed = HistoryCompactor(engine).apply(
        base[0],
        '{"events":["old action"]}',
        [base[-1]],
        "test summarizer",
        silent=True,
    )

    assert changed is True
    active = coordinator.store.session_for_world("world-shadow")
    # Non-destructive: same session, one replace checkpoint, raw events kept.
    assert active["id"] == previous["id"]
    assert coordinator.store.project(active["id"]) == engine.messages
    events = coordinator.store.load_event_payloads(active["id"])
    assert len([e for e in events if e["surface_op"] == "append"]) == len(base)
    checkpoints = [e for e in events if e["event_type"] == "compaction_checkpoint"]
    assert len(checkpoints) == 1
    assert checkpoints[0]["surface_op"] == "replace"
    assert checkpoints[0]["turn_id"] is None
    assert len(checkpoints[0]["source_sequences"]) == 1  # 窗口 [1, 2) 内全部 surface 事件
    # Metadata view never exposes the replacement payload.
    for row in coordinator.store.event_metadata(active["id"]):
        assert "payload" not in row


def test_shadow_store_failure_does_not_block_authoritative_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _game_engine(tmp_path)
    shadow = shadow_adapter.for_engine(engine)

    def fail_sync(*_args, **_kwargs):
        raise OSError("synthetic shadow outage")

    monkeypatch.setattr(shadow.store, "sync_messages", fail_sync)
    engine.messages.append({"role": "user", "content": "仍然保存"})
    assert engine.save("slot_009") == "slot_009"
    messages, _snapshot, metadata = load_game_artifacts("slot_009", context=engine.context)
    assert messages == engine.messages
    assert "context" not in metadata
    assert shadow.diagnostics[-1] == {
        "operation": "sync_messages",
        "error": "OSError",
    }


def test_active_turn_identity_request_hidden_by_failed_but_still_replays(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    base = [{"role": "system", "content": "keeper"}]
    assert coordinator.ensure_bound(base)
    session_id = coordinator.store.session_for_world("world-shadow")["id"]
    with session_scope(coordinator.store.url) as session:
        session.add(
            Turn(
                pk=new_id("turnrow"),
                id="turn-identity",
                world_id="world-shadow",
                kind="action",
                status="active",
                record={},
            )
        )

    in_flight = [*base, {"role": "user", "content": "risky action"}]
    assert coordinator.record_request(
        _prepared("request-identity", in_flight),
        base_messages=in_flight,
        turn_id="turn-identity",
    )

    # 普通投影从不含 active turn 的 action。
    assert coordinator.store.project(session_id) == base

    with session_scope(coordinator.store.url) as session:
        session.query(Turn).filter_by(id="turn-identity").one().status = "failed"

    # 失败后普通投影仍不含该 action。
    assert coordinator.store.project(session_id) == base
    # 但 replay_request_messages 仍能精确重放该 identity 请求。
    assert (
        coordinator.store.replay_request_messages(session_id, "request-identity")
        == in_flight
    )


def test_shadow_disabled_save_succeeds_without_context_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRPG_CONTEXT_EVENT_SHADOW", "0")
    engine = _game_engine(tmp_path)
    engine.messages.append({"role": "user", "content": "普通存档"})
    assert engine.save("slot_777") == "slot_777"

    messages, _snapshot, metadata = load_game_artifacts("slot_777", context=engine.context)
    assert messages == engine.messages
    assert "context" not in metadata

    with session_scope(engine.context.database_url) as session:
        assert session.query(ContextSession).count() == 0
        assert session.query(ModelContextEvent).count() == 0
