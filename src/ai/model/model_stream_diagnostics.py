"""Metadata-only diagnostics shared by model request paths."""

from __future__ import annotations

import time
from typing import Any

from src.ai.model.model_request import StreamPolicy


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
    host._append_model_diagnostic(
        {
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
            **extra,
        }
    )
