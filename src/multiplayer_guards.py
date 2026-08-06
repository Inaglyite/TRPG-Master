"""Per-account spend guardrails for multiplayer turns (in-memory, per process).

Counters live in process memory and reset on restart by design. Three guards:
- at most one in-flight generation turn per account across all worlds;
- a sliding per-minute action rate window;
- a per-UTC-day generation quota.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from .multiplayer import MultiplayerError

logger = logging.getLogger("trpg.multiplayer_guards")

GUARDED_TURN_TYPES = frozenset({"action", "start", "continue"})


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, min(100000, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


class UserTurnGuard:
    """At most one in-flight generation turn per account, across all worlds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: dict[str, tuple[str, str]] = {}

    def acquire(self, user_id: str, world_id: str, action_id: str) -> None:
        with self._lock:
            if user_id in self._in_flight:
                logger.info(
                    "拒绝并发回合 user_id=%s world_id=%s action_id=%s",
                    user_id,
                    world_id,
                    action_id,
                )
                raise MultiplayerError(
                    "action_in_progress",
                    "该账号有正在生成的回合，请等待完成后再提交新行动",
                    429,
                )
            self._in_flight[user_id] = (world_id, action_id)

    def release(self, user_id: str, world_id: str, action_id: str) -> None:
        with self._lock:
            if self._in_flight.get(user_id) == (world_id, action_id):
                self._in_flight.pop(user_id, None)

    def release_action(self, world_id: str, action_id: str) -> None:
        """Drop the in-flight marker once a room action reaches a terminal state."""
        with self._lock:
            for user_id, marker in list(self._in_flight.items()):
                if marker == (world_id, action_id):
                    self._in_flight.pop(user_id, None)

    def reset(self) -> None:
        with self._lock:
            self._in_flight.clear()


class ActionRateLimiter:
    """Sliding-window per-account action rate limit (process memory)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def check(self, user_id: str, *, world_id: str = "") -> None:
        limit = _env_int("TRPG_ACTION_RATE_PER_MINUTE", 10)
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[user_id]
            while attempts and attempts[0] < now - 60.0:
                attempts.popleft()
            if len(attempts) >= limit:
                logger.info(
                    "拒绝超频行动 user_id=%s world_id=%s limit=%d/min",
                    user_id,
                    world_id,
                    limit,
                )
                raise MultiplayerError(
                    "rate_limited",
                    f"操作过于频繁：每个账号每分钟最多 {limit} 次行动，请稍后再试",
                    429,
                )
            attempts.append(now)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


class DailyTurnQuota:
    """Per-account daily generation quota keyed by UTC day (process memory)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[str, int]] = {}

    def check(self, user_id: str, *, world_id: str = "") -> None:
        limit = _env_int("TRPG_DAILY_TURN_QUOTA", 200)
        today = datetime.now(UTC).date().isoformat()
        with self._lock:
            day, count = self._counts.get(user_id, (today, 0))
            if day != today:
                day, count = today, 0
            if count >= limit:
                logger.info(
                    "拒绝超配额行动 user_id=%s world_id=%s 日额度=%d",
                    user_id,
                    world_id,
                    limit,
                )
                raise MultiplayerError(
                    "daily_quota_exceeded",
                    f"今日回合额度已用完（每账号每日 {limit} 次生成），请明天再试",
                    429,
                )
            self._counts[user_id] = (day, count + 1)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


USER_TURN_GUARD = UserTurnGuard()
ACTION_RATE_LIMITER = ActionRateLimiter()
DAILY_TURN_QUOTA = DailyTurnQuota()


def check_action_guards(user_id: str, world_id: str, action_id: str) -> None:
    """Rate + quota checks, then take the account's single in-flight marker."""
    ACTION_RATE_LIMITER.check(user_id, world_id=world_id)
    DAILY_TURN_QUOTA.check(user_id, world_id=world_id)
    USER_TURN_GUARD.acquire(user_id, world_id, action_id)


def reset_action_guards() -> None:
    """Clear process-local counters (primarily for isolated test cases)."""
    USER_TURN_GUARD.reset()
    ACTION_RATE_LIMITER.reset()
    DAILY_TURN_QUOTA.reset()
