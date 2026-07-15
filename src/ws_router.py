"""Typed, explicit routing for the WebSocket application protocol."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


WsPayload = dict[str, Any]
WsHandler = Callable[[WsPayload], Awaitable[None]]


class DuplicateMessageHandlerError(ValueError):
    """Raised when two features attempt to own the same protocol message."""


@dataclass(frozen=True)
class DispatchResult:
    handled: bool
    message_type: str


class WsMessageRouter:
    """Registry-based async message dispatcher.

    Registration is intentionally strict: duplicate ownership is an import-time
    architecture error, not a last-writer-wins behavior discovered at runtime.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, WsHandler] = {}

    def add(self, message_type: str, handler: WsHandler) -> None:
        normalized = message_type.strip()
        if not normalized:
            raise ValueError("message type must not be empty")
        if normalized in self._handlers:
            raise DuplicateMessageHandlerError(normalized)
        self._handlers[normalized] = handler

    def handler(self, message_type: str) -> Callable[[WsHandler], WsHandler]:
        def register(fn: WsHandler) -> WsHandler:
            self.add(message_type, fn)
            return fn

        return register

    async def dispatch(self, payload: WsPayload) -> DispatchResult:
        message_type = payload.get("type")
        if not isinstance(message_type, str) or not message_type.strip():
            return DispatchResult(False, "")
        handler = self._handlers.get(message_type)
        if handler is None:
            return DispatchResult(False, message_type)
        await handler(payload)
        return DispatchResult(True, message_type)

    @property
    def message_types(self) -> frozenset[str]:
        return frozenset(self._handlers)
