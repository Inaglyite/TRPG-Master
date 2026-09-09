"""Metadata-only diagnostics shared by model request paths."""

from __future__ import annotations

import time
from typing import Any

from src.ai.model.model_request import StreamPolicy
from src.ai.model.route_service import route_public_info


def _estimate_input_tokens(context_sections: dict) -> int | None:
    """分区估算之和（互斥分区）；没有分区数据时返回 None。"""
    if not isinstance(context_sections, dict) or not context_sections:
        return None
    total = 0
    for section in context_sections.values():
        if not isinstance(section, dict):
            return None
        value = section.get("estimated_tokens")
        if not isinstance(value, (int, float)):
            return None
        total += int(value)
    return total or None


def token_contract_fields(entry: dict) -> dict:
    """数字口径合同：usage 真实值优先，其次分区估算，都没有就是 unknown。

    只新增/覆盖顶层数值字段；不触碰任何正文。窗口未知时不产百分比。
    """
    usage = entry.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    estimate = _estimate_input_tokens(entry.get("context_sections") or {})
    if isinstance(prompt, (int, float)) and prompt > 0:
        input_tokens, input_source = int(prompt), "provider"
    elif estimate:
        input_tokens, input_source = estimate, "estimate"
    else:
        input_tokens, input_source = None, "unknown"
    window = entry.get("window_tokens")
    fields = {
        "input_tokens": input_tokens,
        "input_source": input_source,
        "output_tokens": usage.get("completion_tokens"),
        "utilization": (
            round(input_tokens / window, 4)
            if input_tokens and isinstance(window, (int, float)) and window > 0
            else None
        ),
    }
    envelope = entry.get("request_envelope")
    if isinstance(envelope, dict):
        capacity = envelope.get("capacity_metadata")
        if isinstance(capacity, dict) and capacity.get("state"):
            fields["capacity_state"] = capacity["state"]
    return fields


def turn_context_summary(host: Any) -> dict | None:
    """主界面摘要：最近一次叙述调用的占用数字（无叙述则标上一回合口径）。

    仅数字与枚举；绝不包含正文、密钥或 Lorebook 条目。
    """
    diagnostics = getattr(host, "_turn_diagnostics", None) or []
    calls = [d for d in diagnostics if isinstance(d, dict) and d.get("role")]
    if not calls:
        return None
    narrative = next(
        (d for d in reversed(calls) if d.get("role") in ("story", "rewrite")),
        None,
    )
    picked = narrative or calls[-1]
    world_id = str(getattr(getattr(host, "context", None), "world_id", "") or "")
    return {
        "world_id": world_id,
        "role": picked.get("role"),
        "model_id": picked.get("model"),
        "input_tokens": picked.get("input_tokens"),
        "input_source": picked.get("input_source", "unknown"),
        "window_tokens": picked.get("window_tokens"),
        "window_source": picked.get("window_source", "unknown"),
        "reserved_output_tokens": picked.get("reserved_output_tokens"),
        "utilization": picked.get("utilization"),
        "capacity_state": picked.get("capacity_state"),
        "config_revision": picked.get("config_revision"),
        # 本回合没有叙述调用时，摘要来自其他角色调用，前端必须标明。
        "from_narrative": narrative is not None,
    }


def record_model_diagnostic(
    host: Any,
    model: str,
    role: str,
    status: str,
    started_at: float,
    first_token: float | None,
    finish_reason: str | None,
    tool_count: int,
    messages: list[dict],
    context_sections: dict,
    usage: dict,
    policy: StreamPolicy,
    **extra: Any,
) -> None:
    """Append a redacted request/stream outcome without prompt bodies."""
    entry = {
        "model": model,
        "role": role,
        "status": status,
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        "first_token_ms": int(first_token * 1000) if first_token is not None else None,
        "finish_reason": finish_reason,
        "tool_count": tool_count,
        "message_count": len(messages),
        "context_sections": context_sections,
        "usage": dict(usage),
        "prompt_profile": policy.prompt_profile,
        "thinking_mode": policy.thinking_type or "provider",
        **route_public_info(host, role),
        **extra,
    }
    entry.update(token_contract_fields(entry))
    host._append_model_diagnostic(entry)
