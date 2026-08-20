"""Provider streaming boundary for conversational model calls."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import context_shadow as _context_shadow
from .context_overflow import is_context_overflow as _is_context_overflow
from .context_overflow import retry_after_overflow as _retry_after_overflow
from .llm_concurrency import LlmBusyError, acquire_llm_slot
from .model_request import StreamPolicy
from .model_stream_capacity import prepare_with_capacity
from .model_stream_diagnostics import record_model_diagnostic
from .model_stream_helpers import (
    emit_inferred_speaker_segments,
    flush_speaker_segments,
    sanitize_visible_narrative,
    stream_usage_dict,
    take_complete_sentences,
)
from .speaker_parser import SpeakerStreamParser
from .tool_policy import attach_request_snapshot
from .tool_protocol import ToolProtocolFilter
from .tool_request_authority import issue_model_request


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
        _overflow_retried: bool = False,
        _capacity_compaction_attempted: bool = False,
    ) -> tuple[str, list]:
        host = self.host
        started_at = time.monotonic()
        first_token_at: float | None = None
        prepared, _capacity_compaction_attempted = prepare_with_capacity(
            self,
            model,
            policy=policy,
            system_overlay=system_overlay,
            system_prompt_override=system_prompt_override,
            enable_tools=enable_tools,
            temperature=temperature,
            messages_override=messages_override,
            compaction_attempted=_capacity_compaction_attempted,
        )
        if prepared is None:
            return "", []
        _context_shadow.record_prepared_request(host, prepared)
        # Issue authority only after capacity preflight has selected the final
        # wire request.  Provisional estimates never consume a request step or
        # become replayable capabilities.
        issue_model_request(host, prepared.request_snapshot, prepared.request_tools)
        messages = prepared.messages
        request_role = prepared.request_role
        request_snapshot = prepared.request_snapshot
        context_sections = prepared.context_sections
        request_envelope = prepared.request_envelope
        kwargs = prepared.provider_kwargs
        system_chars = prepared.system_chars
        tool_schema_chars = prepared.tool_schema_chars

        llm_slot = None
        try:
            llm_slot = acquire_llm_slot(
                model=model,
                world_id=str(getattr(getattr(host, "context", None), "world_id", "") or ""),
            )
            provider_stream = host.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if llm_slot is not None:
                llm_slot.release(status="failed")
            if host.turn_cancellation_requested():
                raise host.turn_cancelled_error("客户端已离开，模型请求已取消") from exc
            record_model_diagnostic(
                host,
                model,
                request_role,
                "request_error",
                started_at,
                None,
                "request_error",
                0,
                messages,
                context_sections,
                {},
                policy,
                error_type=type(exc).__name__,
                request_snapshot=request_snapshot.to_dict(),
                request_envelope=request_envelope,
            )
            if isinstance(exc, LlmBusyError):
                self.log_error(f"模型并发已满: {exc}")
                host.cb.on_error("服务器繁忙：模型调用排队超时，请稍后重试。")
                return "", []
            overflow = _is_context_overflow(exc)
            if messages_override is None and overflow:
                if not _overflow_retried and not _capacity_compaction_attempted:
                    retried = _retry_after_overflow(
                        self,
                        model,
                        policy=policy,
                        system_overlay=system_overlay,
                        system_prompt_override=system_prompt_override,
                        enable_tools=enable_tools,
                        temperature=temperature,
                        buffer_if_tools=buffer_if_tools,
                        retry_on_empty=retry_on_empty,
                    )
                    if retried is not None:
                        return retried
                # Context overflow is not a transient empty response.  It has
                # either consumed its one safe compaction attempt or proved
                # irreducible, so never fall through to the generic retry.
                self.log_error("模型上下文超出容量，已停止本轮请求")
                host.cb.on_error("当前规则与历史过长，无法安全继续本轮；请稍后重试。")
                return "", []
            if retry_on_empty:
                self.log_error(f"API 建立流失败，正在重试: {type(exc).__name__}")
                self.sleep(0.4)
                return self.stream(
                    model,
                    policy=policy,
                    system_overlay=system_overlay,
                    system_prompt_override=system_prompt_override,
                    enable_tools=enable_tools,
                    temperature=temperature,
                    buffer_if_tools=buffer_if_tools,
                    messages_override=messages_override,
                    retry_on_empty=False,
                    _overflow_retried=_overflow_retried,
                    _capacity_compaction_attempted=_capacity_compaction_attempted,
                )
            self.log_error(f"API 请求失败: {type(exc).__name__}")
            host.cb.on_error("模型服务暂时不可用，请稍后重试。")
            return "", []

        full_text = ""
        pending_visible = ""
        initial_sentence_released = False
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = None
        usage_data: dict = {}
        protocol_filter = ToolProtocolFilter()
        # A provider stream can fail only once iteration has started.  Never
        # recurse from the ``except`` below: the global LLM slot is still held
        # until ``finally`` runs, and Pi deliberately sets that capacity to 1.
        deferred_retry: str | None = None
        terminal_overflow = False

        # ⟦npc:id⟧ 发言标签增量解析：标签剥离后文本照常流出，
        # speech_start 触发 on_speaker_segment，发言文本带 npc_id 上下文。
        speaker_parser = SpeakerStreamParser(
            is_valid_npc=getattr(host, "is_valid_npc_id", None) or (lambda _npc_id: False),
            on_unknown_npc=getattr(host, "log_unknown_npc_speaker", None),
        )

        def emit_visible(raw: str) -> None:
            if emit_inferred_speaker_segments(host, raw):
                return
            for kind, text, npc_id in speaker_parser.feed(raw):
                if kind == "text":
                    visible = sanitize_visible_narrative(text)
                    if visible:
                        # 旁白保持单参数调用（兼容既有回调签名），
                        # 发言文本才附带 npc_id 上下文。
                        if npc_id:
                            host.cb.on_narrative(visible, npc_id)
                        else:
                            host.cb.on_narrative(visible)
                elif kind == "speech_start":
                    # speech_start Piece = (kind, npc_id, None)：人物 id 在 text 槽。
                    host.cb.on_speaker_segment(text)

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
                    public_content = protocol_filter.feed(delta.content)
                    full_text += public_content
                    if not buffer_if_tools:
                        if initial_sentence_released:
                            emit_visible(public_content)
                        else:
                            pending_visible += public_content
                            complete, _ = take_complete_sentences(pending_visible)
                            if complete:
                                emit_visible(pending_visible)
                                pending_visible = ""
                                initial_sentence_released = True
                for tool_call in delta.tool_calls or []:
                    acc = tool_calls_acc.setdefault(
                        tool_call.index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
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
            overflow = _is_context_overflow(exc)
            if messages_override is None and not full_text and not tool_calls_acc and overflow:
                if not _overflow_retried and not _capacity_compaction_attempted:
                    deferred_retry = "overflow"
                else:
                    terminal_overflow = True
            if retry_on_empty and not full_text and not tool_calls_acc:
                # An overflow has a separate retry budget and must never
                # degrade into the generic empty-stream retry.
                if not overflow:
                    deferred_retry = "empty"
            if deferred_retry is None and not terminal_overflow:
                finish_reason = "transport_error"
                self.log_error(f"API 流式响应中断: {type(exc).__name__}")
                host.cb.on_error("模型连接中断，已保留本轮收到的内容。")
        finally:
            host._clear_active_stream(provider_stream)
            if llm_slot is not None:
                # Hold the global slot across the whole stream, not just connect.
                llm_slot.release()

        if deferred_retry == "overflow":
            retried = _retry_after_overflow(
                self,
                model,
                policy=policy,
                system_overlay=system_overlay,
                system_prompt_override=system_prompt_override,
                enable_tools=enable_tools,
                temperature=temperature,
                buffer_if_tools=buffer_if_tools,
                retry_on_empty=retry_on_empty,
            )
            if retried is not None:
                return retried
            self.log_error("模型上下文超出容量，已停止本轮请求")
            host.cb.on_error("当前规则与历史过长，无法安全继续本轮；请稍后重试。")
            return "", []
        if terminal_overflow:
            self.log_error("模型上下文超出容量，已停止本轮请求")
            host.cb.on_error("当前规则与历史过长，无法安全继续本轮；请稍后重试。")
            return "", []
        if deferred_retry == "empty":
            self.log_error("API 空流中断，正在重试")
            self.sleep(0.4)
            return self.stream(
                model,
                policy=policy,
                system_overlay=system_overlay,
                system_prompt_override=system_prompt_override,
                enable_tools=enable_tools,
                temperature=temperature,
                buffer_if_tools=buffer_if_tools,
                messages_override=messages_override,
                retry_on_empty=False,
                _overflow_retried=_overflow_retried,
                _capacity_compaction_attempted=_capacity_compaction_attempted,
            )

        trailing_public = protocol_filter.flush()
        full_text += trailing_public
        if not buffer_if_tools:
            if initial_sentence_released:
                emit_visible(trailing_public)
            else:
                pending_visible += trailing_public
        text_tool_calls = protocol_filter.tool_calls()
        if text_tool_calls and tool_calls_acc:
            # A provider must not execute the same call twice if it emitted both forms.
            self.log_error("模型同时返回结构化和文本工具协议；已忽略文本协议")
            text_tool_calls = []
        elif protocol_filter.blocks:
            self.log_error(f"已隔离模型文本工具协议（{len(protocol_filter.blocks)} 个区块）")
        if protocol_filter.malformed:
            self.log_error("已丢弃未闭合或过长的模型文本工具协议")

        full_text = sanitize_visible_narrative(full_text)
        if not buffer_if_tools:
            if pending_visible:
                emit_visible(pending_visible)
            flush_speaker_segments(host, speaker_parser)
        if finish_reason == "length" and not tool_calls_acc and not text_tool_calls:
            host.cb.on_error("（叙述过长被截断，请重试或继续）")
        raw_tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)] + text_tool_calls
        tool_calls: list[dict] = []
        for index, raw_call in enumerate(raw_tool_calls):
            call = dict(raw_call)
            call_id = str(call.get("id") or "")
            if not call_id or call_id.startswith("dsml_"):
                call["id"] = f"{request_snapshot.request_id}:{request_snapshot.step}:{index}"
            tool_calls.append(attach_request_snapshot(call, request_snapshot))
        if buffer_if_tools:
            if tool_calls:
                full_text = ""
            elif full_text:
                emit_visible(full_text)
                flush_speaker_segments(host, speaker_parser)

        elapsed = time.monotonic() - started_at
        first_token = first_token_at - started_at if first_token_at is not None else None
        # The typed envelope is frozen before the request.  Provider usage is
        # completion metadata, so merge only normalized cache counters into a
        # copy for durable ``ModelCall.details`` rather than mutating authority
        # evidence or persisting provider-specific raw chunks.
        if usage_data:
            request_envelope = dict(request_envelope)
            cache = dict(request_envelope.get("cache") or {})
            cache.update(
                {
                    key: usage_data[key]
                    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
                    if key in usage_data
                }
            )
            request_envelope["cache"] = cache
        record_performance = getattr(host, "record_model_performance", None)
        if record_performance:
            record_performance(
                elapsed_ms=elapsed * 1000,
                first_token_ms=first_token * 1000 if first_token is not None else None,
                tool_count=len(tool_calls),
            )
        self.log_model_call(
            model,
            request_role,
            elapsed,
            first_token,
            finish_reason,
            len(tool_calls),
            usage=usage_data,
            system_chars=system_chars,
            tool_schema_chars=tool_schema_chars,
            prompt_profile=policy.prompt_profile,
            thinking_mode=policy.thinking_type or "provider",
        )
        record_model_diagnostic(
            host,
            model,
            request_role,
            "completed" if finish_reason != "transport_error" else "transport_error",
            started_at,
            first_token,
            finish_reason,
            len(tool_calls),
            messages,
            context_sections,
            usage_data,
            policy,
            request_snapshot=request_snapshot.to_dict(),
            request_envelope=request_envelope,
        )
        if not full_text and not tool_calls and retry_on_empty:
            self.log_error("API 返回空响应，正在重试一次")
            self.sleep(0.4)
            return self.stream(
                model,
                policy=policy,
                system_overlay=system_overlay,
                system_prompt_override=system_prompt_override,
                enable_tools=enable_tools,
                temperature=temperature,
                buffer_if_tools=buffer_if_tools,
                messages_override=messages_override,
                retry_on_empty=False,
                _overflow_retried=_overflow_retried,
                _capacity_compaction_attempted=_capacity_compaction_attempted,
            )
        return full_text, tool_calls
