"""Provider context-overflow detection and non-destructive retry."""

from __future__ import annotations

from typing import Any

# Provider context-overflow markers (openai-compatible gateways phrase these
# differently; all known variants carry one of these substrings).
_CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "context window",
    "too many tokens",
)


def is_context_overflow(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _CONTEXT_OVERFLOW_MARKERS)


def retry_after_overflow(streamer: Any, model: str, **kwargs: Any) -> tuple[str, list] | None:
    """Force one non-destructive compaction and retry a context overflow.

    Returns the retried stream result, or ``None`` when the context is
    irreducible (the caller then follows the ordinary error path — rules
    and history are never deleted to make a request fit).
    """
    ensure_compactor = getattr(streamer.host, "_ensure_history_compactor", None)
    compacted = False
    if ensure_compactor is not None:
        try:
            compacted = bool(
                ensure_compactor().summarize(silent=True, allow_rebase_fallback=False)
            )
        except Exception:  # noqa: BLE001 - overflow handling must not break the turn
            compacted = False
    if not compacted:
        streamer.log_error("模型上下文溢出且不可约：已保留全部规则与历史")
        return None
    streamer.log_error("模型上下文溢出，已压缩上下文并重试一次")
    return streamer.stream(model, _overflow_retried=True, **kwargs)
