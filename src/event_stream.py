"""Ordered WebSocket delivery for one game session."""

from __future__ import annotations

import asyncio
import secrets
import threading
from dataclasses import dataclass
from typing import Any


class TurnAlreadyActiveError(RuntimeError):
    """Raised when a session attempts to overlap two turn lifecycles."""


@dataclass
class _QueuedEvent:
    payload: dict[str, Any] | None
    delivered: asyncio.Future[None] | None = None


class OrderedTurnEventStream:
    """Serialize every server message and identify events from the same turn.

    Engine callbacks run in a worker thread while the WebSocket belongs to the
    asyncio thread.  A single sender task avoids competing ``send_json`` tasks
    and preserves callback order all the way to the client.
    """

    def __init__(self, websocket: Any, loop: asyncio.AbstractEventLoop):
        self._websocket = websocket
        self._loop = loop
        self._loop_thread_id = threading.get_ident()
        self._queue: asyncio.Queue[_QueuedEvent] = asyncio.Queue()
        self._lock = threading.Lock()
        self._stream_id = secrets.token_hex(4)
        self._turn_counter = 0
        self._active_turn_id: str | None = None
        self._sequence = 0
        self._closed = False
        self._send_error: BaseException | None = None
        self._sender_task = loop.create_task(self._send_loop())

    async def send(self, payload: dict[str, Any]) -> None:
        """Send a session message through the same FIFO as turn events."""
        if self._closed:
            return
        delivered = self._loop.create_future()
        self._queue.put_nowait(_QueuedEvent(dict(payload), delivered))
        await delivered

    @property
    def has_active_turn(self) -> bool:
        with self._lock:
            return self._active_turn_id is not None

    async def begin_turn(
        self,
        turn_id: str | None = None,
        *,
        turn_kind: str = "gameplay",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start a turn and send its lifecycle event before engine execution."""
        with self._lock:
            if self._active_turn_id is not None:
                raise TurnAlreadyActiveError(
                    f"turn {self._active_turn_id} is still active"
                )
            self._turn_counter += 1
            turn_id = turn_id or f"{self._stream_id}:{self._turn_counter}"
            self._active_turn_id = turn_id
            self._sequence = 0
            start_payload = {
                "type": "gm_turn_start",
                "turn_kind": turn_kind,
                **(metadata or {}),
            }
            payload = self._decorate_locked(start_payload)
        await self._send_scoped(payload)
        return turn_id

    def emit(self, payload: dict[str, Any]) -> None:
        """Queue an engine callback from either the worker or event-loop thread."""
        with self._lock:
            decorated = self._decorate_locked(payload)
        self._enqueue(_QueuedEvent(decorated))

    def end_turn(self, payload: dict[str, Any] | None = None) -> None:
        """Queue the terminal event, then detach later out-of-turn messages."""
        with self._lock:
            decorated = self._decorate_locked(payload or {"type": "done"})
            self._active_turn_id = None
            self._sequence = 0
        self._enqueue(_QueuedEvent(decorated))

    async def flush(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        if self._closed and self._sender_task.done():
            return
        self._closed = True
        if not self._sender_task.done():
            self._queue.put_nowait(_QueuedEvent(None))
        try:
            await self._sender_task
        except asyncio.CancelledError:
            pass

    def _decorate_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload)
        if self._active_turn_id is not None:
            self._sequence += 1
            decorated.setdefault("turn_id", self._active_turn_id)
            decorated.setdefault("seq", self._sequence)
        return decorated

    async def _send_scoped(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        delivered = self._loop.create_future()
        self._queue.put_nowait(_QueuedEvent(payload, delivered))
        await delivered

    def _enqueue(self, event: _QueuedEvent) -> None:
        if self._closed:
            return
        if threading.get_ident() == self._loop_thread_id:
            self._queue.put_nowait(event)
        else:
            self._loop.call_soon_threadsafe(self._enqueue_on_loop, event)

    def _enqueue_on_loop(self, event: _QueuedEvent) -> None:
        if not self._closed:
            self._queue.put_nowait(event)

    async def _send_loop(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                if event.payload is None:
                    return
                await self._websocket.send_json(event.payload)
                if event.delivered is not None and not event.delivered.done():
                    event.delivered.set_result(None)
            except BaseException as exc:
                self._send_error = exc
                self._closed = True
                if event.delivered is not None and not event.delivered.done():
                    event.delivered.set_exception(exc)
                self._fail_pending(exc)
                return
            finally:
                self._queue.task_done()

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if event.delivered is not None and not event.delivered.done():
                event.delivered.set_exception(exc)
            self._queue.task_done()
