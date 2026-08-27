#!/usr/bin/env python3
"""Repeatable local H1 tool-pipeline benchmark (no model/network required).

It compares a recorded-shaped, deterministic local handler with the same
handler wrapped by ``ToolPipeline``.  The default delay models normal local
orchestration without including provider latency, so the result can enforce the
H1 budget: pipeline p95 overhead < 10ms and total p95 increase <= 5%.
"""

from __future__ import annotations

import argparse
import time
from types import SimpleNamespace

from src.ai.tools.registry import tool_catalog_for_names
from src.ai.tools.tool_pipeline import ToolPipeline
from src.ai.tools.tool_policy import MODEL_CALLER, ToolRequestSnapshot, attach_request_snapshot


class _BenchmarkEngine:
    def __init__(self, delay_ms: float) -> None:
        self.context = SimpleNamespace(world_id="benchmark-world")
        self._active_turn_id = "benchmark-turn"
        self.delay_s = max(0.0, delay_ms / 1000)
        self._tool_pipeline_audit: list[dict] = []

    @property
    def active_turn_id(self) -> str:
        return self._active_turn_id

    def raise_if_turn_cancelled(self) -> None:
        return None

    def _execute_model_tool(self, _name: str, _args: dict, *, player_action: str = "") -> str:
        del player_action
        if self.delay_s:
            time.sleep(self.delay_s)
        return '{"ok":true,"total":4}'

    def record_tool_pipeline_outcome(self, outcome: dict, _plan: dict) -> None:
        self._tool_pipeline_audit.append(dict(outcome))


def _call(index: int) -> dict:
    snapshot = ToolRequestSnapshot.create(
        step=index + 1,
        profile="benchmark",
        caller=MODEL_CALLER,
        tools=tool_catalog_for_names(["dice_roll"]),
    )
    return attach_request_snapshot(
        {
            "id": f"benchmark-{index}",
            "type": "function",
            "function": {"name": "dice_roll", "arguments": '{"spec":"1d6"}'},
        },
        snapshot,
    )


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]


def run(*, iterations: int, delay_ms: float) -> dict[str, float]:
    baseline_engine = _BenchmarkEngine(delay_ms)
    baseline: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        baseline_engine._execute_model_tool("dice_roll", {"spec": "1d6"})
        baseline.append((time.perf_counter() - started) * 1000)

    engine = _BenchmarkEngine(delay_ms)
    pipeline = ToolPipeline(engine, timeout_ms=5000)
    wrapped: list[float] = []
    for index in range(iterations):
        started = time.perf_counter()
        outcome = pipeline.execute(_call(index))
        if outcome.status != "ok":  # pragma: no cover - executable guard
            raise RuntimeError(f"benchmark tool failed: {outcome.status}")
        wrapped.append((time.perf_counter() - started) * 1000)

    baseline_p95 = _p95(baseline)
    wrapped_p95 = _p95(wrapped)
    overhead = max(0.0, wrapped_p95 - baseline_p95)
    return {
        "iterations": float(iterations),
        "handler_delay_ms": delay_ms,
        "baseline_p95_ms": baseline_p95,
        "pipeline_p95_ms": wrapped_p95,
        "pipeline_overhead_p95_ms": overhead,
        "local_orchestration_increase_pct": (
            ((wrapped_p95 - baseline_p95) / baseline_p95 * 100) if baseline_p95 else 0.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--handler-delay-ms", type=float, default=10.0)
    parser.add_argument("--assert-budget", action="store_true")
    args = parser.parse_args()
    results = run(iterations=max(10, args.iterations), delay_ms=max(0.0, args.handler_delay_ms))
    for key, value in results.items():
        print(f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}")
    if args.assert_budget and (
        results["pipeline_overhead_p95_ms"] >= 10 or results["local_orchestration_increase_pct"] > 5
    ):
        print("H1 tool pipeline performance budget exceeded")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
