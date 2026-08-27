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

import pytest

from src.ai.context import context_shadow as shadow_adapter
from src.ai.context.context_checkpoint import ContextCheckpoint
from src.ai.context.context_events import ContextEventStore, messages_digest
from src.ai.context.context_shadow import ContextShadowCoordinator
from src.ai.context.history_compactor import (
    TOOL_RESULT_PRUNE_MIN_CHARS,
    HistoryCompactor,
)
from src.ai.model.model_request import StreamPolicy
from src.ai.model.model_streamer import ModelStreamer, _is_context_overflow
from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine, TurnCancelledError
from src.app.runtime import RuntimeContext
from src.storage.database import (
    Base,
    World,
    get_engine,
    session_scope,
)
from src.storage.persistence import load_game_artifacts


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'context-compaction.db'}"


def _coordinator(tmp_path: Path) -> ContextShadowCoordinator:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="world-compact", module_name="module-a"))
    return ContextShadowCoordinator(SimpleNamespace(world_id="world-compact", database_url=url))


def _game_engine(tmp_path: Path, world_id: str = "engine-compact") -> GameEngine:
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
        patch("src.ai.model.llm._get_glm", return_value=None),
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
    _messages, _snapshot, metadata = load_game_artifacts("slot_000", context=engine.context)
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
    answered = {message["tool_call_id"] for message in window if message.get("role") == "tool"}
    for message in window:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            for call in message["tool_calls"]:
                assert call["id"] in answered

    assert _summarize_once(engine) is True
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages


def test_summary_failure_keeps_original_surface_and_emits_no_checkpoint(tmp_path: Path) -> None:
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    engine.save("slot_000")
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    raw_before = len(shadow.store.load_event_payloads(session["id"]))
    surface_before = copy.deepcopy(engine.messages)

    with (
        patch("src.ai.model.llm._get_glm", return_value=None),
        patch.object(
            HistoryCompactor,
            "try_model",
            staticmethod(lambda _client, _model, _text: None),
        ),
    ):
        assert HistoryCompactor(engine).summarize(silent=True) is False

    # The failed summary cannot replace surface history with a fabricated
    # truncation note. Both the model-visible surface and event stream stay
    # exactly unchanged.
    events = shadow.store.load_event_payloads(session["id"])
    assert len(events) == raw_before
    assert _checkpoint_events(shadow.store, session["id"]) == []
    assert engine.messages == surface_before
    assert shadow.store.project(session["id"]) == engine.messages
    # Same session: no epoch churn from a failed compaction.
    assert shadow.store.session_for_world(engine.context.world_id)["id"] == session["id"]


def test_rebase_failure_never_replaces_live_surface(tmp_path: Path) -> None:
    """Shadow outage must reject compaction rather than lose raw history."""
    engine = _game_engine(tmp_path)
    engine.messages = _pair_history(15)
    before = copy.deepcopy(engine.messages)
    compactor = HistoryCompactor(engine)
    with (
        patch("src.ai.context.context_shadow.compact_engine", return_value=False),
        patch("src.ai.context.context_shadow.rebase_engine", return_value=False),
    ):
        assert (
            compactor.apply(
                engine.messages[0],
                '{"events":["x"]}',
                engine.messages[-4:],
                "test summarizer",
                silent=True,
            )
            is False
        )
    assert engine.messages == before


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
    _messages, _snapshot, metadata = load_game_artifacts("slot_000", context=engine.context)
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
        messages.append({"role": "tool", "tool_call_id": f"call_{index}", "content": content})
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


