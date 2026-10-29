"""Real SDK + in-memory HTTP boundary: wire contracts without provider credentials."""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from openai import APIError, BadRequestError, OpenAI

from src.ai.model.provider_transport import ProviderTransport
from src.app.settings_service import (
    ConnectionProbeError,
    _probe_chat,
    _probe_tool_calling,
    _readable_error,
)


def chat_reply(model="model", *, content="好", calls=None):
    return {"id": "chat-1", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop",
                         "message": {"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})}}]}


def client(handler, *, kind="openai_compatible", protocol="chat_completions", base="https://models.example/v1"):
    transport = ProviderTransport(httpx.MockTransport(handler), provider_kind=kind, api_format=protocol)
    return OpenAI(api_key="test-key-never-log", base_url=base, max_retries=0,
                  http_client=httpx.Client(transport=transport, follow_redirects=False, trust_env=False))


@pytest.mark.parametrize("kind,model,required,absent", [
    ("deepseek", "deepseek-flash", {"thinking": {"type": "disabled"}, "temperature": 0}, []),
    ("moonshot", "kimi-k3", {"max_completion_tokens": 1024, "reasoning_effort": "low"}, ["temperature", "thinking", "max_tokens"]),
    ("moonshot", "kimi-k2.6", {"thinking": {"type": "disabled"}}, ["temperature"]),
    ("moonshot", "kimi-k2.7-code", {}, ["temperature", "thinking"]),
    ("qwen", "qwen3.6-plus", {"enable_thinking": False}, ["thinking"]),
    ("zhipu", "glm-5.3", {"thinking": {"type": "disabled"}}, ["temperature"]),
    ("doubao", "ep-test", {"thinking": {"type": "disabled"}}, []),
    ("gemini", "gemini-3.8-flash", {"reasoning_effort": "low"}, ["thinking", "temperature"]),
    ("openai", "gpt-5", {"max_completion_tokens": 1024}, ["max_tokens", "temperature", "thinking"]),
    ("openai", "gpt-5-chat-latest", {"temperature": 0}, ["thinking", "reasoning_effort"]),
    ("openrouter", "moonshotai/kimi-k3", {"reasoning": {"effort": "low"}, "max_tokens": 1024}, ["temperature", "thinking", "reasoning_effort"]),
    ("siliconflow", "Qwen/Qwen3-32B", {"enable_thinking": False}, ["thinking"]),
    ("openai_compatible", "my-private-model", {"temperature": 0, "max_tokens": 1024}, ["thinking"]),
])
def test_provider_wire_parameters(kind, model, required, absent):
    seen = []

    def handler(request):
        body = json.loads(request.read())
        seen.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key-never-log"
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(200, json=chat_reply(model))

    messages = [{"role": "user", "content": "固定测试"}]
    before = copy.deepcopy(messages)
    with client(handler, kind=kind) as sdk:
        result = sdk.with_options(timeout=7).chat.completions.create(
            model=model, messages=messages, max_tokens=1024, temperature=0,
            extra_body={"thinking": {"type": "disabled"}})
        assert result.choices[0].message.content == "好"
    assert messages == before
    for key, value in required.items():
        assert seen[0][key] == value
    assert all(key not in seen[0] for key in absent)


def test_known_host_overrides_old_provider_hint_without_changing_destination():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=chat_reply("kimi-k3"))

    with client(handler, kind="deepseek", base="https://api.moonshot.cn/v1") as sdk:
        sdk.chat.completions.create(model="kimi-k3", messages=[{"role": "user", "content": "好"}], temperature=0)
    assert "thinking" not in json.loads(seen[0].read())
    assert seen[0].url.host == "api.moonshot.cn"


def test_k3_named_tool_choice_keeps_required_call_semantics():
    seen = []
    calls = [{"id": "call-1", "type": "function", "function": {"name": "adjudicate_action", "arguments": "{}"}}]

    def handler(request):
        seen.append(json.loads(request.read()))
        return httpx.Response(200, json=chat_reply("kimi-k3", calls=calls))

    with client(handler, kind="moonshot") as sdk:
        result = sdk.chat.completions.create(
            model="kimi-k3", messages=[{"role": "user", "content": "调用指定工具"}],
            tools=[{"type": "function", "function": {"name": "adjudicate_action", "parameters": {"type": "object"}}}],
            tool_choice={"type": "function", "function": {"name": "adjudicate_action"}},
        )
    assert seen[0]["tool_choice"] == "required"
    assert result.choices[0].message.tool_calls[0].function.name == "adjudicate_action"


class FragmentedStream(httpx.SyncByteStream):
    def __init__(self, payload):
        self.payload, self.closed = payload, False

    def __iter__(self):
        for offset in range(0, len(self.payload), 7):
            yield self.payload[offset:offset + 7]

    def close(self):
        self.closed = True


def sse(events):
    return FragmentedStream(b"".join(("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode() for event in events))


def anthropic_result(blocks=None, stop="end_turn"):
    return {"id": "msg-test", "model": "claude-test", "type": "message", "role": "assistant",
            "content": blocks or [{"type": "text", "text": "好"}], "stop_reason": stop,
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2, "output_tokens": 5}}


def test_anthropic_nonstream_tool_round_trip_auth_usage_and_private_signature():
    seen = []
    blocks = [{"type": "thinking", "thinking": "private", "signature": "signed-private"},
              {"type": "text", "text": "先检查"},
              {"type": "tool_use", "id": "toolu_1", "name": "probe_noop", "input": {"echo": "ok"}}]

    def handler(request):
        assert request.headers["x-api-key"] == "test-key-never-log"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        assert request.url.path == "/v1/messages"
        seen.append(json.loads(request.read()))
        return httpx.Response(200, json=anthropic_result(blocks, "tool_use") if len(seen) == 1 else anthropic_result())

    with client(handler, protocol="anthropic_messages", base="https://models.example") as sdk:
        result = sdk.chat.completions.create(model="claude-test", messages=[{"role": "system", "content": "私有系统"},
            {"role": "user", "content": "测试"}], response_format={"type": "json_object"},
            tools=[{"type": "function", "function": {"name": "probe_noop", "parameters": {"type": "object"}}}],
            tool_choice={"type": "function", "function": {"name": "probe_noop"}}, max_tokens=1024)
        assert result.choices[0].finish_reason == "tool_calls"
        assert result.usage.prompt_tokens == 15 and result.usage.total_tokens == 20
        assert "signed-private" not in result.model_dump_json()
        message = result.choices[0].message.model_dump(exclude_none=True)
        pristine = copy.deepcopy(message)
        sdk.chat.completions.create(model="claude-test", messages=[{"role": "user", "content": "测试"}, message,
            {"role": "tool", "tool_call_id": "toolu_1", "content": "执行成功"}])
        assert message == pristine
    assert seen[0]["tool_choice"] == {"type": "tool", "name": "probe_noop"}
    assert seen[0]["tools"][0]["input_schema"] == {"type": "object"}
    assert seen[1]["messages"][1]["content"] == blocks
    assert seen[1]["messages"][-1]["content"][0]["type"] == "tool_result"


@pytest.mark.parametrize("base,path", [("https://models.example/v1", "/v1/models"), ("https://models.example", "/v1/models"), ("https://models.example/proxy/v1", "/proxy/v1/models")])
def test_anthropic_models_endpoint(base, path):
    def handler(request):
        assert request.url.path == path
        return httpx.Response(200, json={"data": [{"id": "claude-test"}], "has_more": False})

    with client(handler, protocol="anthropic_messages", base=base) as sdk:
        assert sdk.models.list().data[0].id == "claude-test"


def anthropic_events(*, stop="end_turn", tool=False, terminal=True):
    result = [dict(type="message_start", message={"id": "msg-1", "model": "claude-test", "usage": {"input_tokens": 9}})]
    if tool:
        result += [dict(type="content_block_start", index=0, content_block={"type": "tool_use", "id": "toolu_2", "name": "probe_noop", "input": {}}),
                   dict(type="content_block_delta", index=0, delta={"type": "input_json_delta", "partial_json": '{"echo":'}),
                   dict(type="content_block_delta", index=0, delta={"type": "input_json_delta", "partial_json": '"ok"}'}),
                   dict(type="content_block_stop", index=0)]
    else:
        result += [dict(type="content_block_start", index=0, content_block={"type": "text", "text": ""}),
                   dict(type="ping"), dict(type="content_block_delta", index=0, delta={"type": "text_delta", "text": "好"}),
                   dict(type="content_block_stop", index=0)]
    result.append(dict(type="message_delta", delta={"stop_reason": "tool_use" if tool else stop}, usage={"output_tokens": 4}))
    if terminal:
        result.append(dict(type="message_stop"))
    return result


def test_anthropic_stream_tool_deltas_and_close():
    raw = sse(anthropic_events(tool=True))
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=raw), protocol="anthropic_messages") as sdk:
        stream = sdk.chat.completions.create(model="claude-test", messages=[{"role": "user", "content": "好"}], stream=True)
        chunks = list(stream)
    tool_deltas = [c.choices[0].delta.tool_calls for c in chunks if c.choices[0].delta.tool_calls]
    assert tool_deltas[0][0].id == "toolu_2"
    assert "".join(calls[0].function.arguments or "" for calls in tool_deltas) == '{"echo":"ok"}'
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
    assert chunks[-1].usage.total_tokens == 13 and raw.closed


@pytest.mark.parametrize("protocol", ["chat_completions", "anthropic_messages", "responses"])
def test_stream_early_close_closes_underlying_response(protocol):
    events = anthropic_events() if protocol == "anthropic_messages" else responses_events() if protocol == "responses" else chat_events()
    raw = sse(events)
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=raw), protocol=protocol) as sdk:
        stream = sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True)
        next(iter(stream))
        stream.close()
        assert raw.closed


