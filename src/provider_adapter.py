"""DeepSeek provider adapter（H4 §5.8）：provider 特有行为的唯一收口。

- 稳定错误分级：AUTH/QUOTA/RATE_LIMIT/CONTEXT_WINDOW/SERVER/TRANSPORT/
  TIMEOUT/ABORT，替代散落在各调用点的字符串猜测；
- 流事件归一化：reasoning 提取与公开文本/工具增量/usage 严格分离，
  reasoning 永不作为兜底文本进入叙事；
- reasoning passback：DeepSeek thinking 模式要求带工具调用的 assistant
  历史回传 reasoning_content。adapter 以 call id 为键做最小生命周期保管，
  只写进 provider wire 副本，永不落 host.messages、公开历史、审计或遥测；
- 录制：``TRPG_RECORD_MODEL_STREAMS=<dir>`` 时把归一化 chunk 序列写成
  JSON fixture（默认关；Pi/生产不设），供离线 replay 与故障注入测试。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ERROR_AUTH = "auth"
ERROR_QUOTA = "quota"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_CONTEXT_WINDOW = "context_window"
ERROR_SERVER = "server"
ERROR_TRANSPORT = "transport"
ERROR_TIMEOUT = "timeout"
ERROR_ABORT = "abort"
ERROR_BUSY = "busy"  # 本地并发排队，非 provider 错误
ERROR_UNKNOWN = "unknown"

_PASSBACK_KEY = "_reasoning_passback"
_PASSBACK_MAX = 32


def classify_provider_error(exc: BaseException) -> str:
    """把 openai-compatible 异常归入稳定的错误类（metadata-only 诊断用）。"""
    from .context_overflow import is_context_overflow
    from .llm_concurrency import LlmBusyError

    if isinstance(exc, LlmBusyError):
        return ERROR_BUSY
    if is_context_overflow(exc):
        return ERROR_CONTEXT_WINDOW
    name = type(exc).__name__
    if "Timeout" in name:
        return ERROR_TIMEOUT
    status_raw = getattr(exc, "status_code", None)
    try:
        status = int(status_raw) if status_raw is not None else None
    except (TypeError, ValueError):
        status = None
    if status in (401, 403):
        return ERROR_AUTH
    if status == 402:
        return ERROR_QUOTA
    if status == 429:
        return ERROR_RATE_LIMIT
    if status is not None and status >= 500:
        return ERROR_SERVER
    if "Connection" in name or "Connect" in name:
        return ERROR_TRANSPORT
    message = str(exc).lower()
    if "insufficient_quota" in message or "quota exceeded" in message:
        return ERROR_QUOTA
    if "rate limit" in message or "rate_limit" in message:
        return ERROR_RATE_LIMIT
    return ERROR_UNKNOWN


def reasoning_passback_required() -> bool:
    """只有 DeepSeek API 明确要求时才回传 reasoning（§5.8）。"""
    from . import config

    return "deepseek.com" in str(getattr(config, "BASE_URL", "")).lower()


def extract_reasoning_text(chunk: Any) -> str:
    """流 chunk 里的 reasoning_content；任何异常形态都归为空，绝不兜底成文本。"""
    try:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return ""
        value = getattr(delta, "reasoning_content", None)
        return value if isinstance(value, str) else ""
    except Exception:  # 归一化边界：未知 chunk 结构不得向外抛
        return ""


def register_reasoning_passback(host: Any, call_ids: list[str], reasoning: str) -> None:
    """以最终 wire call id 为键保管当步 reasoning（有界，先进先出）。"""
    if not reasoning or not call_ids:
        return
    store = getattr(host, "__dict__", {}).get(_PASSBACK_KEY)
    if not isinstance(store, dict):
        store = {}
        host.__dict__[_PASSBACK_KEY] = store
    for call_id in call_ids:
        if call_id:
            store[str(call_id)] = reasoning
    while len(store) > _PASSBACK_MAX:
        store.pop(next(iter(store)))


def apply_reasoning_passback(host: Any, messages: list[dict]) -> list[dict]:
    """返回 provider wire 副本：带 tool_calls 的 assistant 消息补 reasoning_content。

    不修改 host.messages 本体；没有登记记录时原样返回（零拷贝）。
    """
    store = getattr(host, "__dict__", {}).get(_PASSBACK_KEY)
    if not store:
        return messages
    wire: list[dict] = []
    for message in messages:
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        reasoning = None
        if message.get("role") == "assistant" and isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                hit = store.get(str(call.get("id") or ""))
                if hit:
                    reasoning = hit
                    break
        wire.append({**message, "reasoning_content": reasoning} if reasoning else message)
    return wire


def note_model_retry(host: Any, reason: str, error_class: str, backoff_ms: int) -> None:
    """H4 §5.8：每次重试记录原因/错误类/退避（metadata-only，按回合留存）。"""
    retries = getattr(host, "__dict__", {}).get("_turn_model_retries")
    if not isinstance(retries, list):
        retries = []
        host.__dict__["_turn_model_retries"] = retries
    retries.append({"reason": reason, "error_class": error_class, "backoff_ms": backoff_ms})
    from .turn_performance import increment_counter

    increment_counter(host, "model_retry_count")


def finalize_stream_tool_calls(
    host: Any,
    *,
    tool_calls_acc: dict[int, dict],
    text_tool_calls: list[dict],
    finish_reason: str | None,
    request_snapshot: Any,
    reasoning_text: str,
    log_error: Any,
    on_error: Any,
) -> list[dict]:
    """收口一次流的工具调用：截断 fail-closed、id 重写、快照绑定、reasoning 登记。

    finish_reason="length" 时工具参数增量可能不完整——一律丢弃而不是把
    半截 JSON 送进执行层（H4：被截断的工具调用必须失败关闭）。
    """
    from .tool_policy import attach_request_snapshot

    if finish_reason == "length" and (tool_calls_acc or text_tool_calls):
        log_error("模型输出在工具调用途中被截断，已丢弃本轮工具调用")
        on_error("模型输出被截断，本轮工具调用未执行；请重试。")
        tool_calls_acc = {}
        text_tool_calls = []
    elif finish_reason == "length":
        on_error("（叙述过长被截断，请重试或继续）")
    raw_tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)] + text_tool_calls
    tool_calls: list[dict] = []
    for index, raw_call in enumerate(raw_tool_calls):
        call = dict(raw_call)
        call_id = str(call.get("id") or "")
        if not call_id or call_id.startswith("dsml_"):
            call["id"] = f"{request_snapshot.request_id}:{request_snapshot.step}:{index}"
        tool_calls.append(attach_request_snapshot(call, request_snapshot))
    if reasoning_text and tool_calls:
        # DeepSeek thinking passback：以最终 wire call id 为键最小生命周期保管。
        register_reasoning_passback(
            host, [str(call.get("id") or "") for call in tool_calls], reasoning_text
        )
    return tool_calls


# ---- 录制（离线 replay 的事实来源） -----------------------------------------

_RECORD_ENV = "TRPG_RECORD_MODEL_STREAMS"


def normalize_chunk_record(chunk: Any) -> dict:
    """把一个 provider chunk 归一化为 JSON-safe 录制记录。

    只保留重放当前全部行为所需的字段：content / reasoning / tool 增量 /
    finish_reason / usage。这正是"内部事件流"的持久形态；reasoning 只存在
    于显式开启录制的本地 fixture 目录，不进入任何运行期日志或历史。
    """
    record: dict[str, Any] = {}
    reasoning = extract_reasoning_text(chunk)
    if reasoning:
        record["reasoning"] = reasoning
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        from .model_stream_helpers import stream_usage_dict

        usage_dict = stream_usage_dict(usage)
        if usage_dict:
            record["usage"] = usage_dict
    choices = getattr(chunk, "choices", None) or []
    if choices:
        choice = choices[0]
        finish = getattr(choice, "finish_reason", None)
        if finish:
            record["finish_reason"] = finish
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None)
            if content:
                record["content"] = content
            deltas = []
            for tool_call in getattr(delta, "tool_calls", None) or []:
                function = getattr(tool_call, "function", None)
                deltas.append(
                    {
                        "index": getattr(tool_call, "index", 0),
                        "id": getattr(tool_call, "id", None),
                        "name": getattr(function, "name", None),
                        "arguments": getattr(function, "arguments", None),
                    }
                )
            if deltas:
                record["tool_deltas"] = deltas
    return record


def maybe_record_stream(host: Any, stream: Any, request_id: str) -> Any:
    """TRPG_RECORD_MODEL_STREAMS 开启时用录制生成器包住 provider 流。

    生成器在流耗尽、中断或关闭时把归一化记录落盘；未开启时零开销原样返回。
    """
    root = os.environ.get(_RECORD_ENV, "").strip()
    if not root:
        return stream
    world_id = str(getattr(getattr(host, "context", None), "world_id", "") or "world")
    safe_world = "".join(c if c.isalnum() or c in "-_" else "_" for c in world_id)[:80]
    path = Path(root) / f"{safe_world}-{request_id}.jsonl"
    records: list[dict] = []

    def _gen():
        try:
            for chunk in stream:
                records.append(normalize_chunk_record(chunk))
                yield chunk
        finally:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as handle:
                    for record in records:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                # 录制是诊断设施，绝不能影响游戏主路径。
                pass

    return _gen()