def test_prune_stale_authority_blocks_keeps_only_latest_snapshot(tmp_path: Path) -> None:
    """过期权威状态快照（每条玩家行动内嵌的当轮 JSON）被原地修剪，最新一条保留。"""
    engine = _game_engine(tmp_path)
    authority = (
        "[引擎权威状态｜仅供守秘人，不得复述]\n"
        '{"pc":{"hp":10},"flags":{}}\n约束：随身物品仅以上述 inventory 为准。'
    )
    messages = [{"role": "system", "content": "keeper"}]
    for index in range(4):
        messages.append(
            {
                "role": "user",
                "content": f"[玩家行动] 行动 {index}\n\n{authority}",
            }
        )
        messages.append({"role": "assistant", "content": f"结果 {index}"})
    engine.messages = messages
    engine.save("slot_000")

    pruned = HistoryCompactor(engine).prune_stale_authority_blocks()

    assert pruned == 3
    for index in (1, 3, 5):
        content = engine.messages[index]["content"]
        assert "权威状态快照已过期" in content
        assert f"行动 {(index - 1) // 2}" in content
        assert "约束" not in content
    # 最新一条玩家消息保留完整权威块
    assert "约束" in engine.messages[7]["content"]
    # 投影与影子一致；原文仍在追加日志里
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages
    assert any(
        "约束" in str(event["payload"].get("content") or "")
        for event in shadow.store.load_event_payloads(session["id"])
    )


def test_strip_asset_payloads_removes_only_data_uri() -> None:
    """投递载荷剥离：仅移除 asset_data_uri，保留 asset_url 等模型可用字段。"""
    import json

    from src.ai.tools.tool_aux_handlers import strip_asset_payloads

    payload = {
        "found": True,
        "entity_type": "npc",
        "entity_id": "whitroft",
        "asset_data_uri": "data:image/png;base64," + "A" * 5000,
        "asset_url": "/api/assets/mod/x.png",
    }
    stripped = strip_asset_payloads(json.dumps(payload, ensure_ascii=False))
    result = json.loads(stripped)
    assert "asset_data_uri" not in result
    assert result["asset_url"] == "/api/assets/mod/x.png"
    assert result["asset_delivered"] is True
    # 非 JSON / 无载荷输出原样返回
    assert strip_asset_payloads("plain text") == "plain text"
    no_asset = json.dumps({"found": True}, ensure_ascii=False)
    assert strip_asset_payloads(no_asset) == no_asset


