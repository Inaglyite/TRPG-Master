"""Provider streaming boundary for conversational model calls."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .lorebook import estimate_text_tokens
from .persistence import normalize_tool_message_history
from .tools import MODEL_TOOLS, model_tools_for

_INTERNAL_NARRATIVE_PATTERNS = (
    re.compile(r"(?:让我|我)?先确认(?:一下)?当前(?:的)?信息边界[。.!！]?\s*"),
    re.compile(r"按玩家(?:的)?明确意图[^。！？\n]*[。！？]?\s*"),
    re.compile(
        r"需要(?:确认|记录|写入)[^。！？\n]*(?:world_state|世界状态)[^。！？\n]*[。！？]?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"当前\s*SAN\s*=\s*\d+\s*[？?][^。！？\n]*(?:应该|不对)[^。！？\n]*[。！？]?\s*",
        re.IGNORECASE,
    ),
)


def sanitize_visible_narrative(text: str) -> str:
    for pattern in _INTERNAL_NARRATIVE_PATTERNS:
        text = pattern.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def take_complete_sentences(text: str) -> tuple[str, str]:
    boundaries = list(re.finditer(r"[。！？!?\n]", text))
    if not boundaries:
        return "", text
    cutoff = boundaries[-1].end()
    return text[:cutoff], text[cutoff:]


def stream_usage_dict(usage: object) -> dict:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        raw = usage
    elif hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    else:
        raw = {
            key: getattr(usage, key, None)
            for key in (
                "prompt_tokens", "completion_tokens", "total_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
            )
        }
    allowed = {
        "prompt_tokens", "completion_tokens", "total_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    }
    return {key: value for key, value in raw.items() if key in allowed and value is not None}


@dataclass(frozen=True)
class StreamPolicy:
    dynamic_tools: bool
    stream_usage: bool
    prompt_profile: str
    thinking_type: str | None


class ModelStreamer:
    """Translate provider chunks into narrative events and normalized tool calls."""

    def __init__(
        self,
        host: Any,
        *,
        log_error: Callable[[str], None],
        log_model_call: Callable[..., None],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.host = host
        self.log_error = log_error
        self.log_model_call = log_model_call
        self.sleep = sleep

    def stream(
        self,
        model: str,
        *,
        policy: StreamPolicy,
        system_overlay: str | None = None,
        system_prompt_override: str | None = None,
        enable_tools: bool = True,
        temperature: float = 0.8,
        buffer_if_tools: bool = False,
        messages_override: list[dict] | None = None,
        retry_on_empty: bool = True,
    ) -> tuple[str, list]:
        host = self.host
        started_at = time.monotonic()
        first_token_at: float | None = None
        if messages_override is None:
            host.messages = normalize_tool_message_history(host.messages)
            messages = host.messages
        else:
            messages = normalize_tool_message_history([dict(m) for m in messages_override])
        if system_prompt_override or system_overlay:
            messages = [dict(message) for message in messages]
            if system_prompt_override:
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = system_prompt_override
                else:
                    messages.insert(0, {"role": "system", "content": system_prompt_override})
            if system_overlay and messages and messages[0].get("role") == "system":
                messages[0]["content"] = f"{messages[0].get('content', '')}\n\n---\n\n{system_overlay}"
            elif system_overlay:
                messages.insert(0, {"role": "system", "content": system_overlay})

        request_role = "combat" if system_overlay else "story"
        request_tools = (
            model_tools_for(request_role)
            if enable_tools and policy.dynamic_tools
            else MODEL_TOOLS if enable_tools else []
        )
        role_chars: dict[str, int] = {}
        role_tokens: dict[str, int] = {}
        for message in messages:
            role = str(message.get("role") or "unknown")
            content = str(message.get("content") or "")
            role_chars[role] = role_chars.get(role, 0) + len(content)
            role_tokens[role] = role_tokens.get(role, 0) + estimate_text_tokens(content)
        tool_schema_json = json.dumps(request_tools, ensure_ascii=False, separators=(",", ":"))
        system_chars = role_chars.get("system", 0)
        tool_schema_chars = len(tool_schema_json)
        context_sections = {
            "system": {"chars": system_chars, "estimated_tokens": role_tokens.get("system", 0)},
            "history": {
                "chars": sum(v for r, v in role_chars.items() if r != "system"),
                "estimated_tokens": sum(v for r, v in role_tokens.items() if r != "system"),
            },
            "tool_schema": {
                "chars": tool_schema_chars,
                "estimated_tokens": estimate_text_tokens(tool_schema_json),
            },
        }
        kwargs: dict[str, Any] = {
            "model": model, "messages": messages, "temperature": temperature,
            "max_tokens": 4096, "stream": True,
        }
        if enable_tools:
            kwargs.update(tools=request_tools, tool_choice="auto")
        if policy.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if policy.thinking_type:
            kwargs["extra_body"] = {"thinking": {"type": policy.thinking_type}}

        try:
            provider_stream = host.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if host.turn_cancellation_requested():
                raise host.turn_cancelled_error("客户端已离开，模型请求已取消") from exc
            self._diagnose(host, model, request_role, "request_error", started_at, None,
                           "request_error", 0, messages, context_sections, {}, policy,
                           error_type=type(exc).__name__)
            if retry_on_empty:
                self.log_error(f"API 建立流失败，正在重试: {exc}")
                self.sleep(0.4)
                return self.stream(model, policy=policy, system_overlay=system_overlay,
                                   system_prompt_override=system_prompt_override,
                                   enable_tools=enable_tools, temperature=temperature,
                                   buffer_if_tools=buffer_if_tools,
                                   messages_override=messages_override, retry_on_empty=False)
            self.log_error(f"API: {exc}")
            host.cb.on_error(f"API 错误: {exc}")
            return "", []

        full_text = ""
        pending_visible = ""
        initial_sentence_released = False
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = None
        usage_data: dict = {}
        host._set_active_stream(provider_stream)
        try:
            host.raise_if_turn_cancelled()
            for chunk in provider_stream:
                host.raise_if_turn_cancelled()
                chunk_usage = stream_usage_dict(getattr(chunk, "usage", None))
                if chunk_usage:
                    usage_data = chunk_usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                if first_token_at is None and (delta.content or delta.tool_calls):
                    first_token_at = time.monotonic()
                if delta.content:
                    full_text += delta.content
                    if not buffer_if_tools:
                        if initial_sentence_released:
                            visible = sanitize_visible_narrative(delta.content)
                            if visible:
                                host.cb.on_narrative(visible)
                        else:
                            pending_visible += delta.content
                            complete, _ = take_complete_sentences(pending_visible)
                            if complete:
                                visible = sanitize_visible_narrative(pending_visible)
                                if visible:
                                    host.cb.on_narrative(visible)
                                pending_visible = ""
                                initial_sentence_released = True
                for tool_call in delta.tool_calls or []:
                    acc = tool_calls_acc.setdefault(tool_call.index, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if tool_call.id:
                        acc["id"] += tool_call.id
                    if tool_call.function:
                        acc["function"]["name"] += tool_call.function.name or ""
                        acc["function"]["arguments"] += tool_call.function.arguments or ""
            host.raise_if_turn_cancelled()
        except host.turn_cancelled_error:
            raise
        except Exception as exc:
            if host.turn_cancellation_requested():
                raise host.turn_cancelled_error("客户端已离开，模型流已取消") from exc
            if retry_on_empty and not full_text and not tool_calls_acc:
                self.log_error(f"API 空流中断，正在重试: {exc}")
                self.sleep(0.4)
                return self.stream(model, policy=policy, system_overlay=system_overlay,
                                   system_prompt_override=system_prompt_override,
                                   enable_tools=enable_tools, temperature=temperature,
                                   buffer_if_tools=buffer_if_tools,
                                   messages_override=messages_override, retry_on_empty=False)
            finish_reason = "transport_error"
            self.log_error(f"API 流式响应中断: {exc}")
            host.cb.on_error("模型连接中断，已保留本轮收到的内容。")
        finally:
            host._clear_active_stream(provider_stream)

        full_text = sanitize_visible_narrative(full_text)
        if not buffer_if_tools and pending_visible:
            visible = sanitize_visible_narrative(pending_visible)
            if visible:
                host.cb.on_narrative(visible)
        if finish_reason == "length" and not tool_calls_acc:
            host.cb.on_error("（叙述过长被截断，请重试或继续）")
        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        if buffer_if_tools:
            if tool_calls:
                full_text = ""
            elif full_text:
                host.cb.on_narrative(full_text)

        elapsed = time.monotonic() - started_at
        first_token = first_token_at - started_at if first_token_at is not None else None
        self.log_model_call(
            model, request_role, elapsed, first_token, finish_reason, len(tool_calls),
            usage=usage_data, system_chars=system_chars, tool_schema_chars=tool_schema_chars,
            prompt_profile=policy.prompt_profile,
            thinking_mode=policy.thinking_type or "provider",
        )
        self._diagnose(host, model, request_role,
                       "completed" if finish_reason != "transport_error" else "transport_error",
                       started_at, first_token, finish_reason, len(tool_calls), messages,
                       context_sections, usage_data, policy)
        if not full_text and not tool_calls and retry_on_empty:
            self.log_error("API 返回空响应，正在重试一次")
            self.sleep(0.4)
            return self.stream(model, policy=policy, system_overlay=system_overlay,
                               system_prompt_override=system_prompt_override,
                               enable_tools=enable_tools, temperature=temperature,
                               buffer_if_tools=buffer_if_tools,
                               messages_override=messages_override, retry_on_empty=False)
        return full_text, tool_calls

    @staticmethod
    def _diagnose(host: Any, model: str, role: str, status: str, started_at: float,
                  first_token: float | None, finish_reason: str | None, tool_count: int,
                  messages: list[dict], context_sections: dict, usage: dict,
                  policy: StreamPolicy, **extra: Any) -> None:
        host._append_model_diagnostic({
            "model": model, "role": role, "status": status,
            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            "first_token_ms": int(first_token * 1000) if first_token is not None else None,
            "finish_reason": finish_reason, "tool_count": tool_count,
            "message_count": len(messages), "context_sections": context_sections,
            "usage": dict(usage), "prompt_profile": policy.prompt_profile,
            "thinking_mode": policy.thinking_type or "provider", **extra,
        })
