"""Low-overhead, turn-local latency accounting."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class TurnPerformance:
    started_ns: int = field(default_factory=time.monotonic_ns)
    phases_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    first_visible_ms: float | None = None

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        started = time.monotonic_ns()
        try:
            yield
        finally:
            elapsed = (time.monotonic_ns() - started) / 1_000_000
            self.phases_ms[name] = round(self.phases_ms.get(name, 0.0) + elapsed, 3)

    def add_ms(self, name: str, elapsed_ms: float) -> None:
        self.phases_ms[name] = round(
            self.phases_ms.get(name, 0.0) + max(0.0, float(elapsed_ms)), 3
        )

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + int(amount)

    def mark_first_visible(self) -> None:
        if self.first_visible_ms is None:
            self.first_visible_ms = round(
                (time.monotonic_ns() - self.started_ns) / 1_000_000, 3
            )

    def snapshot(self) -> dict:
        return {
            "turn_total_ms": round(
                (time.monotonic_ns() - self.started_ns) / 1_000_000, 3
            ),
            "first_visible_ms": self.first_visible_ms,
            "phases_ms": dict(self.phases_ms),
            "counters": dict(self.counters),
        }