def responses_result(*, tools=False, status="completed"):
    output = [{"id": "m-1", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "好", "annotations": []}]}]
    if tools:
        output = [{"id": "r-1", "type": "reasoning", "encrypted_content": "opaque-private", "summary": []},
                  {"id": "fc-1", "type": "function_call", "call_id": "call_1", "name": "probe_noop", "arguments": '{"echo":"ok"}'}]
    return {"id": "resp-1", "model": "gpt-5", "created_at": 1, "status": status, "output": output,
            "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12, "input_tokens_details": {"cached_tokens": 3}}}


def test_responses_nonstream_tool_chain_and_encrypted_reasoning():
    seen = []

    def handler(request):
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key-never-log"
        seen.append(json.loads(request.read()))
        return httpx.Response(200, json=responses_result(tools=len(seen) == 1))

    with client(handler, protocol="responses") as sdk:
        result = sdk.chat.completions.create(model="gpt-5", messages=[{"role": "system", "content": "JSON"}, {"role": "user", "content": "好"}],
            response_format={"type": "json_object"}, max_tokens=1024, temperature=0)
        assert result.choices[0].finish_reason == "tool_calls"
        assert "opaque-private" not in result.model_dump_json()
        message = result.choices[0].message.model_dump(exclude_none=True)
        sdk.chat.completions.create(model="gpt-5", messages=[{"role": "user", "content": "好"}, message,
            {"role": "tool", "tool_call_id": "call_1", "content": "执行成功"}])
    assert seen[0]["store"] is False
    assert seen[0]["max_output_tokens"] == 1024 and "temperature" not in seen[0]
    assert seen[0]["text"]["format"] == {"type": "json_object"}
    assert any(item.get("encrypted_content") == "opaque-private" for item in seen[1]["input"])
    assert seen[1]["input"][-1]["type"] == "function_call_output"


def responses_events(*, tools=False, status="completed", terminal=True):
    response = responses_result(tools=tools, status=status)
    events = [{"type": "response.created", "response": {"id": "resp-1", "model": "gpt-5"}}]
    if tools:
        item = {**response["output"][-1], "arguments": ""}
        events += [{"type": "response.output_item.added", "output_index": 1, "item": item},
                   {"type": "response.function_call_arguments.delta", "item_id": "fc-1", "delta": '{"echo":'},
                   {"type": "response.function_call_arguments.delta", "item_id": "fc-1", "delta": '"ok"}'}]
    else:
        events.append({"type": "response.output_text.delta", "delta": "好"})
    if terminal:
        events.append({"type": "response.incomplete" if status == "incomplete" else "response.completed", "response": response})
    return events


def chat_events(*, finish="stop", content="好"):
    return [{"id": "chat-1", "object": "chat.completion.chunk", "created": 1, "model": "test",
             "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]}]


@pytest.mark.parametrize("protocol,events,finish", [
    ("anthropic_messages", anthropic_events(), "stop"),
    ("anthropic_messages", anthropic_events(stop="max_tokens"), "length"),
    ("responses", responses_events(), "stop"),
    ("responses", responses_events(status="incomplete"), "length"),
    ("responses", responses_events(tools=True), "tool_calls"),
])
def test_native_stream_terminal_and_usage(protocol, events, finish):
    raw = sse(events)
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=raw), protocol=protocol) as sdk:
        chunks = list(sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True))
    assert chunks[-1].choices[0].finish_reason == finish
    assert chunks[-1].usage.total_tokens > 0
    if finish == "stop":
        assert "".join(chunk.choices[0].delta.content or "" for chunk in chunks) == "好"
    assert raw.closed


