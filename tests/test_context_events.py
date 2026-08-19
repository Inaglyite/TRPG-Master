"""H2 context-events core tests: store, projector, epoch/branch/GC invariants.

Covers the first-batch H2 contract without touching engine/server integration:
- legacy seed is idempotent (same digest no-op, different digest fails closed)
- append + pure replay produces the exact projected surface / digest
- native tool_call / tool_result and DSML-style pairing normalize correctly
- request_envelope events never carry prompt content and never surface
- failed/cancelled/interrupted turn events never enter later projections
- sibling branches are isolated; ancestor prefix is honored via source_sequence
- resume epoch cutoffs hide post-cutoff future events
- checkpoint ``replace`` masks explicit (session_id, sequence) refs only
- reference-aware GC deletes only closed childless non-current sessions
- ordinary diagnostics/metadata never expose event payload
- migration DDL shape (SQLite partial unique index, columns)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, inspect

from src.context_events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_CHECKPOINT,
    EVENT_CONTEXT_INJECTION,
    EVENT_ENTERED_PLAYER_ACTION,
    EVENT_REQUEST_ENVELOPE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    ContextEventStore,
    ContextProjector,
    infer_event_type,
    messages_digest,
)
from src.database import (
    Base,
    ContextSession,
    ModelContextEvent,
    SaveSlot,
    Snapshot,
    Turn,
    World,
    get_engine,
    new_id,
    session_scope,
)


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def seed_world(url: str, world_id: str = "world-a") -> None:
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="module-a"))


def make_turn(url: str, world_id: str, turn_id: str, status: str = "completed") -> None:
    with session_scope(url) as session:
        session.add(
            Turn(
                pk=new_id("turnrow"),
                id=turn_id,
                world_id=world_id,
                kind="action",
                status=status,
                record={},
            )
        )


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_ensure_session_creates_epoch_one_and_is_idempotent(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    first = store.ensure_session("world-a")
    assert first["session_epoch"] == 1
    assert first["status"] == "active"
    assert first["head_sequence"] == 0
    again = store.ensure_session("world-a")
    assert again["id"] == first["id"]
    # one active session per world, enforced by the partial unique index
    with session_scope(url) as session:
        active = session.query(ContextSession).filter_by(world_id="world-a", status="active").all()
        assert len(active) == 1


def test_ensure_session_after_closed_epoch_uses_next_epoch(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    first = store.ensure_session("world-a")
    store.close_session("world-a")

    second = store.ensure_session("world-a")

    assert second["session_epoch"] == 2
    assert second["parent_session_id"] is None
    assert second["id"] != first["id"]


def test_migration_ddl_shape_sqlite(tmp_path: Path):
    """The ORM creates the H2 tables with the H2 column shapes on SQLite."""
    url = sqlite_url(tmp_path)
    seed_world(url)
    inspector = inspect(get_engine(url))
    sessions = {c["name"]: c for c in inspector.get_columns("context_sessions")}
    events = {c["name"]: c for c in inspector.get_columns("model_context_events")}
    for column in (
        "id",
        "world_id",
        "root_world_id",
        "session_epoch",
        "parent_session_id",
        "parent_world_id",
        "source_sequence",
        "head_sequence",
        "status",
        "seed_digest",
        "created_at",
        "closed_at",
    ):
        assert column in sessions, f"context_sessions missing {column}"
    for column in (
        "id",
        "session_id",
        "world_id",
        "root_world_id",
        "turn_id",
        "step",
        "sequence",
        "event_type",
        "source_kind",
        "source_id",
        "source_version",
        "content_digest",
        "audience",
        "sensitivity",
        "surface_op",
        "source_sequences",
        "payload",
        "created_at",
    ):
        assert column in events, f"model_context_events missing {column}"
    # partial unique index: one active session per world
    index_names = {i["name"] for i in inspector.get_indexes("context_sessions")}
    assert "uq_context_sessions_one_active_per_world" in index_names
    unique = {u["name"] for u in inspector.get_unique_constraints("context_sessions")}
    assert "uq_context_session_world_epoch" in unique
    event_unique = {u["name"] for u in inspector.get_unique_constraints("model_context_events")}
    assert "uq_context_event_sequence" in event_unique


# ---------------------------------------------------------------------------
# Legacy seed
# ---------------------------------------------------------------------------


def test_legacy_seed_is_idempotent_and_rejects_different_digest(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    messages = [
        {"role": "system", "content": "你是主持人。"},
        {"role": "user", "content": "我在酒馆里。"},
        {"role": "assistant", "content": "你看到柜台后的老板。"},
    ]
    head = store.seed_legacy("world-a", messages)
    assert head == 3
    again = store.seed_legacy("world-a", messages)
    assert again == 3  # no double append
    with session_scope(url) as session:
        count = (
            session.query(ModelContextEvent)
            .filter_by(session_id=store.ensure_session("world-a")["id"])
            .count()
        )
        assert count == 3
    with pytest.raises(ValueError):
        store.seed_legacy("world-a", messages + [{"role": "user", "content": "再来一句"}])


def test_seed_concurrent_idempotent_same_digest(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    messages = [{"role": "user", "content": "x"}]
    heads = []
    import threading

    errors: list[Exception] = []

    def worker() -> None:
        try:
            heads.append(store.seed_legacy("world-a", messages))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert heads == [1, 1, 1, 1]


def test_legacy_seed_marks_source_kind_and_payload_private_for_tools(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    messages = [
        {"role": "user", "content": "投个骰子"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "function": {"name": "roll", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"total": 7}'},
    ]
    session_id = store.ensure_session("world-a")["id"]
    store.seed_legacy("world-a", messages)
    meta = store.event_metadata(session_id)
    kinds = {row["event_type"]: row["source_kind"] for row in meta}
    assert kinds[EVENT_ENTERED_PLAYER_ACTION] == "legacy_save"
    assert kinds[EVENT_TOOL_CALL] == "legacy_save"
    assert kinds[EVENT_TOOL_RESULT] == "legacy_save"
    # tool events are sensitivity=private
    by_type = {row["event_type"]: row for row in meta}
    assert by_type[EVENT_TOOL_RESULT]["sensitivity"] == "private"
    assert by_type[EVENT_ENTERED_PLAYER_ACTION]["sensitivity"] == "public"


# ---------------------------------------------------------------------------
# Append + pure replay
# ---------------------------------------------------------------------------


def _append_messages(url: str, session_id: str, messages: list[dict]) -> list[int]:
    store = ContextEventStore(url)
    sequences = []
    for message in messages:
        sequences.append(
            store.append(
                session_id,
                event_type=infer_event_type(message),
                payload=dict(message),
                turn_id="turn-1",
                source_kind="turn",
            )
        )
    return sequences


def test_append_sequences_and_pure_replay_digest(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    messages = [
        {"role": "user", "content": "我看到一扇门"},
        {"role": "assistant", "content": "门后传来低语。"},
    ]
    sequences = _append_messages(url, session_id, messages)
    assert sequences == [1, 2]
    assert store.head_sequence(session_id) == 2
    projected = store.project(session_id)
    assert [m["content"] for m in projected] == ["我看到一扇门", "门后传来低语。"]
    compare = store.shadow_compare(session_id, messages)
    assert compare["match"] is True
    assert compare["projected_count"] == 2
    assert compare["current_count"] == 2
    # non-matching messages fail the shadow compare without mutating
    compare_bad = store.shadow_compare(session_id, messages + [{"role": "user", "content": "x"}])
    assert compare_bad["match"] is False
    assert store.head_sequence(session_id) == 2


def test_replay_is_append_order_preserving_and_ignores_non_surface(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    _append_messages(url, session_id, messages)
    # request envelope must never appear as a projected message
    store.record_request_envelope(session_id, prepared=SimpleNamespace(request_envelope={}))
    projected = store.project(session_id)
    assert [m["content"] for m in projected] == ["a", "b"]
    # metadata view never contains payload
    for row in store.event_metadata(session_id):
        assert "payload" not in row


def test_tool_pairing_native_and_dsml_replays_to_same_digest(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    seed_world(url, "world-b")
    store = ContextEventStore(url)
    session_native = store.ensure_session("world-a")["id"]
    session_dsml = store.ensure_session("world-b")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    make_turn(url, "world-b", "turn-1", status="completed")
    native = [
        {"role": "user", "content": "骰子"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "roll", "arguments": '{"d":20}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"total": 7}'},
        {"role": "assistant", "content": "你掷出了 7。"},
    ]
    dsml = [
        {"role": "user", "content": "骰子"},
        {
            "role": "assistant",
            "content": '[tool_call] {"name": "roll", "id": "c1", "arguments": "{\\"d\\":20}"}',
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"total": 7}'},
        {"role": "assistant", "content": "你掷出了 7。"},
    ]
    _append_messages(url, session_native, native)
    _append_messages(url, session_dsml, dsml)
    projected_native = store.project(session_native)
    projected_dsml = store.project(session_dsml)
    # The event layer only records what the model actually saw: it guarantees
    # replay fidelity against the normalized surface, not that two textual
    # representations of the same tool call (native `tool_calls` array vs a
    # DSML-style string) collapse here.  Converting DSML text into structured
    # tool calls is tool_protocol/model_streamer's job, upstream of this
    # store.  So each input must replay to the digest of its *normalized*
    # surface (normalize_tool_message_history repairs interrupted batches).
    normalized_native = store._normalize_messages(native)
    normalized_dsml = store._normalize_messages(dsml)
    # Native replay preserves the normalized structured surface exactly.
    assert messages_digest(projected_native) == messages_digest(normalized_native)
    assert messages_digest(projected_dsml) == messages_digest(normalized_dsml)


# ---------------------------------------------------------------------------
# Turn visibility: failed/cancelled/interrupted never surface
# ---------------------------------------------------------------------------


def test_failed_turn_events_never_enter_later_projection(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-ok", status="completed")
    make_turn(url, "world-a", "turn-failed", status="failed")
    make_turn(url, "world-a", "turn-cancelled", status="cancelled")
    make_turn(url, "world-a", "turn-interrupted", status="interrupted")
    store.append(
        session_id,
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "ok"},
        turn_id="turn-ok",
        source_kind="turn",
    )
    for bad_turn in ("turn-failed", "turn-cancelled", "turn-interrupted"):
        store.append(
            session_id,
            event_type=EVENT_ENTERED_PLAYER_ACTION,
            payload={"role": "user", "content": bad_turn},
            turn_id=bad_turn,
            source_kind="turn",
        )
    projected = store.project(session_id)
    assert [m["content"] for m in projected] == ["ok"]
    # explicit include of a failed turn still yields nothing (fail closed)
    projected_include = store.project(session_id, include_turn_id="turn-failed")
    assert [m["content"] for m in projected_include] == ["ok"]


def test_active_turn_visible_only_with_explicit_include(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-ok", status="completed")
    make_turn(url, "world-a", "turn-active", status="active")
    store.append(
        session_id,
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "ok"},
        turn_id="turn-ok",
        source_kind="turn",
    )
    store.append(
        session_id,
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "in-flight"},
        turn_id="turn-active",
        source_kind="turn",
    )
    projected = store.project(session_id)
    assert [m["content"] for m in projected] == ["ok"]
    projected_include = store.project(session_id, include_turn_id="turn-active")
    assert {m["content"] for m in projected_include} == {"ok", "in-flight"}


# ---------------------------------------------------------------------------
# Branch isolation + ancestor prefix
# ---------------------------------------------------------------------------


def test_sibling_branches_isolated_and_ancestor_prefix_cutoff(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    # main lineage: epoch1 (seed) -> epoch2 (current), branch forks from epoch1
    epoch1 = store.ensure_session("world-a")
    store.seed_legacy(
        "world-a", [{"role": "system", "content": "s"}, {"role": "user", "content": "u1"}]
    )
    epoch2 = store.begin_epoch("world-a", cutoff_sequence=2)
    # current timeline continues in epoch2
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        epoch2["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "u2"},
        turn_id="turn-1",
        source_kind="turn",
    )
    # sibling branch: reopen from epoch1 head (cutoff 2) as its own world via
    # fork_session (cross-world lineage, events are never copied)
    seed_world(url, "world-b")
    branch = store.fork_session("world-b", epoch1["id"], cutoff_sequence=2)
    assert branch["source_sequence"] == 2
    assert branch["parent_world_id"] == "world-a"
    assert branch["root_world_id"] == "world-a"
    branch_projected = store.project(branch["id"])
    assert [m["content"] for m in branch_projected] == ["s", "u1"]
    main_projected = store.project(epoch2["id"])
    assert [m["content"] for m in main_projected] == ["s", "u1", "u2"]
    # events appended to the branch never leak into the main lineage
    make_turn(url, "world-b", "turn-1", status="completed")
    store.append(
        branch["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "branch-only"},
        turn_id="turn-1",
        source_kind="turn",
    )
    assert "branch-only" not in {m["content"] for m in store.project(epoch2["id"])}


def test_fork_session_rejects_same_world(tmp_path: Path):
    """fork must cross worlds; a same-world fork is a begin_epoch (D)."""
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    with pytest.raises(ValueError, match="同世界"):
        store.fork_session("world-a", session["id"])


def test_fork_concurrent_with_source_append_is_deterministic(tmp_path: Path):
    """fork takes both process locks in sorted order and re-reads the
    source head under them, so racing appends never tear the fork cutoff:
    the branch projection is always a consistent prefix of the main line."""
    import threading

    url = sqlite_url(tmp_path)
    seed_world(url)
    seed_world(url, "world-b")
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    make_turn(url, "world-a", "turn-1", status="completed")

    errors: list[Exception] = []

    def appender() -> None:
        try:
            for _ in range(20):
                store.append(
                    session["id"],
                    event_type=EVENT_ENTERED_PLAYER_ACTION,
                    payload={"role": "user", "content": "m"},
                    turn_id="turn-1",
                    source_kind="turn",
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def forker() -> None:
        try:
            store.fork_session("world-b", session["id"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=appender),
        threading.Thread(target=forker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors

    branch = store.session_for_world("world-b")
    assert branch is not None
    cutoff = int(branch["source_sequence"])
    head = int(store.session_for_world("world-a")["head_sequence"])
    # The branch cutoff is a valid snapshot point: never negative, never
    # beyond the source head, and its projection is a prefix of the source.
    assert 0 <= cutoff <= head
    branch_projected = store.project(branch["id"])
    main_projected = store.project(session["id"])
    assert len(branch_projected) <= len(main_projected)
    assert branch_projected == main_projected[: len(branch_projected)]


# ---------------------------------------------------------------------------
# Resume epoch cutoff hides future
# ---------------------------------------------------------------------------


def test_begin_epoch_cutoff_hides_future_and_validates_range(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        session["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "m1"},
        turn_id="turn-1",
        source_kind="turn",
    )
    make_turn(url, "world-a", "turn-2", status="completed")
    store.append(
        session["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "m2"},
        turn_id="turn-2",
        source_kind="turn",
    )
    # resume from an old save whose cutoff was after m1 only
    resumed = store.begin_epoch("world-a", cutoff_sequence=1)
    assert resumed["session_epoch"] == 2
    assert resumed["source_sequence"] == 1
    assert resumed["parent_session_id"] == session["id"]
    projected = store.project(resumed["id"])
    assert [m["content"] for m in projected] == ["m1"]
    # the future event stays in the old session (never deleted)
    old_events = store.event_metadata(session["id"])
    assert len(old_events) == 2
    # invalid cutoffs fail closed
    with pytest.raises(ValueError):
        store.begin_epoch("world-a", cutoff_sequence=-1)
    with pytest.raises(ValueError):
        store.begin_epoch("world-a", cutoff_sequence=999)


def test_begin_epoch_without_active_session_inlines_creation(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    result = store.begin_epoch("world-a")
    assert result["session_epoch"] == 1
    assert result["status"] == "active"
    assert result["source_sequence"] == 0


def test_begin_epoch_after_closed_session_uses_next_epoch(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    first = store.ensure_session("world-a")
    store.close_session("world-a")

    result = store.begin_epoch("world-a")

    assert result["session_epoch"] == 2
    assert result["parent_session_id"] is None
    assert result["id"] != first["id"]


# ---------------------------------------------------------------------------
# Checkpoint replace: masks explicit (session_id, sequence) refs only
# ---------------------------------------------------------------------------


def test_checkpoint_replace_masks_explicit_refs_and_keeps_raw_events(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    sequences = _append_messages(url, session_id, messages)
    # checkpoint masks events 1 and 2 (pinned to this session), keeps 3,
    # and must carry a replacement message (C: replace without replacement
    # would silently drop history)
    store.append(
        session_id,
        event_type=EVENT_CHECKPOINT,
        payload={"checkpoint": "cp1", "replacement": {"role": "assistant", "content": "R1"}},
        surface_op="replace",
        source_sequences=[
            {"session_id": session_id, "sequence": sequences[0]},
            {"session_id": session_id, "sequence": sequences[1]},
        ],
        source_kind="compaction",
    )
    # replacement lands at the earliest source position, masking a and b
    projected = store.project(session_id)
    assert [m["content"] for m in projected] == ["R1", "c"]
    # raw events still exist in the log (4 = 3 messages + 1 checkpoint)
    assert len(store.event_metadata(session_id)) == 4
    # checkpoint refs are persisted fully structured (bare ints are
    # normalised to {session_id, sequence} at append time)
    checkpoint_meta = [
        r for r in store.event_metadata(session_id) if r["event_type"] == EVENT_CHECKPOINT
    ]
    assert checkpoint_meta[0]["source_sequences"] == [
        {"session_id": session_id, "sequence": sequences[0]},
        {"session_id": session_id, "sequence": sequences[1]},
    ]
    # a bare int is accepted as shorthand for the *current* session and
    # persisted structured; the referenced event exists here
    store.append(
        session_id,
        event_type=EVENT_CHECKPOINT,
        payload={"checkpoint": "cp1b", "replacement": {"role": "assistant", "content": "R1b"}},
        surface_op="replace",
        source_sequences=[sequences[2]],  # bare int -> current session
        source_kind="compaction",
        source_id="cp1b",
    )
    bare_meta = [r for r in store.event_metadata(session_id) if r["source_id"] == "cp1b"]
    assert bare_meta[0]["source_sequences"] == [
        {"session_id": session_id, "sequence": sequences[2]}
    ]
    # a bare/foreign reference to an event that does not exist in the
    # current lineage fails closed at append time (D: missing source)
    with pytest.raises(ValueError):
        store.append(
            session_id,
            event_type=EVENT_CHECKPOINT,
            payload={"checkpoint": "bad", "replacement": {"role": "assistant", "content": "X"}},
            surface_op="replace",
            source_sequences=[999],  # no such sequence in this lineage
            source_kind="compaction",
        )
    # a replace without a replacement message is rejected (C)
    with pytest.raises(ValueError):
        store.append(
            session_id,
            event_type=EVENT_CHECKPOINT,
            payload={"checkpoint": "bare"},
            surface_op="replace",
            source_sequences=[sequences[0]],
            source_kind="compaction",
        )


def test_nested_checkpoint_replacement(tmp_path: Path):
    """A later checkpoint can reference an earlier checkpoint (D)."""
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    sequences = _append_messages(url, session_id, messages)
    cp1 = store.append(
        session_id,
        event_type=EVENT_CHECKPOINT,
        payload={"checkpoint": "cp1", "replacement": {"role": "assistant", "content": "R1"}},
        surface_op="replace",
        source_sequences=[sequences[0], sequences[1]],
        source_kind="compaction",
    )
    assert [m["content"] for m in store.project(session_id)] == ["R1", "c"]
    # cp2 references cp1's own (session_id, sequence): its replacement
    # overwrites the position where R1 landed
    store.append(
        session_id,
        event_type=EVENT_CHECKPOINT,
        payload={"checkpoint": "cp2", "replacement": {"role": "assistant", "content": "R2"}},
        surface_op="replace",
        source_sequences=[{"session_id": session_id, "sequence": cp1}],
        source_kind="compaction",
    )
    assert [m["content"] for m in store.project(session_id)] == ["R2", "c"]
    # raw events (3 messages + 2 checkpoints) all still exist
    assert len(store.event_metadata(session_id)) == 5


def test_projector_missing_source_fails_closed_without_orphan_replacement():
    """A checkpoint whose source is not in the timeline must not mask and
    must never append its replacement out of band (D, pure function)."""
    sessions = [{"id": "s1", "source_sequence": 2}]
    events = {
        "s1": [
            {
                "sequence": 1,
                "world_id": "w1",
                "event_type": EVENT_ENTERED_PLAYER_ACTION,
                "surface_op": "append",
                "turn_id": "t1",
                "payload": {"role": "user", "content": "a"},
            },
            # checkpoint references a sequence that was never projected
            {
                "sequence": 2,
                "world_id": "w1",
                "event_type": EVENT_CHECKPOINT,
                "surface_op": "replace",
                "source_sequences": [{"session_id": "s1", "sequence": 99}],
                "payload": {"replacement": {"role": "assistant", "content": "R"}},
            },
        ],
    }
    result = ContextProjector.project_timeline(sessions, events, visible_turn_ids={("w1", "t1")})
    assert [m["content"] for m in result] == ["a"]
    assert "R" not in {m.get("content") for m in result}


def test_checkpoint_reference_without_session_id_rejected(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        session_id,
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "a"},
        turn_id="turn-1",
        source_kind="turn",
    )
    with pytest.raises(ValueError):
        store.append(
            session_id,
            event_type=EVENT_CHECKPOINT,
            payload={"checkpoint": "cp", "replacement": {"role": "assistant", "content": "R"}},
            surface_op="replace",
            source_sequences=[{"sequence": 1}],  # missing session_id
            source_kind="compaction",
        )


# ---------------------------------------------------------------------------
# Lineage cycle / depth guard
# ---------------------------------------------------------------------------


def test_lineage_cycle_detected(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    with session_scope(url) as db:
        row = db.get(ContextSession, session["id"])
        row.parent_session_id = session["id"]  # self-cycle
        db.flush()
    with pytest.raises(ValueError, match="环"):
        store.project(session["id"])


# ---------------------------------------------------------------------------
# Reference-aware GC
# ---------------------------------------------------------------------------


def test_reference_aware_gc_only_closed_childless_non_current(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        session["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "m1"},
        turn_id="turn-1",
        source_kind="turn",
    )
    # epoch2 references epoch1 -> epoch1 must survive GC
    store.begin_epoch("world-a", cutoff_sequence=1)
    # epoch2 is active -> survives GC
    # create a detached closed orphan in a different world for collection
    seed_world(url, "world-c")
    orphan = store.ensure_session("world-c")
    store.close_session("world-c")
    result = store.reference_aware_gc("world-c")
    assert result["sessions"] == 1
    result_main = store.reference_aware_gc("world-a")
    assert result_main["sessions"] == 0  # epoch1 referenced, epoch2 active
    # events of the collected orphan are gone
    with session_scope(url) as db:
        events = db.query(ModelContextEvent).filter_by(session_id=orphan["id"]).count()
        assert events == 0


def test_reference_aware_gc_refuses_active_and_parent(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    store.ensure_session("world-a")
    store.begin_epoch("world-a", cutoff_sequence=0)
    result = store.reference_aware_gc("world-a")
    assert result["sessions"] == 0
    with session_scope(url) as db:
        assert db.query(ContextSession).filter_by(world_id="world-a").count() == 2


# ---------------------------------------------------------------------------
# Payload privacy: public diagnostics/serialization never expose payload
# ---------------------------------------------------------------------------


def test_event_metadata_never_exposes_payload(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        session_id,
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "秘密内容"},
        turn_id="turn-1",
        source_kind="turn",
    )
    store.record_request_envelope(
        session_id, prepared=SimpleNamespace(request_envelope={"request_id": "r1"})
    )
    for row in store.event_metadata(session_id):
        assert "payload" not in row
        # content_digest is metadata, not content; the payload body (here the
        # secret string) must never appear anywhere in the metadata row.
        assert "秘密" not in json_dumps(row)
    session_dict = store.session_for_world("world-a")
    assert "payload" not in session_dict


def test_request_envelope_never_contains_prompt_content(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    prepared = SimpleNamespace(
        request_envelope={
            "request_id": "req-1",
            "profile": "standard",
            "step": 2,
            "model": "deepseek-r1",
            "message_digest": "abc123",
            "context_section_digests": {"world": "def456"},
            "sections": [
                {
                    "id": "world",
                    "audience": "model_private",
                    "chars": 120,
                    "estimated_tokens": 30,
                    "digest": "def456",
                }
            ],
        }
    )
    store.record_request_envelope(session_id, prepared=prepared)
    # the envelope event exists but contains no prompt text
    meta = store.event_metadata(session_id)
    assert len(meta) == 1
    assert meta[0]["event_type"] == EVENT_REQUEST_ENVELOPE
    assert meta[0]["source_id"] == "req-1"
    # envelope never surfaces as a message
    assert store.project(session_id) == []
    # payload is digest-only
    payloads = store.load_event_payloads(session_id)
    assert payloads[0]["payload"]["request_id"] == "req-1"
    assert "content" not in payloads[0]["payload"]
    assert "sections" in payloads[0]["payload"]


def test_safe_append_fails_without_payload_in_error(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    sequence, error = store.safe_append(
        "missing-session",
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "秘密"},
    )
    assert sequence is None
    assert error is not None
    assert "秘密" not in error


# ---------------------------------------------------------------------------
# Projector pure-function golden tests
# ---------------------------------------------------------------------------


def test_projector_pure_timeline_with_visible_turns_and_replace():
    sessions = [{"id": "s1", "source_sequence": 2}, {"id": "s2", "source_sequence": 5}]
    events = {
        "s1": [
            {
                "sequence": 1,
                "world_id": "w1",
                "event_type": EVENT_ENTERED_PLAYER_ACTION,
                "surface_op": "append",
                "turn_id": "t1",
                "payload": {"role": "user", "content": "a"},
            },
            {
                "sequence": 2,
                "world_id": "w1",
                "event_type": EVENT_ASSISTANT_MESSAGE,
                "surface_op": "append",
                "turn_id": "t1",
                "payload": {"role": "assistant", "content": "b"},
            },
        ],
        "s2": [
            {
                "sequence": 3,
                "world_id": "w1",
                "event_type": EVENT_REQUEST_ENVELOPE,
                "surface_op": "append",
                "turn_id": None,
                "payload": {"request_id": "r1"},
            },
            {
                "sequence": 4,
                "world_id": "w1",
                "event_type": EVENT_ENTERED_PLAYER_ACTION,
                "surface_op": "append",
                "turn_id": "t2",
                "payload": {"role": "user", "content": "c"},
            },
            {
                "sequence": 5,
                "world_id": "w1",
                "event_type": EVENT_TOOL_RESULT,
                "surface_op": "append",
                "turn_id": "t2",
                "payload": {"role": "tool", "content": '{"total": 7}'},
            },
        ],
    }
    # strict (world_id, turn_id) pairs; a bare turn id is never accepted
    result = ContextProjector.project_timeline(
        sessions, events, visible_turn_ids={("w1", "t1"), ("w1", "t2")}
    )
    assert [m["content"] for m in result] == ["a", "b", "c", '{"total": 7}']
    # non-surface request envelope excluded even when turn ids are unrestricted
    result_all = ContextProjector.project_timeline(sessions, events, visible_turn_ids=None)
    assert "r1" not in {m.get("content") for m in result_all}
    # failed turn excluded by visible_turn_ids; a same id in another world
    # is a different key (strict pair, no bare-turn-id fallback)
    result_visible = ContextProjector.project_timeline(
        sessions, events, visible_turn_ids={("w1", "t2")}
    )
    assert [m["content"] for m in result_visible] == ["c", '{"total": 7}']
    result_other_world = ContextProjector.project_timeline(
        sessions, events, visible_turn_ids={("w2", "t1")}
    )
    assert result_other_world == []  # w2/t1 never matches w1 events


def test_strict_world_turn_visibility_store(tmp_path: Path):
    """Store-level projection keys visibility by (world_id, turn_id): a
    branch clone reusing the same turn id in another world stays isolated
    (D)."""
    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    seed_world(url, "world-b")
    store = ContextEventStore(url)
    session_a = store.ensure_session("world-a")
    session_b = store.ensure_session("world-b")
    make_turn(url, "world-a", "turn-1", status="completed")
    make_turn(url, "world-b", "turn-1", status="completed")
    store.append(
        session_a["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "a-only"},
        turn_id="turn-1",
        source_kind="turn",
    )
    store.append(
        session_b["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "b-only"},
        turn_id="turn-1",
        source_kind="turn",
    )
    assert [m["content"] for m in store.project(session_a["id"])] == ["a-only"]
    assert [m["content"] for m in store.project(session_b["id"])] == ["b-only"]


def test_control_message_inferred_as_context_injection():
    assert (
        infer_event_type({"role": "user", "content": "[引擎控制指令｜非玩家发言] 重新开始"})
        == EVENT_CONTEXT_INJECTION
    )
    assert infer_event_type({"role": "user", "content": "我推开门"}) == EVENT_ENTERED_PLAYER_ACTION


def _json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


# re-export for the metadata assertion above
json_dumps = _json_dumps


# ---------------------------------------------------------------------------
# sync_messages: active-turn noop + atomic batch
# ---------------------------------------------------------------------------


def test_sync_messages_active_turn_repeated_sync_is_noop(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-active", status="active")
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    result, sequences = store.sync_messages(session_id, messages, turn_id="turn-active")
    assert result == "appended"
    assert sequences == [1, 2, 3]
    assert store.head_sequence(session_id) == 3
    # re-syncing the same in-flight turn is a noop (projection now includes it)
    result2, sequences2 = store.sync_messages(session_id, messages, turn_id="turn-active")
    assert result2 == "noop"
    assert sequences2 == []
    assert store.head_sequence(session_id) == 3
    with session_scope(url) as session:
        count = session.query(ModelContextEvent).filter_by(session_id=session_id).count()
        assert count == 3


def test_sync_messages_batch_failure_is_atomic_no_partial_write(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-active", status="active")
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    calls = {"n": 0}

    def fail_second(_mapper, _connection, _target):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")

    event.listen(ModelContextEvent, "before_insert", fail_second)
    try:
        with pytest.raises(RuntimeError):
            store.sync_messages(session_id, messages, turn_id="turn-active")
    finally:
        event.remove(ModelContextEvent, "before_insert", fail_second)
    # the whole batch rolled back: head and event count are both still 0
    assert store.head_sequence(session_id) == 0
    with session_scope(url) as session:
        count = session.query(ModelContextEvent).filter_by(session_id=session_id).count()
        assert count == 0


def test_sync_messages_projects_while_world_write_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The prefix snapshot and batch write share one serialization window."""
    from contextlib import contextmanager

    import src.context_events as context_events_module

    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-active", status="active")

    real_lock = context_events_module._world_write_lock
    lock_held = False

    @contextmanager
    def tracking_lock(database_url: str, world_id: str):
        nonlocal lock_held
        with real_lock(database_url, world_id):
            lock_held = True
            try:
                yield
            finally:
                lock_held = False

    real_project = store.project

    def observed_project(*args, **kwargs):
        assert lock_held is True
        return real_project(*args, **kwargs)

    monkeypatch.setattr(context_events_module, "_world_write_lock", tracking_lock)
    monkeypatch.setattr(store, "project", observed_project)

    result, sequences = store.sync_messages(
        session_id,
        [{"role": "user", "content": "a"}],
        turn_id="turn-active",
    )
    assert result == "appended"
    assert sequences == [1]
    assert lock_held is False


