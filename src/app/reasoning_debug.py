"""Explicitly authorized model reasoning evidence, separate from public receipts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from src.app.logger import LOG_DIR, redact

_LOCK = threading.Lock()
_HANDLER = None


class _PrivateHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def handleError(self, record):  # noqa: N802
        pass


def record(host, text, *, request_id, model, role, status):
    if not text or any(
        os.environ.get(key, "1").lower() in {"0", "false", "off"}
        for key in ("TRPG_REASONING_DEBUG", "TRPG_DIAGNOSTICS", "TRPG_DIAGNOSTIC_CONTENT")
    ):
        return
    try:
        context = getattr(host, "context", None)
        clean = redact(text)
        row = {
            "v": 1,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": "model.reasoning",
            "world_id": getattr(context, "world_id", None),
            "module": getattr(context, "module_name", None),
            "turn_id": getattr(host, "active_turn_id", None)
            or getattr(host, "_active_turn_id", None),
            "trigger_request_id": getattr(host, "trigger_request_id", None),
            "request_id": request_id,
            "model": model,
            "role": role,
            "status": status,
            "chars": len(clean),
            "sha256": hashlib.sha256(clean.encode()).hexdigest(),
            "truncated": len(clean) > 65536,
            "text": clean[:65536],
        }
        global _HANDLER
        with _LOCK:
            if _HANDLER is None:
                LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
                _HANDLER = _PrivateHandler(
                    LOG_DIR / "reasoning-debug.jsonl",
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                _HANDLER.setFormatter(logging.Formatter("%(message)s"))
            line = redact(json.dumps(row, ensure_ascii=False))
            _HANDLER.handle(
                logging.LogRecord("trpg.reasoning_debug", logging.DEBUG, "", 0, line, (), None)
            )
    except Exception:
        # Debug evidence must never change game outcomes.
        pass