@pytest.mark.parametrize("protocol,events", [("anthropic_messages", anthropic_events(terminal=False)), ("responses", responses_events(terminal=False))])
def test_native_stream_incomplete_connection_fails(protocol, events):
    raw = sse(events)
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=raw), protocol=protocol) as sdk:
        with pytest.raises(httpx.ReadError):
            list(sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True))
    assert raw.closed


@pytest.mark.parametrize("protocol", ["anthropic_messages", "responses"])
def test_native_stream_error_not_success(protocol):
    raw = sse([{"type": "error", "error": {"type": "overloaded_error", "message": "private"}}])
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=raw), protocol=protocol) as sdk:
        with pytest.raises(APIError):
            list(sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True))
    assert raw.closed


def test_probe_requires_visible_complete_output_and_closes_stream():
    raw = sse(chat_events(finish="length", content=""))
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=raw)) as sdk:
        with pytest.raises(ConnectionProbeError, match="完整"):
            _probe_chat(sdk, "kimi-k3")
    assert raw.closed


def test_probe_rejects_unexpected_tool_and_error_never_exposes_credentials():
    wrong = [{"id": "call-x", "type": "function", "function": {"name": "delete_all", "arguments": "{}"}}]
    with client(lambda _: httpx.Response(200, json=chat_reply(calls=wrong))) as sdk:
        with pytest.raises(ConnectionProbeError):
            _probe_tool_calling(sdk, "test")
    request = httpx.Request("POST", "https://models.example")
    exc = BadRequestError("test-key-never-log", response=httpx.Response(400, request=request), body={"secret": "test-key-never-log"})
    assert "参数" in _readable_error(exc)
    assert "test-key" not in _readable_error(exc)