def test_prune_asset_payloads_strips_history_regardless_of_recency(
    tmp_path: Path,
) -> None:
    """asset_data_uri 是投递载荷而非叙事上下文：不受 keep-recent 窗口保护。"""
    import json

    engine = _game_engine(tmp_path)
    data_uri = "data:image/png;base64," + "B" * 2048
    messages = [{"role": "system", "content": "keeper"}]
    for index in range(6):
        messages.append({"role": "user", "content": f"行动 {index}"})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": "show_handout", "arguments": "{}"},
                    }
                ],
            }
        )
        content = json.dumps(
            {
                "found": True,
                "entity_id": f"npc_{index}",
                "asset_data_uri": data_uri,
                "asset_url": f"/api/assets/mod/npc_{index}.png",
            },
            ensure_ascii=False,
        )
        messages.append({"role": "tool", "tool_call_id": f"call_{index}", "content": content})
    engine.messages = messages
    engine.save("slot_000")

    pruned = HistoryCompactor(engine).prune_asset_payloads()

    # 6 条全部剥离——包括 keep-recent 窗口内的最新一条（投递早已完成）。
    assert pruned == 6
    for message in engine.messages:
        content = str(message.get("content") or "")
        assert "asset_data_uri" not in content
    latest = json.loads(engine.messages[-1]["content"])
    assert latest["asset_url"] == "/api/assets/mod/npc_5.png"
    assert latest["asset_delivered"] is True
    # 投影与影子一致；原文仍在追加日志里
    shadow = shadow_adapter.for_engine(engine)
    session = shadow.store.session_for_world(engine.context.world_id)
    assert shadow.store.project(session["id"]) == engine.messages
    assert any(
        "asset_data_uri" in str(event["payload"].get("content") or "")
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
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        self.calls += 1
        raise self.errors.pop(0)


def _overflow_host(client: _FailingClient, compact_result: bool) -> SimpleNamespace:
    summarize_calls: list[dict] = []

    def _summarize(*, silent: bool, allow_rebase_fallback: bool) -> bool:
        summarize_calls.append({"silent": silent, "allow_rebase_fallback": allow_rebase_fallback})
        return compact_result

    host = SimpleNamespace(
        client=client,
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        context=SimpleNamespace(world_id="w-overflow", database_url=None),
        cb=SimpleNamespace(
            on_error=lambda *_a, **_k: None,
            on_narrative=lambda *_a, **_k: None,
            on_speaker_segment=lambda *_a, **_k: None,
        ),
        _tool_request_step=0,
        _active_turn_id=None,
        _append_model_diagnostic=lambda _d: None,
        _clear_active_stream=lambda _s: None,
        _set_active_stream=lambda _s: None,
        turn_cancellation_requested=lambda: False,
        turn_cancelled_error=TurnCancelledError,
        raise_if_turn_cancelled=lambda: None,
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


def test_overflow_retry_restores_skill_surface_before_rebuilding_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overflow retry must not send the compacted-away rule-less surface."""

    class OverflowThenSuccess:
        def __init__(self) -> None:
            self.calls = 0
            self.kwargs: list[dict] = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            self.calls += 1
            self.kwargs.append(kwargs)
            if self.calls == 1:
                raise Exception("context_length_exceeded")
            delta = SimpleNamespace(content="恢复后的叙述。", tool_calls=[])
            choice = SimpleNamespace(delta=delta, finish_reason="stop")
            return iter([SimpleNamespace(choices=[choice], usage=None)])

    client = OverflowThenSuccess()
    host = _overflow_host(client, compact_result=True)
    restored: list[object] = []

    def restore(engine) -> int:
        restored.append(engine)
        engine.messages.append({"role": "user", "content": "[restored deterministic skill]"})
        return 1

    monkeypatch.setattr("src.ai.skills.skill_activation.refresh_deterministic_skills", restore)
    assert _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    ) == ("恢复后的叙述。", [])
    assert restored == [host]
    assert client.calls == 2
    assert any(
        message.get("content") == "[restored deterministic skill]"
        for message in client.kwargs[1]["messages"]
    )


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

    _streamer(host).stream("test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False)

    # Second overflow inside the retried call must not compact again.
    assert client.calls == 2
    assert len(host.summarize_calls) == 1


def test_midstream_overflow_releases_slot_before_compaction_retry() -> None:
    """Pi runs a one-slot pool, so an iterator failure must not self-deadlock."""

    class Slot:
        def __init__(self) -> None:
            self.released = False

        def release(self, **_kwargs) -> None:
            self.released = True

    slots: list[Slot] = []

    def acquire(**_kwargs):
        slot = Slot()
        slots.append(slot)
        return slot

    class Client:
        def __init__(self) -> None:
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:

                def broken():
                    raise RuntimeError("context_length_exceeded")
                    yield None  # pragma: no cover - make this a generator

                return broken()
            assert slots[0].released is True
            return iter(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="恢复后的叙述。", tool_calls=[]),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    )
                ]
            )

    client = Client()
    host = _overflow_host(client, compact_result=True)
    with patch("src.ai.model.model_streamer.acquire_llm_slot", side_effect=acquire):
        assert _streamer(host).stream(
            "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
        ) == ("恢复后的叙述。", [])
    assert client.calls == 2
    assert len(host.summarize_calls) == 1


# ---------------------------------------------------------------------------
# H2 proactive wire-capacity preflight
# ---------------------------------------------------------------------------


class _OneChunkClient:
    def __init__(self) -> None:
        self.calls = 0
        self.kwargs: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        delta = SimpleNamespace(content="安全的叙述。", tool_calls=[])
        choice = SimpleNamespace(delta=delta, finish_reason="stop")
        return iter([SimpleNamespace(choices=[choice], usage=None)])


class _CapacityCompactor:
    def __init__(self, host: SimpleNamespace) -> None:
        self.host = host
        self.prune_calls = 0
        self.summary_calls = 0

    def prune_old_tool_results(self) -> int:
        self.prune_calls += 1
        # Simulate a verified old tool-result prune shrinking the actual
        # model surface.  The streamer must rebuild its request afterwards.
        self.host.messages[0]["content"] = "short retained context"
        return 1

    def summarize(self, *, silent: bool, allow_rebase_fallback: bool) -> bool:
        self.summary_calls += 1
        assert silent is True
        assert allow_rebase_fallback is False
        return False


def _capacity_host(client: _OneChunkClient, content: str) -> SimpleNamespace:
    diagnostics: list[dict] = []
    errors: list[str] = []
    host = SimpleNamespace(
        client=client,
        messages=[{"role": "system", "content": content}],
        context=SimpleNamespace(world_id="w-capacity", database_url=None),
        cb=SimpleNamespace(
            on_error=errors.append,
            on_narrative=lambda *_args: None,
            on_speaker_segment=lambda *_args: None,
        ),
        _tool_request_step=0,
        _active_turn_id=None,
        _append_model_diagnostic=diagnostics.append,
        _clear_active_stream=lambda _s: None,
        _set_active_stream=lambda _s: None,
        turn_cancellation_requested=lambda: False,
        turn_cancelled_error=TurnCancelledError,
        raise_if_turn_cancelled=lambda: None,
    )
    host.diagnostics = diagnostics
    host.errors = errors
    return host


def test_capacity_preflight_compacts_then_rebuilds_wire_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRPG_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("TRPG_CONTEXT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("TRPG_MAX_OUTPUT_TOKENS", "1000")
    client = _OneChunkClient()
    host = _capacity_host(client, "x" * 18_000)  # ~4500 input tokens: compact, not hard
    compactor = _CapacityCompactor(host)
    host._ensure_history_compactor = lambda: compactor

    text, tool_calls = _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    )

    assert (text, tool_calls) == ("安全的叙述。", [])
    assert client.calls == 1
    assert client.kwargs[0]["max_tokens"] == 1000
    assert compactor.prune_calls == 1
    assert compactor.summary_calls == 0
    preflight = next(
        item for item in host.diagnostics if item.get("event") == "context_capacity_preflight"
    )
    assert preflight["before"]["status"] == "compact"
    assert preflight["after"]["status"] == "within"
    envelope = next(
        item["request_envelope"] for item in host.diagnostics if "request_envelope" in item
    )
    assert envelope["capacity"]["max_output_tokens"] == 1000
    assert envelope["capacity"]["status"] == "within"
    assert "x" * 40 not in str(envelope)


def test_capacity_preflight_restores_skill_surface_before_final_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified preflight compaction restores controls before provider open."""
    monkeypatch.setenv("TRPG_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("TRPG_CONTEXT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("TRPG_MAX_OUTPUT_TOKENS", "1000")
    client = _OneChunkClient()
    host = _capacity_host(client, "x" * 18_000)
    host._ensure_history_compactor = lambda: _CapacityCompactor(host)
    restored: list[object] = []

    def restore(engine) -> int:
        restored.append(engine)
        engine.messages.append({"role": "user", "content": "[restored deterministic skill]"})
        return 1

    monkeypatch.setattr("src.ai.skills.skill_activation.refresh_deterministic_skills", restore)
    assert _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    ) == ("安全的叙述。", [])
    assert restored == [host]
    assert any(
        message.get("content") == "[restored deterministic skill]"
        for message in client.kwargs[0]["messages"]
    )


def test_capacity_hard_limit_never_opens_provider_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRPG_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("TRPG_CONTEXT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("TRPG_MAX_OUTPUT_TOKENS", "1000")
    client = _OneChunkClient()
    host = _capacity_host(client, "x" * 30_000)  # ~7500 input tokens >= hard 7192

    assert _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=True
    ) == ("", [])
    assert client.calls == 0
    assert host.errors == ["当前规则与历史过长，无法安全继续本轮；请稍后重试。"]
    diagnostic = next(
        item for item in host.diagnostics if item.get("status") == "capacity_irreducible"
    )
    assert diagnostic["request_envelope"]["capacity"]["status"] == "irreducible"
    assert "x" * 40 not in str(diagnostic)


def test_capacity_hard_limit_first_attempts_verified_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large but replaceable old surface is not intrinsically irreducible."""
    monkeypatch.setenv("TRPG_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("TRPG_CONTEXT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("TRPG_MAX_OUTPUT_TOKENS", "1000")
    client = _OneChunkClient()
    host = _capacity_host(client, "x" * 30_000)
    compactor = _CapacityCompactor(host)
    host._ensure_history_compactor = lambda: compactor

    assert _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    ) == ("安全的叙述。", [])
    assert client.calls == 1
    assert compactor.prune_calls == 1
    preflight = next(
        item for item in host.diagnostics if item.get("event") == "context_capacity_preflight"
    )
    assert preflight["before"]["status"] == "irreducible"
    assert preflight["after"]["status"] == "within"


def test_capacity_hard_limit_can_use_verified_summary_after_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard is a final verdict only after safe compaction has been tried."""
    monkeypatch.setenv("TRPG_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("TRPG_CONTEXT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("TRPG_MAX_OUTPUT_TOKENS", "1000")
    client = _OneChunkClient()
    host = _capacity_host(client, "x" * 30_000)

    class SummaryOnlyCompactor:
        prune_calls = 0
        summary_calls = 0

        def prune_old_tool_results(self) -> int:
            self.prune_calls += 1
            return 0

        def summarize(self, *, silent: bool, allow_rebase_fallback: bool) -> bool:
            self.summary_calls += 1
            assert silent is True
            assert allow_rebase_fallback is False
            # This stands in for a verified replace checkpoint: the next
            # request is rebuilt from the actual reduced model surface.
            host.messages[0]["content"] = "short retained context"
            return True

    compactor = SummaryOnlyCompactor()
    host._ensure_history_compactor = lambda: compactor

    assert _streamer(host).stream(
        "test-model", policy=_POLICY, enable_tools=False, retry_on_empty=False
    ) == ("安全的叙述。", [])
    assert client.calls == 1
    assert compactor.prune_calls == 1
    assert compactor.summary_calls == 1
    preflight = next(
        item for item in host.diagnostics if item.get("event") == "context_capacity_preflight"
    )
    assert preflight["before"]["status"] == "irreducible"
    assert preflight["after"]["status"] == "within"


def test_capacity_override_hard_limit_never_opens_provider_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRPG_CONTEXT_WINDOW_TOKENS", "8192")
    monkeypatch.setenv("TRPG_CONTEXT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("TRPG_MAX_OUTPUT_TOKENS", "1000")
    client = _OneChunkClient()
    host = _capacity_host(client, "short")

    assert _streamer(host).stream(
        "test-model",
        policy=_POLICY,
        enable_tools=False,
        messages_override=[{"role": "system", "content": "x" * 30_000}],
        retry_on_empty=True,
    ) == ("", [])
    assert client.calls == 0
    assert host.errors == ["当前规则与历史过长，无法安全继续本轮；请稍后重试。"]


def test_overflow_with_default_retry_does_not_fall_through_to_generic_retry() -> None:
    client = _FailingClient([Exception("context_length_exceeded")])
    host = _overflow_host(client, compact_result=False)

    assert _streamer(host).stream("test-model", policy=_POLICY, enable_tools=False) == ("", [])
    assert client.calls == 1
    assert len(host.summarize_calls) == 1


def test_second_overflow_after_safe_retry_is_not_retried_again() -> None:
    client = _FailingClient(
        [
            Exception("context_length_exceeded"),
            Exception("context_length_exceeded"),
        ]
    )
    host = _overflow_host(client, compact_result=True)

    assert _streamer(host).stream("test-model", policy=_POLICY, enable_tools=False) == ("", [])
    assert client.calls == 2
    assert len(host.summarize_calls) == 1
