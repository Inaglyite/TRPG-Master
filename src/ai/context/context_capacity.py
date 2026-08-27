"""H2 context-capacity base: window-aware thresholds, never deletes content.

This is the read-only capacity foundation for proactive compaction.  Given an
*estimated* input token count it computes the provider window, the target
threshold (``window * TRPG_CONTEXT_TARGET_RATIO``, default 0.78) and the hard
threshold (window minus reserved max output), and returns one status:

- ``within``    — below target: no action needed.
- ``compact``   — at/above target but below hard: compaction is *suggested*
  before the next request; the caller decides how to act without deleting the
  authoritative surface.
- ``irreducible`` — at/above hard: even compaction cannot guarantee a
  maximal reply fits; nothing here deletes or truncates any content.

Diagnostics never include message bodies: only token counts and thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.app import config

STATUS_WITHIN = "within"
STATUS_COMPACT = "compact"
STATUS_IRREDUCIBLE = "irreducible"


@dataclass(frozen=True)
class CapacityPlan:
    """Threshold plan for one provider window."""

    window_tokens: int
    target_ratio: float
    target_tokens: int
    hard_tokens: int
    max_output_tokens: int

    def to_dict(self) -> dict[str, int | float]:
        return {
            "window_tokens": self.window_tokens,
            "target_ratio": self.target_ratio,
            "target_tokens": self.target_tokens,
            "hard_tokens": self.hard_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True)
class CapacityDiagnostic:
    """Metadata-only evaluation result; never carries message content."""

    estimated_input_tokens: int
    status: str
    plan: CapacityPlan
    # Fraction of the window the estimated input consumes (0..1).
    utilization: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_input_tokens": self.estimated_input_tokens,
            "status": self.status,
            "utilization": round(self.utilization, 4),
            **self.plan.to_dict(),
        }


def build_plan(
    *,
    window_tokens: int | None = None,
    target_ratio: float | None = None,
    max_output_tokens: int | None = None,
) -> CapacityPlan:
    """Build a threshold plan, honoring config defaults when args are None."""
    window = window_tokens if window_tokens is not None else config.context_window_tokens()
    ratio = (
        target_ratio
        if target_ratio is not None
        else config.context_target_ratio()
    )
    output = (
        max_output_tokens
        if max_output_tokens is not None
        else config.max_output_tokens()
    )
    target = int(window * ratio)
    hard = max(0, window - output)
    return CapacityPlan(
        window_tokens=window,
        target_ratio=ratio,
        target_tokens=target,
        hard_tokens=hard,
        max_output_tokens=output,
    )


def evaluate(
    estimated_input_tokens: int,
    *,
    window_tokens: int | None = None,
    target_ratio: float | None = None,
    max_output_tokens: int | None = None,
) -> CapacityDiagnostic:
    """Classify an estimated input against the window thresholds.

    ``estimated_input_tokens`` is always clamped to ``>= 0``; negative or
    bogus estimates are treated as zero (fail open to ``within``, never
    trigger compaction on bad input).  The classification is:
    ``within`` < target <= ``compact`` < hard <= ``irreducible``.
    """
    estimated = max(0, int(estimated_input_tokens))
    plan = build_plan(
        window_tokens=window_tokens,
        target_ratio=target_ratio,
        max_output_tokens=max_output_tokens,
    )
    # ``window_tokens=0`` is only reachable through a direct caller/test (the
    # runtime config is bounded). Treat an unknown/disabled capacity as a
    # no-op rather than incorrectly declaring every request irreducible.
    if plan.window_tokens <= 0:
        status = STATUS_WITHIN
    elif estimated >= plan.hard_tokens:
        status = STATUS_IRREDUCIBLE
    elif estimated >= plan.target_tokens:
        status = STATUS_COMPACT
    else:
        status = STATUS_WITHIN
    utilization = estimated / plan.window_tokens if plan.window_tokens else 0.0
    return CapacityDiagnostic(
        estimated_input_tokens=estimated,
        status=status,
        plan=plan,
        utilization=min(1.0, utilization),
    )
