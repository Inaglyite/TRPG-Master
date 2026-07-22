#!/usr/bin/env python3
"""Repeatable local micro-benchmark for turn infrastructure (no model calls)."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import RuntimeContext  # noqa: E402
from src.tools import execute_function  # noqa: E402


def _measure(operation, iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "iterations": iterations,
        "mean_ms": round(statistics.fmean(samples), 3),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(samples), 3),
    }


def main() -> int:
    iterations = max(10, int(sys.argv[1]) if len(sys.argv) > 1 else 100)
    with tempfile.TemporaryDirectory(prefix="trpg-perf-") as temporary:
        context = RuntimeContext.create(
            "benchmark-world",
            "mansion_of_madness",
            project_root=PROJECT_ROOT,
            runtime_root=Path(temporary),
        )
        results = {
            "dice_in_process": _measure(
                lambda: execute_function("dice_roll", {"spec": "2d6+1"}, context=context),
                iterations,
            ),
            "state_read_database": _measure(
                lambda: context.world_store.load(), iterations
            ),
        }
        with context.world_store.turn_cache():
            results["state_read_turn_cache"] = _measure(
                lambda: context.world_store.load(), iterations
            )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
