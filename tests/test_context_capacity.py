"""H2 context-capacity base tests: env parsing, target/hard, diagnostics.

The capacity base is read-only: it computes thresholds and a status from an
*estimated* input token count and never deletes/truncates any content, and
diagnostics never carry message bodies (metadata only).
"""

from __future__ import annotations

import pytest

import src.config as config
from src.context_capacity import (
    STATUS_COMPACT,
    STATUS_IRREDUCIBLE,
    STATUS_WITHIN,
    CapacityDiagnostic,
    CapacityPlan,
    build_plan,
    evaluate,
)


def _reload_config(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    """Set a clean capacity env for deterministic runtime-read tests.

    The config helpers read ``os.environ`` at call time. Reloading the module
    would redefine ``CapacityPlan`` while this test module still holds the old
    class object, producing an unrelated ``isinstance`` false negative.
    """
    monkeypatch.delenv("TRPG_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("TRPG_CONTEXT_TARGET_RATIO", raising=False)
    monkeypatch.delenv("TRPG_MAX_OUTPUT_TOKENS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------


def test_context_window_defaults_to_65536(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config(monkeypatch, {})
    assert config.context_window_tokens() == 65_536
    assert config.context_target_ratio() == 0.78
    assert config.max_output_tokens() == 4096


def test_context_window_bounded_and_invalid_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config(monkeypatch, {"TRPG_CONTEXT_WINDOW_TOKENS": "65536"})
    assert config.context_window_tokens() == 65_536

    _reload_config(monkeypatch, {"TRPG_CONTEXT_WINDOW_TOKENS": "100"})  # below min
    assert config.context_window_tokens() == config.CONTEXT_WINDOW_TOKENS_MIN

    _reload_config(monkeypatch, {"TRPG_CONTEXT_WINDOW_TOKENS": "999999999"})  # above max
    assert config.context_window_tokens() == config.CONTEXT_WINDOW_TOKENS_MAX

    _reload_config(monkeypatch, {"TRPG_CONTEXT_WINDOW_TOKENS": "abc"})  # non-numeric
    assert config.context_window_tokens() == config.CONTEXT_WINDOW_TOKENS_DEFAULT


def test_context_target_ratio_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config(monkeypatch, {"TRPG_CONTEXT_TARGET_RATIO": "0.5"})
    assert config.context_target_ratio() == 0.5

    _reload_config(monkeypatch, {"TRPG_CONTEXT_TARGET_RATIO": "0.05"})  # below min
    assert config.context_target_ratio() == config.CONTEXT_TARGET_RATIO_MIN

    _reload_config(monkeypatch, {"TRPG_CONTEXT_TARGET_RATIO": "0.99"})  # above max
    assert config.context_target_ratio() == config.CONTEXT_TARGET_RATIO_MAX

    _reload_config(monkeypatch, {"TRPG_CONTEXT_TARGET_RATIO": "junk"})  # non-numeric
    assert config.context_target_ratio() == config.CONTEXT_TARGET_RATIO_DEFAULT


def test_max_output_tokens_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config(monkeypatch, {"TRPG_MAX_OUTPUT_TOKENS": "2048"})
    assert config.max_output_tokens() == 2048

    _reload_config(monkeypatch, {"TRPG_MAX_OUTPUT_TOKENS": "0"})
    assert config.max_output_tokens() == 1

    _reload_config(monkeypatch, {"TRPG_MAX_OUTPUT_TOKENS": "nope"})
    assert config.max_output_tokens() == config.MAX_OUTPUT_TOKENS_DEFAULT


def test_max_output_keeps_a_compaction_band(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config(
        monkeypatch,
        {
            "TRPG_CONTEXT_WINDOW_TOKENS": "4000",
            "TRPG_CONTEXT_TARGET_RATIO": "0.50",
            "TRPG_MAX_OUTPUT_TOKENS": "99999",
        },
    )
    # Runtime window has a defensive 8192-token floor: target=4096, so at
    # least one token remains between target and the hard boundary.
    assert config.max_output_tokens() == 4095


# ---------------------------------------------------------------------------
# Threshold plan: target / hard / headroom
# ---------------------------------------------------------------------------


def test_plan_defaults_65536_window_and_78_percent_target() -> None:
    plan = build_plan()
    assert plan.window_tokens == 65_536
    assert plan.target_ratio == 0.78
    assert plan.target_tokens == int(65_536 * 0.78)
    # Hard threshold leaves room for a maximal output (4096).
    assert plan.hard_tokens == 65_536 - 4096
    assert plan.max_output_tokens == 4096
    assert plan.target_tokens < plan.hard_tokens


def test_plan_explicit_args_override() -> None:
    plan = build_plan(window_tokens=10_000, target_ratio=0.80, max_output_tokens=2000)
    assert plan.target_tokens == 8000
    assert plan.hard_tokens == 8000
    assert plan.to_dict() == {
        "window_tokens": 10_000,
        "target_ratio": 0.80,
        "target_tokens": 8000,
        "hard_tokens": 8000,
        "max_output_tokens": 2000,
    }


def test_plan_hard_never_exceeds_window() -> None:
    # Even an absurd max output cannot push hard above the window.
    plan = build_plan(window_tokens=4096, target_ratio=0.5, max_output_tokens=100_000)
    assert plan.hard_tokens == 0


# ---------------------------------------------------------------------------
# Status classification: within / compact / irreducible
# ---------------------------------------------------------------------------


def test_status_within_below_target() -> None:
    result = evaluate(1000, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    assert result.status == STATUS_WITHIN
    assert result.estimated_input_tokens == 1000
    assert result.utilization == pytest.approx(0.1)


def test_status_compact_at_and_above_target_below_hard() -> None:
    plan = build_plan(window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    # target = 5000, hard = 8000
    at_target = evaluate(5000, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    assert at_target.status == STATUS_COMPACT
    below_hard = evaluate(7999, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    assert below_hard.status == STATUS_COMPACT
    assert plan.target_tokens == 5000
    assert plan.hard_tokens == 8000


def test_status_irreducible_at_hard_and_above() -> None:
    result = evaluate(8000, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    assert result.status == STATUS_IRREDUCIBLE
    above = evaluate(9000, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    assert above.status == STATUS_IRREDUCIBLE


def test_negative_estimate_fails_open_to_within() -> None:
    result = evaluate(-5, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    assert result.status == STATUS_WITHIN
    assert result.estimated_input_tokens == 0


def test_zero_window_never_compacts() -> None:
    result = evaluate(100, window_tokens=0, target_ratio=0.5, max_output_tokens=0)
    assert result.status == STATUS_WITHIN
    assert result.utilization == 0.0


# ---------------------------------------------------------------------------
# Diagnostics: metadata only, never message content
# ---------------------------------------------------------------------------


def test_diagnostic_never_contains_message_content() -> None:
    result = evaluate(7000, window_tokens=10_000, target_ratio=0.5, max_output_tokens=2000)
    payload = result.to_dict()
    serialized = str(payload)
    # Threshold/status metadata only — no body text can sneak in via keys.
    assert set(payload) == {
        "estimated_input_tokens",
        "status",
        "utilization",
        "window_tokens",
        "target_ratio",
        "target_tokens",
        "hard_tokens",
        "max_output_tokens",
    }
    assert "content" not in serialized
    assert "message" not in serialized
    assert "prompt" not in serialized


def test_diagnostic_dataclasses_roundtrip() -> None:
    plan = build_plan(window_tokens=4096, target_ratio=0.78, max_output_tokens=1024)
    diag = CapacityDiagnostic(
        estimated_input_tokens=3000,
        status=STATUS_COMPACT,
        plan=plan,
        utilization=0.75,
    )
    assert isinstance(plan, CapacityPlan)
    assert diag.to_dict()["status"] == STATUS_COMPACT
    assert diag.to_dict()["estimated_input_tokens"] == 3000
