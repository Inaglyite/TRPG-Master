"""诊断数字合同：usage/估算口径、窗口来源、回合摘要与多人脱敏投影。"""

from __future__ import annotations

from types import SimpleNamespace

from src.ai.model.model_request import StreamPolicy
from src.ai.model.model_stream_diagnostics import (
    record_model_diagnostic,
    token_contract_fields,
    turn_context_summary,
)
from src.ai.model.route_service import (
    EffectiveSettings,
    RoleBinding,
    parse_role_binding,
    resolve_routes,
)
from src.multiplayer.messages import safe_multiplayer_diagnostics

POLICY = StreamPolicy(
    dynamic_tools=False,
    stream_usage=False,
    prompt_profile="hybrid",
    thinking_type=None,
)

SECTIONS = {
    "system": {"chars": 3000, "estimated_tokens": 1000},
    "history": {"chars": 6000, "estimated_tokens": 2000},
    "tool_schema": {"chars": 900, "estimated_tokens": 300},
}


class TestTokenContract:
    def test_provider_usage_wins_over_estimate(self):
        fields = token_contract_fields(
            {
                "usage": {"prompt_tokens": 5000, "completion_tokens": 320},
                "context_sections": SECTIONS,
                "window_tokens": 65536,
            }
        )
        assert fields["input_tokens"] == 5000
        assert fields["input_source"] == "provider"
        assert fields["output_tokens"] == 320
        assert fields["utilization"] == round(5000 / 65536, 4)

    def test_estimate_when_no_usage(self):
        fields = token_contract_fields(
            {"usage": {}, "context_sections": SECTIONS, "window_tokens": 65536}
        )
        assert fields["input_tokens"] == 3300
        assert fields["input_source"] == "estimate"

    def test_unknown_when_nothing(self):
        fields = token_contract_fields({"usage": {}, "context_sections": {}})
        assert fields["input_tokens"] is None
        assert fields["input_source"] == "unknown"
        # 窗口未知：不产百分比
        assert fields["utilization"] is None

    def test_no_percentage_without_window(self):
        fields = token_contract_fields({"usage": {"prompt_tokens": 100}, "window_tokens": None})
        assert fields["input_tokens"] == 100
        assert fields["utilization"] is None

    def test_capacity_state_lifted_from_envelope(self):
        fields = token_contract_fields(
            {
                "usage": {"prompt_tokens": 100},
                "window_tokens": 65536,
                "request_envelope": {"capacity_metadata": {"state": "compact"}},
            }
        )
        assert fields["capacity_state"] == "compact"


class _Host:
    def __init__(self):
        self.diagnostics: list[dict] = []
        self.client = None

    def _append_model_diagnostic(self, entry: dict) -> None:
        self.diagnostics.append(entry)


class TestRecordEnrichment:
    def test_stream_record_carries_route_contract(self):
        host = _Host()
        host._model_routes = resolve_routes(
            EffectiveSettings(
                narrative=parse_role_binding(
                    {
                        "mode": "custom",
                        "service": {
                            "provider_kind": "openai_compatible",
                            "base_url": "http://127.0.0.1:11434/v1",
                            "api_key": "sk-x",
                            "model_id": "qwen3:32b",
                            "window_tokens": 32768,
                        },
                    },
                    role_label="叙述模型",
                    allow_private=True,
                ),
                judgement=RoleBinding(mode="default", service=None),
                revision=7,
            )
        )
        record_model_diagnostic(
            host,
            "qwen3:32b",
            "story",
            "completed",
            0.0,
            None,
            "stop",
            0,
            [],
            SECTIONS,
            {"prompt_tokens": 1234, "completion_tokens": 50},
            POLICY,
        )
        entry = host.diagnostics[0]
        assert entry["config_revision"] == 7
        assert entry["binding_id"].startswith("svc_")
        assert entry["window_tokens"] == 32768
        assert entry["window_source"] == "manual"
        assert entry["reserved_output_tokens"] is not None
        assert entry["input_source"] == "provider"
        assert entry["utilization"] == round(1234 / 32768, 4)
        assert "sk-x" not in str(entry)

    def test_record_without_routes_still_has_contract(self):
        host = _Host()
        record_model_diagnostic(
            host, "m", "story", "completed", 0.0, None, "stop", 0, [], SECTIONS, {}, POLICY
        )
        entry = host.diagnostics[0]
        assert entry["input_source"] == "estimate"
        assert entry["input_tokens"] == 3300
        assert "config_revision" not in entry


class TestTurnContextSummary:
    def test_prefers_latest_narrative_call(self):
        host = SimpleNamespace(
            _turn_diagnostics=[
                {"role": "adjudication", "model": "judge", "input_tokens": 10},
                {
                    "role": "story",
                    "model": "story-model",
                    "input_tokens": 5000,
                    "input_source": "provider",
                    "window_tokens": 65536,
                    "utilization": 0.076,
                    "config_revision": 3,
                },
                {"role": "audit", "model": "judge", "input_tokens": 20},
            ]
        )
        summary = turn_context_summary(host)
        assert summary["model_id"] == "story-model"
        assert summary["from_narrative"] is True
        assert summary["utilization"] == 0.076

    def test_marks_non_narrative_fallback(self):
        host = SimpleNamespace(
            _turn_diagnostics=[{"role": "audit", "model": "judge", "input_tokens": 20}]
        )
        summary = turn_context_summary(host)
        assert summary["from_narrative"] is False

    def test_empty_is_none(self):
        assert turn_context_summary(SimpleNamespace(_turn_diagnostics=[])) is None


class TestMemberProjection:
    def test_safe_projection_keeps_numeric_contract(self):
        report = {
            "turn_id": "t1",
            "model_calls": [
                {
                    "model": "deepseek-flash",
                    "role": "story",
                    "status": "completed",
                    "input_tokens": 5000,
                    "input_source": "provider",
                    "window_tokens": 65536,
                    "window_source": "manual",
                    "utilization": 0.076,
                    "config_revision": 3,
                    "binding_id": "svc_abc123",
                    "provider_kind": "deepseek",
                    "reserved_output_tokens": 4096,
                    "output_tokens": 320,
                    "capacity_state": "within",
                    "usage": {"prompt_tokens": 5000, "secret": "drop-me"},
                    "api_key": "sk-never",
                    "prompt": "keeper text",
                }
            ],
        }
        safe = safe_multiplayer_diagnostics(report)
        call = safe["model_calls"][0]
        assert call["input_tokens"] == 5000
        assert call["window_tokens"] == 65536
        assert call["utilization"] == 0.076
        assert call["config_revision"] == 3
        assert call["provider_kind"] == "deepseek"
        assert "api_key" not in call
        assert "prompt" not in call
        # usage 数值树保留 token，字符串秘密被剔除
        assert call["usage"] == {"prompt_tokens": 5000}
