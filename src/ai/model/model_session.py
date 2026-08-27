"""Provider-neutral state for one conversational model session."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelSession:
    """Own message history and the cancellable provider stream lifecycle."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    _cancel_event: threading.Event = field(default_factory=threading.Event, init=False)
    _stream_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _active_stream: object | None = field(default=None, init=False)

    def replace_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages

    def reset(self, system_message: dict[str, Any]) -> None:
        self.messages = [system_message]
        self.diagnostics = []
        self.clear_cancellation()

    def clear_cancellation(self) -> None:
        self._cancel_event.clear()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_event.is_set()

    def set_active_stream(self, stream: object | None) -> None:
        with self._stream_lock:
            self._active_stream = stream

    def clear_active_stream(self, stream: object) -> None:
        with self._stream_lock:
            if self._active_stream is stream:
                self._active_stream = None

    def cancel(self) -> None:
        """Signal cancellation and best-effort close the current provider stream."""
        self._cancel_event.set()
        with self._stream_lock:
            stream = self._active_stream
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass

    def append_diagnostic(self, diagnostic: dict[str, Any]) -> None:
        self.diagnostics.append(diagnostic)