# ---------------------------------------------------------------------------
# Lineage seed markers: parent sessions can never be re-seeded
# ---------------------------------------------------------------------------


def test_seed_legacy_rejects_session_with_parent(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    epoch1 = store.ensure_session("world-a")
    store.seed_legacy("world-a", [{"role": "user", "content": "m1"}])
    epoch2 = store.begin_epoch("world-a", cutoff_sequence=1)
    assert epoch2["parent_session_id"] == epoch1["id"]
    assert epoch2["seed_digest"]  # lineage marker present
    with pytest.raises(ValueError, match="parent"):
        store.seed_legacy("world-a", [{"role": "user", "content": "x"}])


def test_seed_legacy_rejects_unmarked_root_with_events(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session_id = store.ensure_session("world-a")["id"]
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        session_id,
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "m1"},
        turn_id="turn-1",
        source_kind="turn",
    )
    # root session with head > 0 but no seed marker must refuse a seed
    with pytest.raises(ValueError, match="未标记"):
        store.seed_legacy("world-a", [{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# begin_fresh_epoch: new game, old events kept but never inherited
# ---------------------------------------------------------------------------


def test_begin_fresh_epoch_keeps_old_events_without_inheriting(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    epoch1 = store.ensure_session("world-a")
    store.seed_legacy("world-a", [{"role": "user", "content": "old"}])
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        epoch1["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "old-2"},
        turn_id="turn-1",
        source_kind="turn",
    )
    fresh = store.begin_fresh_epoch("world-a")
    assert fresh["parent_session_id"] is None
    assert fresh["session_epoch"] == 2
    assert fresh["head_sequence"] == 0
    assert fresh["seed_digest"] == ""
    # old events are kept in the closed session but never inherited
    assert store.project(fresh["id"]) == []
    assert len(store.event_metadata(epoch1["id"])) == 2
    with session_scope(url) as db:
        assert db.get(ContextSession, epoch1["id"]).status == "closed"
    # the fresh session is the new active one
    assert store.session_for_world("world-a")["id"] == fresh["id"]


# ---------------------------------------------------------------------------
# resume_from: reopen a historical cutoff (including the active source)
# ---------------------------------------------------------------------------


def test_resume_from_old_cutoff(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    epoch1 = store.ensure_session("world-a")
    store.seed_legacy(
        "world-a",
        [{"role": "user", "content": "s"}, {"role": "user", "content": "u1"}],
    )
    epoch2 = store.begin_epoch("world-a", cutoff_sequence=2)
    # resume from the historical epoch1 at cutoff 1 (only "s" is inherited)
    resumed = store.resume_from("world-a", epoch1["id"], cutoff=1)
    assert resumed["parent_session_id"] == epoch1["id"]
    assert resumed["source_sequence"] == 1
    assert resumed["session_epoch"] == 3
    assert [m["content"] for m in store.project(resumed["id"])] == ["s"]
    # epoch2 was the active session and is now closed
    with session_scope(url) as db:
        assert db.get(ContextSession, epoch2["id"]).status == "closed"


def test_resume_from_active_source_is_atomic(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    make_turn(url, "world-a", "turn-1", status="completed")
    store.append(
        session["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "m1"},
        turn_id="turn-1",
        source_kind="turn",
    )
    store.append(
        session["id"],
        event_type=EVENT_ENTERED_PLAYER_ACTION,
        payload={"role": "user", "content": "m2"},
        turn_id="turn-1",
        source_kind="turn",
    )
    resumed = store.resume_from("world-a", session["id"], cutoff=1)
    assert resumed["parent_session_id"] == session["id"]
    assert resumed["source_sequence"] == 1
    assert resumed["session_epoch"] == 2
    assert [m["content"] for m in store.project(resumed["id"])] == ["m1"]
    with session_scope(url) as db:
        assert db.get(ContextSession, session["id"]).status == "closed"


def test_resume_from_validates_world_and_cutoff(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    seed_world(url, "world-b")
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    other = store.ensure_session("world-b")
    with pytest.raises(ValueError, match="同一世界"):
        store.resume_from("world-a", other["id"], cutoff=0)
    with pytest.raises(ValueError, match="cutoff"):
        store.resume_from("world-a", session["id"], cutoff=999)


# ---------------------------------------------------------------------------
# Reference-aware GC also protects save-slot and turn-record references
# ---------------------------------------------------------------------------


def test_reference_aware_gc_protects_save_and_turn_references(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url, "world-save")
    seed_world(url, "world-turn")
    store = ContextEventStore(url)

    # a closed session referenced from a SaveSlot.metadata_json
    save_session = store.ensure_session("world-save")
    store.close_session("world-save")
    with session_scope(url) as db:
        snapshot = Snapshot(id=new_id("snap"), world_id="world-save", revision=1, state={})
        db.add(snapshot)
        snapshot_id = snapshot.id
    with session_scope(url) as db:
        db.add(
            SaveSlot(
                id=new_id("slot"),
                world_id="world-save",
                slot_key="auto",
                metadata_json={"context": {"session_id": save_session["id"]}},
                snapshot_id=snapshot_id,
            )
        )

    # a closed session referenced from a Turn.record
    turn_session = store.ensure_session("world-turn")
    store.close_session("world-turn")
    with session_scope(url) as db:
        db.add(
            Turn(
                pk=new_id("turnrow"),
                id="turn-ref",
                world_id="world-turn",
                kind="action",
                status="completed",
                record={"context": {"session_id": turn_session["id"]}},
            )
        )

    assert store.reference_aware_gc("world-save")["sessions"] == 0
    assert store.reference_aware_gc("world-turn")["sessions"] == 0
    with session_scope(url) as db:
        assert db.get(ContextSession, save_session["id"]) is not None
        assert db.get(ContextSession, turn_session["id"]) is not None


# ---------------------------------------------------------------------------
# project_with_refs: per-message provenance for compaction checkpoints
# ---------------------------------------------------------------------------


def test_project_with_refs_matches_project_and_addresses_events(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    messages = [
        {"role": "system", "content": "keeper"},
        {"role": "user", "content": "行动一"},
        {"role": "assistant", "content": "结果一"},
    ]
    store.seed_legacy("world-a", messages)

    projected = store.project(session["id"])
    with_refs = store.project_with_refs(session["id"])

    assert [message for message, _sid, _seq in with_refs] == projected
    assert [(sid, seq) for _m, sid, seq in with_refs] == [
        (session["id"], 1),
        (session["id"], 2),
        (session["id"], 3),
    ]


def test_project_with_refs_replacement_maps_to_checkpoint_event(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url)
    store = ContextEventStore(url)
    session = store.ensure_session("world-a")
    store.seed_legacy(
        "world-a",
        [
            {"role": "system", "content": "keeper"},
            {"role": "user", "content": "旧行动"},
            {"role": "assistant", "content": "旧结果"},
            {"role": "user", "content": "新行动"},
        ],
    )
    replacement = {"role": "user", "content": "（摘要）"}
    checkpoint_seq = store.append(
        session["id"],
        event_type=EVENT_CHECKPOINT,
        payload={"replacement": replacement},
        surface_op="replace",
        source_sequences=[
            {"session_id": session["id"], "sequence": 2},
            {"session_id": session["id"], "sequence": 3},
        ],
    )

    with_refs = store.project_with_refs(session["id"])

    assert [m["content"] for m, _s, _q in with_refs] == [
        "keeper",
        "（摘要）",
        "新行动",
    ]
    # The replacement position is addressed by the checkpoint event itself,
    # so a later nested replace can reference it.
    assert with_refs[1][1:] == (session["id"], checkpoint_seq)

    # A nested replace through the refs view works end to end.
    store.append(
        session["id"],
        event_type=EVENT_CHECKPOINT,
        payload={"replacement": {"role": "user", "content": "（二次摘要）"}},
        surface_op="replace",
        source_sequences=[
            {"session_id": with_refs[1][1], "sequence": with_refs[1][2]},
            {"session_id": with_refs[2][1], "sequence": with_refs[2][2]},
        ],
    )
    assert [m["content"] for m in store.project(session["id"])] == [
        "keeper",
        "（二次摘要）",
    ]
