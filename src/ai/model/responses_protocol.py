"""Pure conversion between internal Chat messages and the Responses API."""

from __future__ import annotations

import time

from .wire_history import WireHistory


def tool_calls(output: list[dict]) -> list[dict]:
    return [{"id": item["call_id"], "type": "function", "function": {
        "name": item["name"], "arguments": item.get("arguments", "{}")}}
        for item in output if item.get("type") == "function_call"]


def usage_view(usage: dict) -> dict:
    prompt, completion = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": usage.get("total_tokens", prompt + completion),
            "prompt_tokens_details": usage.get("input_tokens_details") or {},
            "completion_tokens_details": usage.get("output_tokens_details") or {}}


def finish_reason(body: dict) -> str:
    if body.get("status") == "incomplete":
        return "content_filter" if (body.get("incomplete_details") or {}).get("reason") == "content_filter" else "length"
    if body.get("status") != "completed":
        return "length"
    return "tool_calls" if tool_calls(body.get("output") or []) else "stop"


def request_body(body: dict, history: WireHistory) -> dict:
    items: list[dict] = []
    for message in body.get("messages", []):
        role = message["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": str(message.get("content") or "")})
            continue
        calls = message.get("tool_calls") or []
        saved = history.recall(calls) if calls else None
        if isinstance(saved, list):
            items.extend(saved)
            continue
        content = message.get("content")
        if content:
            items.append({"role": role, "content": content})
        for call in calls:
            items.append({"type": "function_call", "call_id": call["id"],
                          "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    result = {"model": body["model"], "input": items, "store": False,
              "stream": bool(body.get("stream", False)),
              "max_output_tokens": body.get("max_completion_tokens", body.get("max_tokens", 8192))}
    for key in ("temperature", "top_p", "parallel_tool_calls"):
        if key in body:
            result[key] = body[key]
    if body.get("reasoning_effort"):
        result["reasoning"] = {"effort": body["reasoning_effort"]}
        result["include"] = ["reasoning.encrypted_content"]
    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_object":
        result["text"] = {"format": {"type": "json_object"}}
    elif response_format.get("type") == "json_schema":
        result["text"] = {"format": {"type": "json_schema", **response_format["json_schema"]}}
    tools = [{"type": "function", **tool["function"]} for tool in body.get("tools") or []]
    if tools:
        for tool in tools:
            tool.setdefault("strict", False)
        result["tools"] = tools
        choice = body.get("tool_choice", "auto")
        result["tool_choice"] = ({"type": "function", "name": choice["function"]["name"]}
                                 if isinstance(choice, dict) else choice)
    return result


def response_body(body: dict, history: WireHistory) -> dict:
    output = body.get("output") or []
    calls = tool_calls(output)
    if body.get("status") == "completed":
        history.remember(calls, output)
    content = "".join(block.get("text", "") for item in output if item.get("type") == "message"
                      for block in item.get("content", []) if block.get("type") == "output_text")
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"id": body["id"], "model": body["model"], "created": int(body.get("created_at") or time.time()),
            "object": "chat.completion", "choices": [{"index": 0, "message": message,
            "finish_reason": finish_reason(body)}], "usage": usage_view(body.get("usage") or {})}
