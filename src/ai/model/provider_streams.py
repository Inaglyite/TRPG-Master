"""SSE conversion with incremental text/tools and explicit terminal validation."""

from __future__ import annotations

import json
import time

import httpx

from . import anthropic_protocol as anthropic
from . import responses_protocol as responses
from .wire_history import WireHistory


def sse_events(response: httpx.Response):
    lines: list[str] = []
    size = 0
    for line in response.iter_lines():
        if line.startswith("data:"):
            value = line[5:].lstrip(" ")
            lines.append(value)
            size += len(value)
            if size > 2_000_000:
                raise httpx.ReadError("模型流事件超出大小限制")
        elif not line and lines:
            data = "\n".join(lines)
            if data == "[DONE]":
                return
            yield json.loads(data)
            lines, size = [], 0
    if lines:
        data = "\n".join(lines)
        if data != "[DONE]":
            yield json.loads(data)


def packet(value: dict) -> bytes:
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()


class ProviderStream(httpx.SyncByteStream):
    def __init__(self, response: httpx.Response, protocol: str, history: WireHistory, model: str):
        self.response, self.protocol, self.history, self.model = response, protocol, history, model
        self.message_id = "provider-stream"

    def close(self) -> None:
        self.response.close()

    def _chunk(self, delta: dict, *, finish=None, usage=None) -> bytes:
        value = {"id": self.message_id, "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": self.model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            value["usage"] = usage
        return packet(value)

    def __iter__(self):
        try:
            if self.protocol == "anthropic_messages":
                yield from self._anthropic()
            elif self.protocol == "responses":
                yield from self._responses()
            else:
                yield from self._chat()
        finally:
            self.close()

    def _chat(self):
        message = {"role": "assistant", "content": ""}
        calls: dict[int, dict] = {}
        completed = False
        for event in sse_events(self.response):
            for choice in event.get("choices") or []:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                message["content"] += delta.get("content") or ""
                for key in ("reasoning_content", "reasoning_details"):
                    if isinstance(delta.get(key), (str, list)):
                        initial = [] if isinstance(delta[key], list) else ""
                        message[key] = message.get(key, initial) + delta[key]
                for call in delta.get("tool_calls") or []:
                    current = calls.setdefault(call["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    current["id"] += call.get("id") or ""
                    for key, value in (call.get("function") or {}).items():
                        if key in {"name", "arguments"}:
                            current["function"][key] += value or ""
                    if call.get("extra_content"):
                        current["extra_content"] = call["extra_content"]
                completed |= choice.get("finish_reason") in {"stop", "tool_calls"}
            yield packet(event)
        if completed and calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
            self.history.remember(message["tool_calls"], message)
        yield b"data: [DONE]\n\n"

    def _anthropic(self):
        blocks: dict[int, dict] = {}
        indexes: dict[int, int] = {}
        arguments: dict[int, str] = {}
        usage: dict = {}
        stop = None
        for event in sse_events(self.response):
            kind = event.get("type")
            if kind == "error":
                yield packet({"error": {"message": "Anthropic 生成流返回错误", "code": (event.get("error") or {}).get("type", "provider_error")}})
                return
            if kind == "message_start":
                message = event["message"]
                self.message_id, self.model = message["id"], message["model"]
                usage.update(message.get("usage") or {})
            elif kind == "content_block_start":
                index, block = event["index"], dict(event["content_block"])
                blocks[index] = block
                if block["type"] == "tool_use":
                    indexes[index] = len(indexes)
                    arguments[index] = json.dumps(block["input"], ensure_ascii=False) if block.get("input") else ""
                    yield self._chunk({"tool_calls": [{"index": indexes[index], "id": block["id"], "type": "function",
                                                      "function": {"name": block["name"], "arguments": arguments[index]}}]})
                elif block["type"] == "text" and block.get("text"):
                    yield self._chunk({"content": block["text"]})
            elif kind == "content_block_delta":
                index, delta = event["index"], event["delta"]
                if delta["type"] == "text_delta":
                    blocks[index]["text"] += delta["text"]
                    yield self._chunk({"content": delta["text"]})
                elif delta["type"] == "input_json_delta":
                    arguments[index] += delta["partial_json"]
                    yield self._chunk({"tool_calls": [{"index": indexes[index], "function": {"arguments": delta["partial_json"]}}]})
                elif delta["type"] == "thinking_delta":
                    blocks[index]["thinking"] += delta["thinking"]
                elif delta["type"] == "signature_delta":
                    blocks[index]["signature"] = blocks[index].get("signature", "") + delta["signature"]
            elif kind == "content_block_stop" and event["index"] in arguments:
                index = event["index"]
                if not arguments[index]:
                    arguments[index] = "{}"
                    yield self._chunk({"tool_calls": [{"index": indexes[index], "function": {"arguments": "{}"}}]})
                blocks[index]["input"] = json.loads(arguments[index] or "{}")
            elif kind == "message_delta":
                stop = event.get("delta", {}).get("stop_reason", stop)
                usage.update(event.get("usage") or {})
            elif kind == "message_stop":
                content = [blocks[index] for index in sorted(blocks)]
                if stop == "tool_use":
                    self.history.remember(anthropic.tool_calls(content), content)
                yield self._chunk({}, finish=anthropic.finish_reason(stop), usage=anthropic.usage_view(usage))
                yield b"data: [DONE]\n\n"
                return
        raise httpx.ReadError("Anthropic 生成流在 message_stop 前中断")

    def _responses(self):
        indexes: dict[str, int] = {}
        arguments: dict[str, str] = {}
        text_seen = False
        for event in sse_events(self.response):
            kind = event.get("type")
            if kind in {"error", "response.failed"}:
                yield packet({"error": {"message": "Responses 生成流返回错误", "code": "provider_error"}})
                return
            if kind == "response.created":
                self.message_id, self.model = event["response"]["id"], event["response"]["model"]
            elif kind == "response.output_text.delta":
                text_seen = True
                yield self._chunk({"content": event["delta"]})
            elif kind == "response.output_item.added" and event["item"]["type"] == "function_call":
                item = event["item"]
                indexes[item["id"]] = len(indexes)
                arguments[item["id"]] = item.get("arguments") or ""
                yield self._chunk({"tool_calls": [{"index": indexes[item["id"]], "id": item["call_id"], "type": "function",
                                                  "function": {"name": item["name"], "arguments": arguments[item["id"]]}}]})
            elif kind == "response.function_call_arguments.delta":
                item_id = event["item_id"]
                arguments[item_id] += event["delta"]
                yield self._chunk({"tool_calls": [{"index": indexes[item_id], "function": {"arguments": event["delta"]}}]})
            elif kind == "response.function_call_arguments.done":
                item_id = event["item_id"]
                complete = event["arguments"]
                if not complete.startswith(arguments[item_id]):
                    raise httpx.ReadError("Responses 工具参数的增量与最终值冲突")
                remainder = complete[len(arguments[item_id]):]
                if remainder:
                    yield self._chunk({"tool_calls": [{"index": indexes[item_id], "function": {"arguments": remainder}}]})
                    arguments[item_id] = complete
            elif kind in {"response.completed", "response.incomplete"}:
                body = event["response"]
                normalized = responses.response_body(body, self.history)
                message = normalized["choices"][0]["message"]
                if not text_seen and message["content"]:
                    yield self._chunk({"content": message["content"]})
                if not indexes and message.get("tool_calls"):
                    yield self._chunk({"tool_calls": [dict(call, index=i) for i, call in enumerate(message["tool_calls"])]})
                yield self._chunk({}, finish=responses.finish_reason(body), usage=responses.usage_view(body.get("usage") or {}))
                yield b"data: [DONE]\n\n"
                return
        raise httpx.ReadError("Responses 生成流在终止事件前中断")
