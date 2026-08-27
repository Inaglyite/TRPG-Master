"""Concurrency primitives for a single WebSocket game session.

This module deliberately contains no FastAPI or game-domain imports.  The
server owns protocol policy; these types own the much smaller concurrency
contracts that can otherwise become implicit in a large message loop.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TurnRejection(StrEnum):
    """Stable reasons why a new turn lease could not be acquired."""

    SESSION_BUSY = "session_busy"
    WORLD_BUSY = "world_busy"


@dataclass
class TurnLease:
    """An idempotently releasable lease over session and world mutation."""

    _session_lock: threading.Lock
    _world_lock: threading.Lock
    _released: bool = False
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def release(self) -> None:
        """Release both locks exactly once, even when cleanup races."""
        with self._guard:
            if self._released:
                return
            self._released = True
            self._world_lock.release()
            self._session_lock.release()

    @property
    def released(self) -> bool:
        with self._guard:
            return self._released


class SessionTurnGate:
    """Coordinates mutation within one connection and across one world.

    The world lock may be rebound after switching timelines.  Existing leases
    retain the exact lock they acquired, so a later rebind cannot make cleanup
    release the wrong world's lock.
    """

    def __init__(self, world_lock: threading.Lock):
        self._session_lock = threading.Lock()
        self._world_lock = world_lock
        self._state_guard = threading.Lock()

    def try_acquire(self) -> tuple[TurnLease | None, TurnRejection | None]:
        if not self._session_lock.acquire(blocking=False):
            return None, TurnRejection.SESSION_BUSY
        with self._state_guard:
            world_lock = self._world_lock
        if not world_lock.acquire(blocking=False):
            self._session_lock.release()
            return None, TurnRejection.WORLD_BUSY
        return TurnLease(self._session_lock, world_lock), None

    def try_acquire_session(self) -> bool:
        """Reserve connection-local mutation that does not hold a world turn."""
        return self._session_lock.acquire(blocking=False)

    def release_session(self) -> None:
        self._session_lock.release()

    def rebind_world(self, world_lock: threading.Lock) -> None:
        """Select the lock used by future leases.

        Callers must hold the session reservation while rebinding.
        """
        with self._state_guard:
            self._world_lock = world_lock

    @property
    def busy(self) -> bool:
        with self._state_guard:
            world_lock = self._world_lock
        return self._session_lock.locked() or world_lock.locked()


class PendingReply[T]:
    """One synchronous worker-to-client request/reply handshake.

    Replies can optionally be correlated by request id.  Starting a second
    request while one is active is rejected instead of silently overwriting
    the first worker's event.
    """

    def __init__(self, default: T):
        self._default = default
        self._lock = threading.Lock()
        self._event: threading.Event | None = None
        self._request_id: str | None = None
        self._result = default

    def wait(self, *, request_id: str | None = None, timeout: float = 120) -> T:
        event = threading.Event()
        with self._lock:
            if self._event is not None:
                raise RuntimeError("a reply is already pending")
            self._event = event
            self._request_id = request_id
            self._result = self._default
        event.wait(timeout=timeout)
        with self._lock:
            result = self._result
            self._event = None
            self._request_id = None
            return result

    def resolve(self, result: T, *, request_id: str | None = None) -> bool:
        with self._lock:
            if self._event is None:
                return False
            if self._request_id is not None and request_id != self._request_id:
                return False
            self._result = result
            self._event.set()
            return True

    def cancel(self) -> bool:
        """Wake a pending worker with the configured safe default."""
        with self._lock:
            if self._event is None:
                return False
            self._result = self._default
            self._event.set()
            return True

    @property
    def active(self) -> bool:
        with self._lock:
            return self._event is not None


@dataclass
class WsSessionContext:
    """Mutable lifecycle state owned by exactly one WebSocket connection."""

    outbound: Any
    turn_gate: SessionTurnGate
    suggest_reply: PendingReply[bool] = field(
        default_factory=lambda: PendingReply(False)
    )
    decision_reply: PendingReply[str | None] = field(
        default_factory=lambda: PendingReply(None)
    )
    active_lease: TurnLease | None = None
    close_requested: bool = False

    async def reserve_turn(self) -> bool:
        """Acquire a turn lease and emit the stable protocol rejection if busy."""
        lease, rejection = self.turn_gate.try_acquire()
        if rejection is TurnRejection.SESSION_BUSY:
            finishing = not self.outbound.has_active_turn
            await self.outbound.send({
                "type": "turn_rejected",
                "reason": "turn_finalizing" if finishing else "turn_in_progress",
                "message": (
                    "上一回合正在收尾，请稍后重试刚才的行动。"
                    if finishing
                    else "上一回合尚未结束，请等待守秘人完成叙述。"
                ),
            })
            return False
        if rejection is TurnRejection.WORLD_BUSY:
            await self.outbound.send({
                "type": "turn_rejected",
                "reason": "world_turn_in_progress",
                "message": "这个世界的上一回合仍在后台收尾，请稍后再恢复或行动。",
            })
            return False
        self.active_lease = lease
        return True

    def release_turn(self) -> None:
        lease = self.active_lease
        if lease is not None:
            self.active_lease = None
            lease.release()

    @property
    def turn_busy(self) -> bool:
        return self.turn_gate.busy

    def cancel_pending_replies(self) -> None:
        self.suggest_reply.cancel()
        self.decision_reply.cancel()
