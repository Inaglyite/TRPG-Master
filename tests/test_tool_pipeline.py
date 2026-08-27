from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai.model.model_request import StreamPolicy, prepare_model_request
from src.ai.tools.registry import tool_catalog_for_names
from src.ai.tools.tool_pipeline import ToolPipeline
from src.ai.tools.tool_policy import MODEL_CALLER, ToolRequestSnapshot, attach_request_snapshot
from src.ai.tools.tool_request_authority import issue_model_request
from src.app.agent_graph import _execute_tools
from src.app.engine_primitives import TurnCancelledError
from src.storage.database import Base, ModelCall, World, get_engine, session_scope
from src.storage.database_turn_journal import DatabaseTurnJournal


def _model_call(engine: _PipelineEngine, call_id: str, name: str, arguments: str) -> dict:
    snapshot = ToolRequestSnapshot.create(
        step=3,
        profile="story:fixture",
        caller=MODEL_CALLER,
        tools=tool_catalog_for_names([name]),
        world_id=engine.context.world_id,
        turn_id=engine.active_turn_id,
    )
    call = attach_request_snapshot(
        {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        },
        snapshot,
    )
    issue_model_request(engine, snapshot, tool_catalog_for_names([name]))
    return call


def _model_call_for_legacy(engine, call_id: str, name: str, arguments: str) -> dict:
    """Issue a request for the legacy graph fixture without a RuntimeContext."""
    catalog = tool_catalog_for_names([name])
    snapshot = ToolRequestSnapshot.create(
        step=3,
        profile="story:fixture",
        caller=MODEL_CALLER,
        tools=catalog,
    )
    call = attach_request_snapshot(
        {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        },
        snapshot,
    )
    issue_model_request(engine, snapshot, catalog)
    return call


class _PipelineEngine:
    def __init__(self) -> None:
        self.context = SimpleNamespace(world_id="world-pipeline")
        self._active_turn_id = "turn-pipeline"
        self.executions: list[tuple[str, dict]] = []
        self.audits: list[tuple[dict, dict]] = []
        self.cancelled = False

    @property
    def active_turn_id(self) -> str:
        return self._active_turn_id

    def raise_if_turn_cancelled(self) -> None:
        if self.cancelled:
            raise TurnCancelledError("test cancellation")

    def _execute_model_tool(self, name: str, args: dict, *, player_action: str = "") -> str:
        self.executions.append((name, dict(args)))
        return '{"ok":true}'

    def record_tool_pipeline_outcome(self, outcome: dict, plan: dict) -> None:
        self.audits.append((dict(outcome), dict(plan)))


def test_pipeline_semantic_idempotency_records_each_call_without_duplicate_write():
    engine = _PipelineEngine()
    pipeline = ToolPipeline(engine, timeout_ms=5000)

    first = pipeline.execute(_model_call(engine, "call-first", "state_add_item", '{"item":"钥匙"}'))
    retry = pipeline.execute(_model_call(engine, "call-retry", "state_add_item", '{"item":"钥匙"}'))

    assert first.status == "ok"
    assert retry.status == "reused"
    assert retry.reused is True
    assert engine.executions == [("state_add_item", {"item": "钥匙"})]
    assert [audit[0]["call_id"] for audit in engine.audits] == ["call-first", "call-retry"]
    assert {audit[0]["turn_id"] for audit in engine.audits} == {"turn-pipeline"}
    assert all("args" not in audit[0] for audit in engine.audits)


def test_pipeline_cancellation_prevents_handler_execution():
    engine = _PipelineEngine()
    engine.cancelled = True
    pipeline = ToolPipeline(engine, timeout_ms=5000)

    with pytest.raises(TurnCancelledError):
        pipeline.execute(_model_call(engine, "call-cancelled", "state_add_item", '{"item":"钥匙"}'))

    assert engine.executions == []


def test_pipeline_deadline_before_execution_is_one_safe_tool_result():
    engine = _PipelineEngine()
    pipeline = ToolPipeline(engine, timeout_ms=1)
    with patch("src.ai.tools.tool_pipeline.time.monotonic", side_effect=[0.0, 0.01, 0.011]):
        outcome = pipeline.execute(
            _model_call(engine, "call-timeout", "state_add_item", '{"item":"钥匙"}')
        )

    assert outcome.status == "timeout"
    assert engine.executions == []
    assert engine.audits[0][0]["error_code"] == "deadline_before_execution"


def test_pipeline_invalid_output_is_not_passed_back_to_model():
    engine = _PipelineEngine()
    engine._execute_model_tool = lambda *_args, **_kwargs: object()
    pipeline = ToolPipeline(engine, timeout_ms=5000)

    outcome = pipeline.execute(
        _model_call(engine, "call-bad-output", "dice_roll", '{"spec":"1d6"}')
    )

    assert outcome.status == "invalid_output"
    assert "object" not in outcome.output
    assert outcome.error_code == "invalid_tool_output"


