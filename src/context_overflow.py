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


def _restore_deterministic_skill_surface(streamer: Any) -> int:
    """Restore controls removed by overflow compaction before its one retry."""

    try:
        from .skill_activation import refresh_deterministic_skills

        return refresh_deterministic_skills(streamer.host)
    except Exception as exc:  # noqa: BLE001 - overflow recovery must not crash a turn
        streamer.log_error(f"上下文溢出压缩后恢复确定性 Skill 失败: {type(exc).__name__}")
        return 0


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
            compactor = ensure_compactor()
            estimate = getattr(compactor, "estimate_tokens", None)
            before_tokens = int(estimate()) if callable(estimate) else None
            # Deterministic pruning is cheaper and preserves a bounded
            # head/tail.  A summary is only attempted after it, and neither
            # operation may rebase/destructively replace the live surface.
            prune = getattr(compactor, "prune_old_tool_results", None)
            pruned = int(prune()) if callable(prune) else 0
            summarized = bool(
                compactor.summarize(silent=True, allow_rebase_fallback=False)
            )
            after_tokens = int(estimate()) if callable(estimate) else None
            # A successful API return is not enough: a malformed/oversized
            # summary must not trigger a blind retry if it did not actually
            # reduce the active surface.
            compacted = bool(pruned or summarized) and (
                before_tokens is None
                or after_tokens is None
                or after_tokens < before_tokens
            )
        except Exception:  # noqa: BLE001 - overflow handling must not break the turn
            compacted = False
    if not compacted:
        streamer.log_error("模型上下文溢出且不可约：已保留全部规则与历史")
        return None
    # ``HistoryCompactor`` may have replaced an old engine-control message.
    # Restore deterministic Skills before recursively rebuilding the retry
    # request so one turn never sends a rule-less prompt merely because its
    # first provider call overflowed.
    _restore_deterministic_skill_surface(streamer)
    streamer.log_error("模型上下文溢出，已压缩上下文并重试一次")
    # ``stream`` always re-evaluates capacity before it opens another provider
    # connection.  Mark this compaction as consumed so a second overflow (or
    # a generic empty-response retry) cannot repeatedly summarize the same
    # turn.
    return streamer.stream(
        model,
        _overflow_retried=True,
        _capacity_compaction_attempted=True,
        **kwargs,
    )
