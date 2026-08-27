"""Process-wide concurrency cap for synchronous model calls (spend guardrail)."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger("trpg.llm")

_ACQUIRE_TIMEOUT_SECONDS = 60.0

_lock = threading.Lock()
_semaphore: threading.BoundedSemaphore | None = None
_semaphore_value: int | None = None


class LlmBusyError(RuntimeError):
    """Raised when a model call waits too long for a concurrency slot."""


def max_concurrency() -> int:
    try:
        return max(1, min(64, int(os.environ.get("TRPG_LLM_MAX_CONCURRENCY", "2"))))
    except (TypeError, ValueError):
        return 2


def _get_semaphore() -> threading.BoundedSemaphore:
    global _semaphore, _semaphore_value
    with _lock:
        value = max_concurrency()
        # Rebuild when the env override changes so tests can monkeypatch it.
        if _semaphore is None or _semaphore_value != value:
            _semaphore = threading.BoundedSemaphore(value)
            _semaphore_value = value
        return _semaphore


class LlmCallSlot:
    """RAII handle for one in-flight model call."""

    def __init__(
        self,
        semaphore: threading.BoundedSemaphore,
        *,
        model: str,
        world_id: str,
        user_id: str,
    ) -> None:
        self._semaphore = semaphore
        self._model = model
        self._world_id = world_id
        self._user_id = user_id
        self._started_at = time.monotonic()
        self._released = False

    def release(self, *, status: str = "completed") -> None:
        if self._released:
            return
        self._released = True
        elapsed = time.monotonic() - self._started_at
        self._semaphore.release()
        logger.info(
            "LLM 调用结束 model=%s world_id=%s user_id=%s status=%s 耗时=%.2fs",
            self._model,
            self._world_id,
            self._user_id,
            status,
            elapsed,
        )

    def __enter__(self) -> LlmCallSlot:
        return self

    def __exit__(self, exc_type, _exc, _tb) -> None:
        self.release(status="failed" if exc_type is not None else "completed")


def acquire_llm_slot(*, model: str = "", world_id: str = "", user_id: str = "") -> LlmCallSlot:
    """Take one global model-call slot, waiting at most 60 seconds."""
    semaphore = _get_semaphore()
    started_at = time.monotonic()
    if not semaphore.acquire(timeout=_ACQUIRE_TIMEOUT_SECONDS):
        logger.warning(
            "LLM 并发获取超时 model=%s world_id=%s user_id=%s 等待>=%.0fs",
            model,
            world_id,
            user_id,
            _ACQUIRE_TIMEOUT_SECONDS,
        )
        raise LlmBusyError("服务器繁忙：模型调用排队超时，请稍后重试")
    waited = time.monotonic() - started_at
    if waited >= 1.0:
        logger.info(
            "LLM 并发等待 model=%s world_id=%s user_id=%s 等待=%.2fs",
            model,
            world_id,
            user_id,
            waited,
        )
    return LlmCallSlot(semaphore, model=model, world_id=world_id, user_id=user_id)


@contextmanager
def llm_call_slot(*, model: str = "", world_id: str = "", user_id: str = ""):
    """Context manager wrapping one blocking model call in the global cap."""
    slot = acquire_llm_slot(model=model, world_id=world_id, user_id=user_id)
    try:
        yield slot
    except BaseException:
        slot.release(status="failed")
        raise
    else:
        slot.release()