def test_probe_uses_auto_tool_choice_for_always_thinking_models():
    def handler(request):
        body = json.loads(request.read())
        assert body["tool_choice"] == "auto"
        assert "temperature" not in body
        return httpx.Response(200, json=chat_reply(calls=[{"id": "call-p", "type": "function", "function": {
            "name": "probe_noop", "arguments": '{"echo":"ok"}'}}]))

    with client(handler) as sdk:
        _probe_tool_calling(sdk, "kimi-k3")


def test_chat_replays_reasoning_and_tool_signatures_only_for_matching_calls():
    events = chat_events(finish=None, content="")
    events[0]["choices"][0]["delta"] = {"reasoning_content": None}
    reasoning = copy.deepcopy(events[0])
    reasoning["choices"][0]["delta"] = {"reasoning_content": "private-thinking"}
    tool = copy.deepcopy(events[0])
    tool["choices"][0] = {"index": 0, "finish_reason": "tool_calls", "delta": {"tool_calls": [{"index": 0, "id": "call-signed", "type": "function",
        "function": {"name": "probe_noop", "arguments": '{"echo":"ok"}'}, "extra_content": {"google": {"thought_signature": "private-signature"}}}]}}
    seen = []

    def handler(request):
        body = json.loads(request.read())
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=sse(events + [reasoning, tool]))
        return httpx.Response(200, json=chat_reply())

    assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "call-signed", "type": "function", "function": {"name": "probe_noop", "arguments": '{"echo":"ok"}'}}]}
    before = copy.deepcopy(assistant)
    with client(handler) as sdk:
        list(sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True))
        sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}, assistant, {"role": "tool", "tool_call_id": "call-signed", "content": "ok"}])
        changed = copy.deepcopy(assistant)
        changed["tool_calls"][0]["function"]["arguments"] = '{"echo":"different"}'
        sdk.chat.completions.create(model="test", messages=[changed])
    assert assistant == before
    assert seen[1]["messages"][1]["reasoning_content"] == "private-thinking"
    assert seen[1]["messages"][1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "private-signature"
    assert "reasoning_content" not in seen[2]["messages"][0]


def test_anthropic_empty_argument_object_is_complete_json():
    events = [anthropic_events()[0], {"type": "content_block_start", "index": 2, "content_block": {
        "type": "tool_use", "id": "toolu-empty", "name": "get_state", "input": {}}},
        {"type": "content_block_stop", "index": 2}, {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"}]
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=sse(events)), protocol="anthropic_messages") as sdk:
        chunks = list(sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True))
    arguments = "".join(call.function.arguments or "" for chunk in chunks for call in chunk.choices[0].delta.tool_calls or [])
    assert json.loads(arguments) == {}


def test_responses_argument_done_completes_missing_delta_suffix():
    events = responses_events(tools=True)
    events.pop(-2)
    events.insert(-1, {"type": "response.function_call_arguments.done", "item_id": "fc-1", "arguments": '{"echo":"ok"}'})
    with client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=sse(events)), protocol="responses") as sdk:
        chunks = list(sdk.chat.completions.create(model="test", messages=[{"role": "user", "content": "好"}], stream=True))
    arguments = "".join(call.function.arguments or "" for chunk in chunks for call in chunk.choices[0].delta.tool_calls or [])
    assert json.loads(arguments) == {"echo": "ok"}
