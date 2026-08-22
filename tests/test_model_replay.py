"""H4 离线 replay / gold turns / 故障注入。

gold fixture 是归一化 chunk 记录（JSONL），回放走真实 ModelStreamer 全链路
（prepare → issue → 流归一化 → 工具收口），断言输出确定性。故障注入验证
错误分级、重试记录与 surface 不破坏。
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.model_fixtures import (
    build_chunk,
    load_fixture,
    replay_client,
    run_stream,
    streamer_host,
)


def _public_calls(tool_calls: list[dict]) -> list[tuple[str, str, str]]:
    return [
        (
            str(call.get("id") or ""),
            str((call.get("function") or {}).get("name") or ""),
            str((call.get("function") or {}).get("arguments") or ""),
        )
        for call in tool_calls
    ]


def test_gold_opening_story_replay():
    host = streamer_host(replay_client(load_fixture("opening_story.jsonl")))
    text, tool_calls = run_stream(host)
    assert tool_calls == []
    assert text == "雾气从档案馆的窗缝里渗进来，你手中的信件还带着墨香。\n\n书桌上的台灯忽明忽暗。"
    # usage 归一化进入诊断（含 cache 分离字段）。
    usage = host.diagnostics[-1]["usage"]
    assert usage["prompt_tokens"] == 1200
    assert usage["prompt_cache_hit_tokens"] == 800
    assert host.diagnostics[-1]["status"] == "completed"


def test_gold_check_turn_replay_with_reasoning_passback():
    host = streamer_host(replay_client(load_fixture("check_turn.jsonl")))
    _text, tool_calls = run_stream(host)
    assert _public_calls(tool_calls) == [
        ("call_search", "skill_check", '{"skill":"侦查"}')
    ]
    # reasoning 以最终 call id 登记 passback，不出现在任何公开输出。
    assert host.__dict__["_reasoning_passback"]["call_search"] == "玩家要搜查书桌，需要一次侦查检定。"
    assert "侦查检定" not in "".join(host.narratives)


def test_gold_combat_turn_replay():
    host = streamer_host(replay_client(load_fixture("combat_turn.jsonl")))
    text, tool_calls = run_stream(host)
    assert "管家的影子扑了过来！" in text
    assert _public_calls(tool_calls) == [
        ("call_fight", "combat_action", '{"action": "闪避"}')
    ]


def test_replay_is_deterministic_across_runs():
    first = streamer_host(replay_client(load_fixture("check_turn.jsonl")))
    second = streamer_host(replay_client(load_fixture("check_turn.jsonl")))
    text1, calls1 = run_stream(first)
    text2, calls2 = run_stream(second)
    assert text1 == text2
    assert _public_calls(calls1) == _public_calls(calls2)


def test_recording_roundtrip_preserves_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_RECORD_MODEL_STREAMS", str(tmp_path))
    host = streamer_host(replay_client(load_fixture("check_turn.jsonl")))
    text1, calls1 = run_stream(host)
    recorded = list(tmp_path.glob("*.jsonl"))
    assert len(recorded) == 1
    # 录制文件可以直接作为新的回放事实来源，输出完全一致。
    import json

    records = [
        json.loads(line)
        for line in recorded[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    host2 = streamer_host(replay_client(records))
    text2, calls2 = run_stream(host2)
    assert text1 == text2
    assert _public_calls(calls1) == _public_calls(calls2)


# ---- 故障注入 ----------------------------------------------------------------


class _ScriptedClient:
    """outcome：Exception（create 抛错）/ list[dict]（正常流）/ ("cut", list, exc)（流中段抛错）。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple) and outcome[0] == "cut":
            _tag, records, exc = outcome

            def _gen():
                for record in records:
                    yield build_chunk(record)
                raise exc

            return _gen()
        return iter(build_chunk(record) for record in outcome)


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def test_connect_5xx_classified_and_retried_once():
    client = _ScriptedClient(
        [_StatusError("bad gateway", 502), load_fixture("opening_story.jsonl")]
    )
    host = streamer_host(client)
    text, _calls = run_stream(host, retry_on_empty=True)
    assert "雾气从档案馆" in text
    assert client.calls == 2
    first = host.diagnostics[0]
    assert first["status"] == "request_error"
    assert first["error_class"] == "server"
    retries = host.__dict__["_turn_model_retries"]
    assert retries == [{"reason": "connect_failed", "error_class": "server", "backoff_ms": 400}]


def test_midstream_cut_preserves_partial_text_and_classifies():
    client = _ScriptedClient(
        [("cut", [{"content": "前半段叙述。"}], APIConnectionError_for_test("reset"))]
    )
    host = streamer_host(client)
    text, tool_calls = run_stream(host)
    assert tool_calls == []
    assert "前半段叙述。" in text
    assert host.diagnostics[-1]["status"] == "transport_error"
    assert host.diagnostics[-1]["error_class"] == "transport"
    assert any("中断" in message for message in host.errors)


class APIConnectionError_for_test(Exception):
    pass


def test_midstream_cut_without_content_retries_with_note():
    client = _ScriptedClient(
        [
            ("cut", [], APIConnectionError_for_test("reset")),
            load_fixture("opening_story.jsonl"),
        ]
    )
    host = streamer_host(client)
    text, _calls = run_stream(host, retry_on_empty=True)
    assert "雾气从档案馆" in text
    retries = host.__dict__["_turn_model_retries"]
    assert retries == [
        {"reason": "empty_stream", "error_class": "transport", "backoff_ms": 400}
    ]


def test_context_overflow_compacts_once_and_is_classified():
    compact_calls: list[dict] = []

    def _summarize(*, silent: bool, allow_rebase_fallback: bool) -> bool:
        compact_calls.append({"silent": silent})
        return True

    client = _ScriptedClient(
        [
            Exception("This model's maximum context length is 8192"),
            load_fixture("opening_story.jsonl"),
        ]
    )
    host = streamer_host(client)
    host._ensure_history_compactor = lambda: SimpleNamespace(summarize=_summarize)
    text, _calls = run_stream(host)
    assert "雾气从档案馆" in text
    assert len(compact_calls) == 1
    assert host.diagnostics[0]["error_class"] == "context_window"
    retries = host.__dict__["_turn_model_retries"]
    assert retries[0]["reason"] == "context_overflow"
