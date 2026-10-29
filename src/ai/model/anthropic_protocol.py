"""Pure conversion between internal Chat messages and Anthropic Messages."""

from __future__ import annotations

import json
import time

from .wire_history import WireHistory


def finish_reason(reason: str | None) -> str:
    return {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
            "max_tokens": "length", "refusal": "content_filter"}.get(reason, "length")


def usage_view(usage: dict) -> dict:
    cached = int(usage.get("cache_read_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    prompt = int(usage.get("input_tokens") or 0) + cached + written
    output = int(usage.get("output_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output,
            "prompt_tokens_details": {"cached_tokens": cached}, "cache_write_tokens": written}


def tool_calls(blocks: list[dict]) -> list[dict]:
    return [{"id": b["id"], "type": "function", "function": {
        "name": b["name"], "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)}}
        for b in blocks if b.get("type") == "tool_use"]


def _text_blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if content is None:
        return []
    if isinstance(content, list) and all(b.get("type") == "text" for b in content):
        return [{"type": "text", "text": b["text"]} for b in content if b.get("text")]
    raise ValueError("Anthropic 适配器当前仅接受文字游戏消息")


def request_body(body: dict, history: WireHistory) -> dict:
    system: list[dict] = []
    messages: list[dict] = []
    for message in body.get("messages", []):
        role = message["role"]
        if role in {"system", "developer"}:
            system.extend(_text_blocks(message.get("content")))
            continue
        if role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                       "content": str(message.get("content") or "")}]
        else:
            blocks = _text_blocks(message.get("content"))
            calls = message.get("tool_calls") or []
            if calls:
                saved = history.recall(calls)
                if isinstance(saved, list):
                    blocks = saved
                else:
                    for call in calls:
                        function = call["function"]
                        arguments = json.loads(function.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("工具参数必须是 JSON 对象")
                        blocks.append({"type": "tool_use", "id": call["id"],
                                       "name": function["name"], "input": arguments})
        if not blocks:
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})
    result = {"model": body["model"], "messages": messages,
              "max_tokens": body.get("max_completion_tokens", body.get("max_tokens", 8192)),
              "stream": bool(body.get("stream", False))}
    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_object":
        system.append({"type": "text", "text": "仅输出一个合法 JSON 对象，不要输出代码围栏或对象之外的文字。"})
    elif response_format.get("type") == "json_schema":
        result["output_config"] = {"format": {"type": "json_schema", "schema": response_format["json_schema"]["schema"]}}
    if system:
        result["system"] = system
    tools = []
    for tool in body.get("tools") or []:
        function = tool["function"]
        tools.append({"name": function["name"], "description": function.get("description", ""),
                      "input_schema": function.get("parameters") or {"type": "object"}})
    if tools:
        result["tools"] = tools
        choice = body.get("tool_choice", "auto")
        result["tool_choice"] = ({"type": "tool", "name": choice["function"]["name"]}
                                 if isinstance(choice, dict) else
                                 {"type": {"required": "any", "none": "none"}.get(choice, "auto")})
        if body.get("parallel_tool_calls") is False:
            result["tool_choice"]["disable_parallel_tool_use"] = True
    # Use provider defaults for sampling/thinking: newer Claude models restrict
    # sampling and signed thinking blocks must never be synthesized.
    return result


def response_body(body: dict, history: WireHistory) -> dict:
    blocks = body.get("content") or []
    calls = tool_calls(blocks)
    if body.get("stop_reason") == "tool_use":
        history.remember(calls, blocks)
    message = {"role": "assistant", "content": "".join(b.get("text", "") for b in blocks if b.get("type") == "text")}
    if calls:
        message["tool_calls"] = calls
    return {"id": body["id"], "model": body["model"], "object": "chat.completion",
            "created": int(time.time()), "choices": [{"index": 0, "message": message,
            "finish_reason": finish_reason(body.get("stop_reason"))}], "usage": usage_view(body.get("usage") or {})}