def test_old_graph_path_can_shadow_v2_without_second_execution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRPG_TOOL_PIPELINE_V2", "0")
    monkeypatch.setenv("TRPG_TOOL_PIPELINE_SHADOW", "1")
    calls: list[tuple[str, dict]] = []
    shadows: list[dict] = []
    engine = SimpleNamespace(
        messages=[],
        cb=SimpleNamespace(on_tension=lambda *_args: None),
        _execute_tool=lambda name, args: calls.append((name, dict(args))) or '{"ok":true}',
        _maybe_hint_optional_skill=lambda _name: None,
        record_tool_pipeline_shadow=lambda record: shadows.append(dict(record)),
    )

    call = _model_call_for_legacy(engine, "call-shadow", "state_add_item", '{"item":"钥匙"}')
    result = _execute_tools(
        {
            "engine": engine,
            "tool_round": 0,
            "tool_calls": [call],
        }
    )

    assert calls == [("state_add_item", {"item": "钥匙"})]
    assert result["executed_tools"][0]["name"] == "state_add_item"
    assert len(shadows) == 1
    assert shadows[0]["mode"] == "shadow"
    assert shadows[0]["would_execute"] is True
    assert shadows[0]["call_id"] == "call-shadow"
    assert shadows[0]["step"] == 3
    assert shadows[0]["name"] == "state_add_item"
    assert shadows[0]["duration_ms"] >= 0


def test_typed_request_envelope_freezes_capacity_context_and_turn_identity():
    host = SimpleNamespace(
        context=SimpleNamespace(world_id="world-envelope"),
        _active_turn_id="turn-envelope",
        _tool_request_step=0,
        messages=[
            {"role": "system", "content": "core rules"},
            {"role": "user", "content": "inspect the desk"},
        ],
    )
    prepared = prepare_model_request(
        host,
        "deepseek-v4-flash",
        policy=StreamPolicy(
            dynamic_tools=True,
            stream_usage=True,
            prompt_profile="hybrid",
            thinking_type=None,
        ),
        system_overlay=None,
        system_prompt_override=None,
        enable_tools=True,
        temperature=0.8,
        messages_override=None,
    )

    envelope = prepared.envelope
    audit = envelope.audit_dict()
    assert envelope.request_id == prepared.request_snapshot.request_id
    assert (envelope.world_id, envelope.turn_id, envelope.step) == (
        "world-envelope",
        "turn-envelope",
        1,
    )
    assert audit["capacity"]["max_output_tokens"] == 4096
    assert audit["capacity"]["status"] == "within"
    assert audit["capacity"]["estimated_input_tokens"] > 0
    assert audit["tool_catalog_digest"] == prepared.request_snapshot.tool_catalog_digest
    assert [section["id"] for section in audit["sections"]] == [
        "system",
        "history",
        "tool_schema",
    ]
    assert "core rules" not in str(audit)


def test_database_turn_journal_keeps_active_and_completed_v2_audit(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="world-pipeline", module_name="module-a"))
    world_dir = tmp_path / "worlds" / "world-pipeline"
    with patch.dict(os.environ, {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"}):
        journal = DatabaseTurnJournal(
            world_dir,
            world_id="world-pipeline",
            module_name="module-a",
            owner_token="pipeline-owner",
        )
        turn_id = journal.begin(kind="action", player_input="检查书桌")
        outcome = {
            "call_id": "call-audit",
            "request_id": "request-audit",
            "world_id": "world-pipeline",
            "turn_id": turn_id,
            "step": 1,
            "name": "state_add_item",
            "status": "ok",
            "duration_ms": 1.2,
            "args_digest": "a" * 64,
            "output_digest": "b" * 64,
        }
        journal.append_tool_outcome(
            turn_id,
            outcome=outcome,
            mutation_plan={"mode": "turn_cache", "planned_mutations": []},
        )
        assert journal.read(turn_id)["tool_pipeline"]["outcomes"] == [outcome]
        completed = journal.complete(
            turn_id,
            messages=[],
            world_state={"revision": 0, "schema_version": 0},
            narrative="完成。",
            choices=[],
            diagnostics={
                "model_calls": [
                    {
                        "model": "deepseek-v4-flash",
                        "prompt_profile": "hybrid",
                        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                        "request_envelope": {
                            "provider": "openai_compatible",
                            "model": "deepseek-v4-flash",
                            "capacity": {"max_output_tokens": 4096},
                            "tool_catalog_digest": "c" * 64,
                            "sections": [{"id": "system", "digest": "d" * 64}],
                            "cache": {"stream_usage_requested": True},
                        },
                    }
                ],
                "tool_pipeline": {"version": 2, "outcomes": [outcome]},
            },
        )
        assert completed["tool_pipeline"]["outcomes"] == [outcome]

    with session_scope(url) as session:
        call = session.query(ModelCall).one()
        envelope = call.details["request_envelope"]
        assert envelope["provider"] == "openai_compatible"
        assert envelope["capacity"]["max_output_tokens"] == 4096
        assert envelope["tool_catalog_digest"] == "c" * 64
