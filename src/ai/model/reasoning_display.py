"""Private, bounded presentation of provider reasoning; never narrative/history."""

from __future__ import annotations

import time

from src.ai.model.provider_adapter import extract_reasoning_text

DISPLAY_LIMIT = 65536


class ReasoningDisplay:
    def __init__(self, host, request_id, model, role):
        self.host, self.request_id, self.model, self.role = host, request_id, model, role
        self.text = ""
        self.pending = ""
        self.sent = 0
        self.started = False
        self.finished = False
        self.last_flush = time.monotonic()

    def _send(self, phase, **fields):
        callback = getattr(getattr(self.host, "cb", None), "on_private_event", None)
        if not callable(callback):
            return
        context = getattr(self.host, "context", None)
        event = {
            "type": "model_reasoning",
            "phase": phase,
            "request_id": self.request_id,
            "model": self.model,
            "role": self.role,
            **fields,
        }
        world = getattr(context, "world_id", None)
        turn = getattr(self.host, "active_turn_id", None) or getattr(
            self.host, "_active_turn_id", None
        )
        if world:
            event["world_id"] = world
        if turn:
            event["turn_id"] = turn
        try:
            callback(event)
        except Exception:
            pass

    def feed_text(self, text):
        if not isinstance(text, str) or not text or self.finished:
            return
        self.text += text  # Preserve the provider's exact tool-passback evidence.
        if not self.started:
            self.started = True
            self._send("start")
        remaining = DISPLAY_LIMIT - self.sent - len(self.pending)
        self.pending += text[: max(0, remaining)]
        if len(self.pending) >= 256 or time.monotonic() - self.last_flush >= 0.1:
            self._flush()

    def consume(self, chunk):
        self.feed_text(extract_reasoning_text(chunk))

    def _flush(self):
        while self.pending:
            text, self.pending = self.pending[:2048], self.pending[2048:]
            self.sent += len(text)
            self._send("delta", text=text)
        self.last_flush = time.monotonic()

    def finish(self, status="completed"):
        if self.finished:
            return
        self.finished = True
        if not self.started:
            return
        self._flush()
        self._send("end", status=status, truncated=len(self.text) > DISPLAY_LIMIT)
        from src.app.reasoning_debug import record

        record(
            self.host,
            self.text,
            request_id=self.request_id,
            model=self.model,
            role=self.role,
            status=status,
        )
