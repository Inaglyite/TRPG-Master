"""H4 provider adapter：错误分级 / reasoning passback / 截断工具调用 fail-closed。"""

from __future__ import annotations

from types import SimpleNamespace

from src.ai.model.llm_concurrency import LlmBusyError
from src.ai.model.model_request import StreamPolicy, prepare_model_request
from src.ai.model.model_streamer import ModelStreamer
from src.ai.model.provider_adapter import (
    apply_reasoning_passback,
    classify_provider_error,
    extract_reasoning_text,
    register_reasoning_passback,
)

_POLICY = StreamPolicy(
    dynamic_tools=False,
    stream_usage=False,
    prompt_profile="test",
    thinking_type=None,
)


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


def test_classify_provider_error_stable_classes():
    assert classify_provider_error(_StatusError("unauthorized", 401)) == "auth"
    assert classify_provider_error(_StatusError("forbidden", 403)) == "auth"
    assert classify_provider_error(_StatusError("balance", 402)) == "quota"
    assert classify_provider_error(_StatusError("slow down", 429)) == "rate_limit"
    assert classify_provider_error(_StatusError("oops", 503)) == "server"
    assert classify_provider_error(APITimeoutError("timed out")) == "timeout"
    assert classify_provider_error(APIConnectionError("reset")) == "transport"
    assert (
        classify_provider_error(Exception("This model's maximum context length is 8192"))
        == "context_window"
    )
    assert classify_provider_error(LlmBusyError("busy")) == "busy"
    assert classify_provider_error(Exception("insufficient_quota")) == "quota"
    assert classify_provider_error(Exception("weird")) == "unknown"


def test_extract_reasoning_text_never_falls_back_to_text():
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="想一想"), finish_reason=None)]
    )
    assert extract_reasoning_text(chunk) == "想一想"
    assert extract_reasoning_text(SimpleNamespace(choices=[])) == ""
    assert extract_reasoning_text(SimpleNamespace(choices=[SimpleNamespace(delta=None)])) == ""
    assert extract_reasoning_text(SimpleNamespace()) == ""
    assert extract_reasoning_text(object()) == ""


def test_reasoning_passback_wire_copy_only():
    host = SimpleNamespace()
    register_reasoning_passback(host, ["call-1"], "隐藏推理")
    messages = [
        {"role": "system", "content": "s"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "dice_roll", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
    ]
    wire = apply_reasoning_passback(host, messages)
    assert wire[1]["reasoning_content"] == "隐藏推理"
    # host.messages 本体不被改写；无登记记录时原样返回（零拷贝）。
    assert "reasoning_content" not in messages[1]
    assert apply_reasoning_passback(SimpleNamespace(), messages) is messages


def _chunk(content=None, reasoning=None, tool_deltas=None, finish=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_deltas,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
    )


def _tool_delta(call_id=None, name=None, arguments=None, index=0):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _host(chunks):
    errors: list[str] = []

    class _Client:
        chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: iter(chunks))
        )

    host = SimpleNamespace(
        client=_Client(),
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        context=SimpleNamespace(world_id="w-adapter", database_url=None),
        cb=SimpleNamespace(
            on_error=errors.append,
            on_narrative=lambda *_a, **_k: None,
            on_speaker_segment=lambda *_a, **_k: None,
        ),
        _tool_request_step=0,
        _active_turn_id=None,
        _append_model_diagnostic=lambda _d: None,
        _clear_active_stream=lambda _s: None,
        _set_active_stream=lambda _s: None,
        turn_cancellation_requested=lambda: False,
        turn_cancelled_error=RuntimeError,
        raise_if_turn_cancelled=lambda: None,
    )
    host.errors = errors
    return host


def _stream(host, policy=_POLICY):
    streamer = ModelStreamer(
        host,
        log_error=lambda _m: None,
        log_model_call=lambda *_a, **_k: None,
        sleep=lambda _s: None,
    )
    return streamer.stream(
        "test-model",
        policy=policy,
        enable_tools=True,
        retry_on_empty=False,
    )


def test_stream_registers_reasoning_passback_by_final_call_id():
    host = _host(
        [
            _chunk(reasoning="先分析局面"),
            _chunk(tool_deltas=[_tool_delta(call_id="call_", name="dice_")]),
            _chunk(tool_deltas=[_tool_delta(call_id="1", name="roll", arguments='{"expr"')]),
            _chunk(tool_deltas=[_tool_delta(arguments=':"1d100"}')]),
            _chunk(finish="tool_calls"),
        ]
    )
    _text, tool_calls = _stream(host)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_1"
    store = host.__dict__["_reasoning_passback"]
    assert store["call_1"] == "先分析局面"


def test_stream_truncated_tool_calls_fail_closed():
    host = _host(
        [
            _chunk(tool_deltas=[_tool_delta(call_id="c1", name="dice_roll", arguments='{"expr"')]),
            _chunk(finish="length"),
        ]
    )
    text, tool_calls = _stream(host)
    assert tool_calls == []
    assert text == ""
    assert any("截断" in message for message in host.errors)


def test_prepare_request_passback_only_on_wire_copy():
    host = _host([])
    register_reasoning_passback(host, ["call_1"], "隐藏推理")
    host.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "dice_roll", "arguments": "{}"},
                }
            ],
        }
    )
    host.messages.append({"role": "tool", "tool_call_id": "call_1", "content": "{}"})
    thinking = StreamPolicy(
        dynamic_tools=False,
        stream_usage=False,
        prompt_profile="test",
        thinking_type="enabled",
    )
    prepared = prepare_model_request(
        host,
        "test-model",
        policy=thinking,
        system_overlay=None,
        system_prompt_override=None,
        enable_tools=True,
        temperature=0.7,
        messages_override=None,
    )
    wire = prepared.provider_kwargs["messages"]
    assistant = next(m for m in wire if m.get("role") == "assistant")
    # 默认 BASE_URL 是 DeepSeek：wire 副本带 passback，持久面不带。
    assert assistant.get("reasoning_content") == "隐藏推理"
    persisted = next(m for m in host.messages if m.get("role") == "assistant")
    assert "reasoning_content" not in persisted
