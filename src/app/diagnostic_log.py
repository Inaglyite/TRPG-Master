"""Bounded JSONL receipts and redacted narrative evidence; never change game outcomes."""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.app.logger import LOG_DIR, redact

_LOCK = threading.Lock()
_HANDLER: RotatingFileHandler | None = None
_WARNED = False
_RUN_ID = uuid.uuid4().hex[:12]
_IDS = frozenset({
    "world_id", "turn_id", "module", "request_id", "resolution_id", "receipt_id",
    "source_turn_id", "speaker_id", "phase", "status", "code", "model", "role",
    "finish_reason", "prompt_profile", "thinking_mode", "tool", "call_id", "parent_call_id",
    "path", "clue_id", "skill_id", "check_id", "scene_id", "error_type", "event_type", "kind",
    "error_class", "reason", "severity", "source_id", "output_id", "output_sha256",
})
_NUMBERS = frozenset({
    "elapsed_ms", "first_token_ms", "tool_count", "message_count", "input_tokens", "output_tokens",
    "input_chars", "output_chars", "choice_count", "segment_count", "ok", "success", "roll",
    "target", "san_loss", "san_before", "san_after", "loss", "config_revision", "backoff_ms",
})
_ID = re.compile(r"^[\w.@:/+-]{1,200}$")


class _PrivateHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def handleError(self, record):  # noqa: N802 — logging API
        _warn()


def _warn():
    global _WARNED
    if not _WARNED:
        _WARNED = True
        try:
            print("[diagnostics] 日志文件无法写入；游戏继续运行，请检查日志目录权限与磁盘空间。", file=sys.stderr)
        except (OSError, ValueError):
            pass


def _handler():
    global _HANDLER
    with _LOCK:
        if _HANDLER is None:
            LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            _HANDLER = _PrivateHandler(LOG_DIR / "diagnostics.jsonl", maxBytes=5 * 1024 * 1024,
                                       backupCount=3, encoding="utf-8")
            _HANDLER.setFormatter(logging.Formatter("%(message)s"))
        return _HANDLER


def emit(event: str, host=None, **fields) -> None:
    """Whitelist fields before serialization; never serialize arbitrary payloads."""
    if os.environ.get("TRPG_DIAGNOSTICS", "1").lower() in {"0", "false", "off"}:
        return
    try:
        context = getattr(host, "context", None)
        references = {
            "world_id": getattr(context, "world_id", None),
            "module": getattr(context, "module_name", None),
            "turn_id": getattr(host, "_active_turn_id", None),
            "request_id": getattr(host, "_turn_request_id", None),
            **fields,
        }
        row = {"v": 2, "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
               "pid": os.getpid(), "run_id": _RUN_ID, "thread_id": threading.get_ident(),
               "event": event if _ID.fullmatch(event) else "invalid_event"}
        for key, value in references.items():
            if key in _IDS and isinstance(value, str) and _ID.fullmatch(value):
                row[key] = redact(value)
            elif key in _NUMBERS and isinstance(value, (int, float, bool)) and math.isfinite(value):
                row[key] = value
        # Output is admitted only at named narrative boundaries, never generic payloads.
        if event in {"dialogue.raw", "dialogue.accepted", "dialogue.emitted", "turn.output", "consistency.raw"} and isinstance(fields.get("output_text"), str):
            clean = redact(fields["output_text"])
            row["output_sha256"] = hashlib.sha256(clean.encode("utf-8")).hexdigest()
            row["output_chars"] = len(clean)
            enabled = os.environ.get("TRPG_DIAGNOSTIC_CONTENT", "1").lower() not in {"0", "false", "off"}
            row["content_recorded"] = enabled
            row["content_truncated"] = enabled and len(clean) > 65536
            if enabled:
                row["output_text"] = clean[:65536]
        # No exception message, source code or locals: only frame locations for debugging.
        if isinstance(fields.get("exception"), BaseException):
            exc = fields["exception"]
            row["error_type"] = type(exc).__name__
            row["frames"] = [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                             for f in traceback.extract_tb(exc.__traceback__)[-12:]]
        line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        _handler().handle(logging.LogRecord("trpg.diagnostics", logging.INFO, "", 0, line, (), None))
    except Exception:
        _warn()


def trace_tool(function):
    """Observe tool receipts without rewriting arguments, results or exceptions."""
    @functools.wraps(function)
    def wrapped(host, name, args):
        started = time.monotonic()
        call_id = uuid.uuid4().hex[:16]
        references = {key: args[key] for key in ("path", "clue_id", "skill_id", "scene_id", "severity", "source_id") if key in args}
        emit("tool.started", host, tool=name, call_id=call_id, **references)
        try:
            result = function(host, name, args)
        except Exception as exc:
            emit("tool.failed", host, tool=name, call_id=call_id, exception=exc,
                 elapsed_ms=round((time.monotonic() - started) * 1000, 2))
            raise
        values = {}
        if isinstance(result, str) and len(result) <= 262144:
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    values = {key: value for key, value in parsed.items() if key in {"ok", "success", "roll", "target", "san_loss", "san_before", "san_after", "loss", "code", "check_id"}}
            except Exception:  # malformed/deep results must not break a successful tool
                pass
        emit("tool.completed", host, **values, tool=name, call_id=call_id,
             output_chars=len(result) if isinstance(result, str) else 0,
             elapsed_ms=round((time.monotonic() - started) * 1000, 2))
        return result
    return wrapped
