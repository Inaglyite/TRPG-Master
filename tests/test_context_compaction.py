"""H2 non-destructive compaction: replace checkpoints, pruning, overflow retry.

The compactor no longer rewrites history destructively: summary replace and
tool-result pruning emit ``compaction_checkpoint`` events that mask explicit
source refs while raw events stay in the log.  These tests pin the window
rules (never split a tool call/result pair), the in-turn rollback surface
contract, and the context-overflow retry policy.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import context_shadow as shadow_adapter
from src.config import PROJECT_ROOT
from src.context_checkpoint import ContextCheckpoint
from src.context_events import ContextEventStore, messages_digest
from src.context_shadow import ContextShadowCoordinator
from src.database import (
    Base,
    World,
    get_engine,
    session_scope,
)
from src.engine import GameEngine, TurnCancelledError
from src.history_compactor import (
    TOOL_RESULT_PRUNE_MIN_CHARS,
    HistoryCompactor,
)
from src.model_request import StreamPolicy
from src.model_streamer import ModelStreamer, _is_context_overflow
from src.persistence import load_game_artifacts
from src.runtime import RuntimeContext


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'context-compaction.db'}"


def _coordinator(tmp_path: Path) -> ContextShadowCoordinator:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="world-compact", module_name="module-a"))
    return ContextShadowCoordinator(
        SimpleNamespace(world_id="world-compact", database_url=url)
    )


def _game_engine(tmp_path: Path, world_id: str = "engine-compact") -> GameEngine:
    context = RuntimeContext.create(
        world_id,
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    with patch("src.engine.OpenAI", return_value=object()):
        engine = GameEngine(context)
    engine.prepare_session()
    return engine


def _pair_history(count: int) -> list[dict]:
    messages = [{"role": "system", "content": "keeper"}]
    for index in range(count):
        messages.append({"role": "user", "content": f"行动 {index}"})
        messages.append({"role": "assistant", "content": f"结果 {index}"})
    return messages


def _checkpoint_events(store: ContextEventStore, session_id: str) -> list[dict]:
    return [
        event
        for event in store.load_event_payloads(session_id)
        if event["event_type"] == "compaction_checkpoint"
    ]


def _summarize_once(engine: GameEngine) -> bool:
    with (
        patch("src.llm._get_glm", return_value=None),
        patch.object(
            HistoryCompactor,
            "try_model",
            staticmethod(lambda _client, _model, _text: '{"events":["x"]}'),
        ),
    ):
        return HistoryCompactor(engine).summarize(silent=True)


# ---------------------------------------------------------------------------
# Summary replace
# ---------------------------------------------------------------------------


def test_summary_replace_mints_single_checkpoint_and_signs_save(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)  # 31 messages
    engine.save("slot_000")  # seeds the shadow session
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)

    assert _summarize_once(engine) is True

    # cutoff = 31 - 24 = 7, already on a user boundary → window = [1, 7)
    checkpoints = _checkpoint_events(shadow.store, session["id"])
    assert len(checkpoints) == 1
    assert checkpoints[0]["surface_op"] == "replace"
    assert checkpoints[0]["turn_id"] is None
    assert len(checkpoints[0]["source_sequences"]) == 6

    assert shadow.store.project(session["id"]) == engine.messages
    compare = shadow.store.shadow_compare(session["id"], engine.messages)
    assert compare["match"] is True

    engine.save("slot_000")
    _messages, _snapshot, metadata = load_game_artifacts(
        "slot_000", context=engine.context
    )
    checkpoint = ContextCheckpoint.from_mapping(metadata["context"])
    assert checkpoint.session_id == session["id"]
    assert checkpoint.surface_digest == messages_digest(engine.messages)


def test_compaction_boundary_never_splits_tool_call_pairs(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    messages = [{"role": "system", "content": "keeper"}]
    for index in range(14):
        messages.append({"role": "user", "content": f"行动 {index}"})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": "read_clue", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{index}",
                "content": f"线索 {index}",
            }
        )
    engine.messages = messages  # 43 messages → naive cutoff 19 lands mid-pair
    engine.save("slot_000")
    compactor = HistoryCompactor(engine)

    cutoff = compactor._compaction_cutoff()
    assert messages[cutoff]["role"] == "user"
    window = messages[1:cutoff]
    answered = {
        message["tool_call_id"] for message in window if message.get("role") == "tool"
    }
    for message in window:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            for call in message["tool_calls"]:
                assert call["id"] in answered

    assert _summarize_once(engine) is True
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages


def test_truncation_fallback_also_uses_replace_checkpoint(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    engine.save("slot_000")
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    raw_before = len(shadow.store.load_event_payloads(session["id"]))

    with (
        patch("src.llm._get_glm", return_value=None),
        patch.object(
            HistoryCompactor,
            "try_model",
            staticmethod(lambda _client, _model, _text: None),
        ),
    ):
        assert HistoryCompactor(engine).summarize(silent=True) is True

    # No history was destroyed: every raw event survives, plus one checkpoint.
    events = shadow.store.load_event_payloads(session["id"])
    assert len(events) == raw_before + 1
    assert events[-1]["event_type"] == "compaction_checkpoint"
    assert "已丢弃最早的" in events[-1]["payload"]["replacement"]["content"]
    assert shadow.store.project(session["id"]) == engine.messages
    # Same session: no epoch churn from a fallback truncation.
    assert shadow.store.session_for_world(engine.context.world_id)["id"] == session["id"]


def test_compact_falls_back_to_rebase_when_projection_diverged(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    engine.save("slot_000")
    shadow = shadow_adapter.for_engine(engine)
    previous = shadow.store.session_for_world(engine.context.world_id)
    # Diverge the authoritative surface from the shadow projection.
    engine.messages.append({"role": "user", "content": "未同步的消息"})

    changed = HistoryCompactor(engine).apply(
        engine.messages[0],
        '{"events":[]}',
        engine.messages[-4:],
        "test summarizer",
        silent=True,
    )

    assert changed is True
    active = shadow.store.session_for_world(engine.context.world_id)
    assert active["id"] != previous["id"]
    assert active["parent_session_id"] is None
    assert shadow.store.project(active["id"]) == engine.messages


def test_compaction_resume_from_autosave_after_reload(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    engine.save("slot_000")
    assert _summarize_once(engine) is True
    engine.save("slot_000")

    loaded = engine.load("slot_000")
    assert loaded == len(engine.messages) - 1
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages
    assert shadow.diagnostics == []


# ---------------------------------------------------------------------------
# In-turn compaction (context-overflow path) and the rollback surface
# ---------------------------------------------------------------------------


def _begin_turn_with_delta(engine: GameEngine, action: str) -> str:
    turn_id = engine.begin_turn_record(kind="action", player_input=action)
    engine.messages.append({"role": "user", "content": action})
    shadow = shadow_adapter.for_engine(engine)
    assert shadow.sync_turn(
        list(engine.messages),
        turn_id,
        engine.__dict__.get("_turn_context_surface"),
    )
    return turn_id


def test_in_turn_compaction_updates_rollback_surface(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    engine.save("slot_000")
    surface = copy.deepcopy(engine.messages)
    _begin_turn_with_delta(engine, "当前行动")
    replacement = {"role": "user", "content": "（摘要）"}

    assert shadow_adapter.compact_engine(engine, 1, 10, replacement) is True
    engine.messages = [engine.messages[0], replacement, *engine.messages[10:]]

    expected_surface = [surface[0], replacement, *surface[10:]]
    assert engine.__dict__["_turn_context_surface"] == expected_surface

    # The turn fails: rollback lands on the compacted surface and the
    # projection (failed-turn delta invisible, checkpoint sources turn-less)
    # agrees with it, so the next save can still sign a checkpoint.
    engine.finish_turn_record(status="failed", error="boom")
    assert engine.messages == expected_surface
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages

    engine.save("slot_000")
    _messages, _snapshot, metadata = load_game_artifacts(
        "slot_000", context=engine.context
    )
    checkpoint = ContextCheckpoint.from_mapping(metadata["context"])
    assert checkpoint.surface_digest == messages_digest(engine.messages)


def test_in_turn_compaction_refused_when_window_crosses_surface(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    engine.save("slot_000")
    surface_len = len(engine.messages)
    _begin_turn_with_delta(engine, "当前行动")
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)

    assert (
        shadow_adapter.compact_engine(
            engine, 1, surface_len + 1, {"role": "user", "content": "（摘要）"}
        )
        is False
    )
    assert _checkpoint_events(shadow.store, session["id"]) == []


# ---------------------------------------------------------------------------
# Tool-result pruning
# ---------------------------------------------------------------------------


def test_prune_old_tool_results_replaces_in_place_and_keeps_pairing(
    tmp_path: Path,
) -> None:
    engine = _game_engine(tmp_path)
    big = "x" * (TOOL_RESULT_PRUNE_MIN_CHARS + 100)
    messages = [{"role": "system", "content": "keeper"}]
    for index in range(14):
        messages.append({"role": "user", "content": f"行动 {index}"})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": "read_clue", "arguments": "{}"},
                    }
                ],
            }
        )
        content = big if index in {0, 13} else f"线索 {index}"
        messages.append(
            {"role": "tool", "tool_call_id": f"call_{index}", "content": content}
        )
    engine.messages = messages
    engine.save("slot_000")

    pruned = HistoryCompactor(engine).prune_old_tool_results()

    # Only the old (in-window) bulky result is pruned; the recent one stays.
    assert pruned == 1
    assert engine.messages[3]["content"].startswith("（工具结果已修剪")
    assert engine.messages[3]["tool_call_id"] == "call_0"
    assert engine.messages[-1]["content"] == big
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages
    # Raw bulky result still exists in the append-only log.
    assert any(
        event["payload"].get("content") == big
        for event in shadow.store.load_event_payloads(session["id"])
    )


# ---------------------------------------------------------------------------
# Context-overflow controlled retry
# ---------------------------------------------------------------------------


def test_is_context_overflow_markers() -> None:
    assert _is_context_overflow(Exception("This model's maximum context length is 8192"))
    assert _is_context_overflow(Exception("context_length_exceeded"))
    assert _is_context_overflow(Exception("too many tokens in prompt"))
    assert not _is_context_overflow(Exception("rate limit exceeded"))


class _FailingClient:
    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors
        self.calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **_kwargs):
        self.calls += 1
        raise self.errors.pop(0)


def _overflow_host(client: _FailingClient, compact_result: bool) -> SimpleNamespace:
    summarize_calls: list[dict] = []

    def _summarize(*, silent: bool, allow_rebase_fallback: bool) -> bool:
        summarize_calls.append(
            {"silent": silent, "allow_rebase_fallback": allow_rebase_fallback}
        )
        return compact_result

    host = SimpleNamespace(
        client=client,
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        context=SimpleNamespace(world_id="w-overflow", database_url=None),
        cb=SimpleNamespace(on_error=lambda *_a, **_k: None),
        _tool_request_step=0,
        _active_turn_id=None,
        _append_model_diagnostic=lambda _d: None,
        _clear_active_stream=lambda _s: None,
        turn_cancellation_requested=lambda: False,
        turn_cancelled_error=TurnCancelledError,
        _ensure_history_compactor=lambda: SimpleNamespace(summarize=_summarize),
    )
    host.summarize_calls = summarize_calls
    return host


def _streamer(host) -> ModelStreamer:
    return ModelStreamer(
        host,
        log_error=lambda _m: None,
        log_model_call=lambda *_a, **_k: None,
        sleep=lambda _s: None,
    )


_POLICY = StreamPolicy(
    dynamic_tools=False,
    stream_usage=False,
    prompt_profile="test",
    thinking_type=None,
)


def test_overflow_compacts_once_and_retries() -> None:
    client = _FailingClient(
        [
            Exception("This model's maximum context length is 8192 tokens"),
            RuntimeError("provider down"),
        ]
    )
    host = _overflow_host(client, compact_result=True)

    text, tools = _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    )

    assert (text, tools) == ("", [])
    assert client.calls == 2
    assert host.summarize_calls == [{"silent": True, "allow_rebase_fallback": False}]


def test_overflow_irreducible_context_does_not_retry() -> None:
    client = _FailingClient([Exception("context_length_exceeded")])
    host = _overflow_host(client, compact_result=False)

    text, tools = _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    )

    assert (text, tools) == ("", [])
    assert client.calls == 1
    assert len(host.summarize_calls) == 1
    # The authoritative surface is untouched by the failed overflow handling.
    assert host.messages == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_overflow_retry_not_attempted_twice() -> None:
    client = _FailingClient(
        [
            Exception("maximum context length exceeded"),
            Exception("maximum context length exceeded"),
        ]
    )
    host = _overflow_host(client, compact_result=True)

    _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    )

    # Second overflow inside the retried call must not compact again.
    assert client.calls == 2
    assert len(host.summarize_calls) == 1
