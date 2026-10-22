"""Bounded stage receipts; no narrative, model prompt, or arbitrary error text."""

from __future__ import annotations

import re
import time

_ID = re.compile(r"^[\w.:-]{1,160}$")


def record_stage(engine, phase: str, status: str, **references) -> None:
    entry = {"phase": phase, "status": status, "at_monotonic_ms": int(time.monotonic() * 1000)}
    values = {
        "world_id": getattr(getattr(engine, "context", None), "world_id", None),
        "turn_id": getattr(engine, "active_turn_id", None),
        "resolution_id": getattr(getattr(engine, "_resolution_session", None), "id", None),
        "request_id": getattr(engine, "_turn_request_id", None),
        **{key: value for key, value in references.items()
           if key in {"code", "speaker_id", "source_turn_id", "receipt_id"}},
    }
    for key, value in values.items():
        if isinstance(value, str) and _ID.fullmatch(value):
            entry[key] = value
    trace = engine.__dict__.setdefault("_turn_stage_trace", [])
    trace.append(entry)
    del trace[:-64]


def failure_diagnostics(engine) -> dict:
    return {
        "commit_status": "not_committed",
        "stages": list(getattr(engine, "_turn_stage_trace", [])),
        "model_calls": list(getattr(engine, "_turn_diagnostics", [])),
    }
