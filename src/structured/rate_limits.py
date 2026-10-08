"""Effect-free dice admission, shared by account across worlds in one process.

This is not a generation quota or a game clock. Current deployments use one
worker; a future multi-worker deployment must replace it with shared storage.
Restoring/forking a world does not reset admission; process restart does.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable

from .errors import StructuredError


def _configured_limit() -> int:
    try:
        return max(1, min(1000, int(os.environ.get("TRPG_ORDINARY_ROLLS_PER_MINUTE", "30"))))
    except ValueError:
        return 30


class OrdinaryDiceRateLimiter:
    """Sliding 60s window; bounded keys, never evict a still-active account."""

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_accounts: int = 4096
    ) -> None:
        self._clock = clock
        self._max_accounts = max(1, max_accounts)
        self._lock = threading.Lock()
        self._attempts: dict[tuple[str, str], deque[float]] = {}
        self._next_cleanup = 0.0

    def check(self, database_url: str, user_id: str) -> None:
        if not user_id:
            raise StructuredError("not_authorized", "普通骰缺少已认证的操作者。")
        # Do not retain/log database credentials. Separate independent stores,
        # but not world, character, role, socket or service-instance identities.
        key = (hashlib.sha256(database_url.encode()).hexdigest(), user_id)
        limit = _configured_limit()
        with self._lock:
            now = self._clock()
            cutoff = now - 60.0
            if now >= self._next_cleanup or (
                key not in self._attempts and len(self._attempts) >= self._max_accounts
            ):
                self._attempts = {
                    account: times
                    for account, times in self._attempts.items()
                    if times and times[-1] > cutoff
                }
                self._next_cleanup = now + 60.0
            if key not in self._attempts and len(self._attempts) >= self._max_accounts:
                raise StructuredError(
                    "rate_limited", "普通骰服务暂时繁忙，请稍后按原请求 ID 重试。", retryable=True
                )
            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                seconds = max(1, math.ceil(attempts[0] + 60.0 - now))
                raise StructuredError(
                    "rate_limited",
                    f"普通骰过于频繁：每账号每分钟最多 {limit} 次，请约 {seconds} 秒后按原请求 ID 重试。",
                    retryable=True,
                )
            # Charge the physical RNG attempt, including a later transaction
            # failure. Same-ID committed replays never reach this method.
            attempts.append(now)


ORDINARY_DICE_RATE_LIMITER = OrdinaryDiceRateLimiter()
