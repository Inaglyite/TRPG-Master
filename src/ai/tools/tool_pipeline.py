"""Typed request and tool execution pipeline for one authoritative turn.

This module deliberately *wraps* the existing domain handlers.  Rules still
live in :mod:`src.ai.tools.registry` and mutations still stay inside ``WorldStore``'s
``turn_cache`` until ``DatabaseTurnJournal.complete`` atomically commits the
turn.  The pipeline owns only the cross-cutting concerns that previously sat
in the graph loop: request provenance, policy enforcement, idempotency,
cancellation, bounded output handling and audit-safe outcomes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from src.ai.tools.registry import MODEL_TOOL_NAMES, TOOL_SCHEMA_BY_NAME
from src.ai.tools.tool_policy import (
    REQUEST_METADATA_KEY,
    ToolPolicyError,
    ToolRequestSnapshot,
    authorize_model_tool_call,
    denied_tool_result,
    payload_digest,
    schemas_for_catalog,
)
from src.ai.tools.tool_request_authority import execution_snapshot, issued_model_request

ToolVisibility = Literal["model", "engine_internal"]
ToolMutationMode = Literal["read_only", "turn_cache"]
ToolOutcomeStatus = Literal[
    "ok", "reused", "denied", "cancelled", "timeout", "error", "invalid_output"
]


@dataclass(frozen=True)
class ContextSection:
    """One typed model-context partition.

    ``content`` stays in memory for request construction; durable diagnostics
    contain only :meth:`audit_dict`, never a second copy of prompt text.
    """

    section_id: str
    audience: str
    priority: int
    content: str
    source: str
    estimated_tokens: int

    @property
    def digest(self) -> str:
        return payload_digest(self.content)

    def audit_dict(self) -> dict[str, object]:
        return {
            "id": self.section_id,
            "audience": self.audience,
            "priority": self.priority,
            "source": self.source,
            "chars": len(self.content),
            "estimated_tokens": self.estimated_tokens,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class RequestEnvelope:
    """Frozen authority and capacity evidence for one provider request."""

    request_id: str
    world_id: str
    turn_id: str | None
    step: int
    profile: str
    caller: str
    provider: str
    model: str
    max_output_tokens: int
    sections: tuple[ContextSection, ...]
    allowed_tool_names: tuple[str, ...]
    tool_catalog_digest: str
    message_digest: str
    cache_metadata: dict[str, object] = field(default_factory=dict)
    # H2 metadata-only capacity evidence.  It is intentionally computed from
    # the provider wire payload and contains no prompt/body text.
    capacity_metadata: dict[str, object] = field(default_factory=dict)

    def audit_dict(self) -> dict[str, object]:
        """Return stable, non-secret metadata suitable for ``ModelCall.details``."""
        return {
            "version": 2,
            "request_id": self.request_id,
            "world_id": self.world_id,
            "turn_id": self.turn_id,
            "step": self.step,
            "profile": self.profile,
            "caller": self.caller,
            "provider": self.provider,
            "model": self.model,
            "capacity": {
                "max_output_tokens": self.max_output_tokens,
                **dict(self.capacity_metadata),
            },
            "allowed_tool_names": list(self.allowed_tool_names),
            "tool_catalog_digest": self.tool_catalog_digest,
            "message_digest": self.message_digest,
            "sections": [section.audit_dict() for section in self.sections],
            "cache": dict(self.cache_metadata),
        }


@dataclass(frozen=True)
class ToolDescriptor:
    """Server-owned behavior contract for one registered tool."""

    name: str
    input_schema: dict[str, Any]
    visibility: ToolVisibility
    mutation_mode: ToolMutationMode
    idempotency_scope: Literal["call", "semantic_turn"]
    timeout_ms: int
    max_output_chars: int = 65_536

    def audit_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "visibility": self.visibility,
            "mutation_mode": self.mutation_mode,
            "idempotency_scope": self.idempotency_scope,
            "timeout_ms": self.timeout_ms,
            "input_schema_digest": payload_digest(self.input_schema),
        }


@dataclass(frozen=True)
class ToolCall:
    """A model-proposed call after request-scoped authorization succeeds."""

    call_id: str
    request_id: str
    world_id: str
    turn_id: str | None
    step: int
    caller: str
    descriptor: ToolDescriptor
    args: dict[str, Any]

    @property
    def semantic_key(self) -> str:
        payload = {
            "world_id": self.world_id,
            "turn_id": self.turn_id,
            "name": self.descriptor.name,
            "args": self.args,
        }
        return payload_digest(payload)

    def audit_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "request_id": self.request_id,
            "world_id": self.world_id,
            "turn_id": self.turn_id,
            "step": self.step,
            "caller": self.caller,
            "name": self.descriptor.name,
            "args_digest": payload_digest(self.args),
            "semantic_key": self.semantic_key,
            "descriptor": self.descriptor.audit_dict(),
        }


@dataclass(frozen=True)
class ToolOutcome:
    """Exactly one safe outcome for every accepted or rejected tool call."""

    call_id: str
    request_id: str | None
    world_id: str
    turn_id: str | None
    step: int | None
    name: str
    status: ToolOutcomeStatus
    output: str
    duration_ms: float
    reused: bool = False
    error_code: str | None = None
    timeout_exceeded: bool = False
    args: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def audit_dict(self) -> dict[str, object]:
        """Persist only correlation and digest data; outputs may contain private rules."""
        return {
            "call_id": self.call_id,
            "request_id": self.request_id,
            "world_id": self.world_id,
            "turn_id": self.turn_id,
            "step": self.step,
            "name": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 3),
            "reused": self.reused,
            "error_code": self.error_code,
            "timeout_exceeded": self.timeout_exceeded,
            "args_digest": payload_digest(self.args),
            "output_digest": payload_digest(self.output),
        }

    def executed_tool_dict(self) -> dict[str, object]:
        """Compatibility shape retained in completed turn records."""
        return {
            "name": self.name,
            "args": dict(self.args),
            "output": self.output,
            "call_id": self.call_id,
            "request_id": self.request_id,
            "step": self.step,
            "status": self.status,
            "reused": self.reused,
            "timeout_exceeded": self.timeout_exceeded,
            **({"policy_denied": self.error_code} if self.status == "denied" else {}),
        }


@dataclass
class MutationPlan:
    """Declarative record of mutations expected to commit with the current turn."""

    world_id: str
    turn_id: str | None
    calls: list[dict[str, object]] = field(default_factory=list)

    def add(self, call: ToolCall) -> None:
        if call.descriptor.mutation_mode != "turn_cache":
            return
        self.calls.append(
            {
                "call_id": call.call_id,
                "name": call.descriptor.name,
                "semantic_key": call.semantic_key,
            }
        )

    def audit_dict(self) -> dict[str, object]:
        return {"mode": "turn_cache", "planned_mutations": list(self.calls)}


@dataclass
class ToolUnitOfWork:
    """Turn-scoped execution unit; external effects are intentionally absent.

    Existing model tools are either read-only or mutate ``WorldStore.turn_cache``.
    New cross-medium effects must use an explicit post-commit outbox rather than
    being added to this class as an untracked side effect.
    """

    world_id: str
    turn_id: str | None
    plan: MutationPlan


@dataclass
class ToolExecutionLedger:
    """Idempotency cache retained for the active turn and mirrored to its journal."""

    by_call_id: dict[str, ToolOutcome] = field(default_factory=dict)
    by_semantic_key: dict[str, ToolOutcome] = field(default_factory=dict)
    outcomes: list[ToolOutcome] = field(default_factory=list)

    def find(self, call: ToolCall) -> ToolOutcome | None:
        outcome = self.by_call_id.get(call.call_id)
        if outcome is not None:
            return outcome
        if call.descriptor.idempotency_scope == "semantic_turn":
            return self.by_semantic_key.get(call.semantic_key)
        return None

    def remember(self, call: ToolCall, outcome: ToolOutcome) -> None:
        self.by_call_id[call.call_id] = outcome
        if call.descriptor.idempotency_scope == "semantic_turn":
            self.by_semantic_key[call.semantic_key] = outcome
        self.outcomes.append(outcome)


def record_engine_tool_outcome(engine: Any, outcome: dict, plan: dict) -> None:
    """Persist a redacted outcome without growing ``GameEngine`` plumbing."""
    callback = getattr(engine, "record_tool_pipeline_outcome", None)
    if callback:
        callback(outcome, plan)
        return
    audit = getattr(engine, "_tool_pipeline_audit", None)
    if not isinstance(audit, list):
        audit = []
        engine._tool_pipeline_audit = audit
    audit.append(dict(outcome))
    journal = getattr(engine, "turn_journal", None)
    turn_id = getattr(engine, "_active_turn_id", None)
    append = getattr(journal, "append_tool_outcome", None)
    if append and turn_id:
        append(turn_id, outcome=outcome, mutation_plan=plan)


def record_engine_tool_shadow(engine: Any, comparison: dict) -> None:
    """Record non-executing rollout evidence with active-turn diagnostics."""
    callback = getattr(engine, "record_tool_pipeline_shadow", None)
    if callback:
        callback(comparison)
        return
    if not isinstance(comparison, dict):
        return
    shadow = getattr(engine, "_tool_pipeline_shadow", None)
    if not isinstance(shadow, list):
        shadow = []
        engine._tool_pipeline_shadow = shadow
    shadow.append(dict(comparison))


_SEMANTIC_TURN_TOOLS = frozenset(
    {
        "state_add_clue",
        "state_add_item",
        "state_remove_item",
        "link_clues",
        "npc_reveal",
        "use_item",
        "sanity_event",
        "sanity_loss",
        "sanity_restore",
        "psychoanalysis",
        "reality_check",
        "apply_damage",
        "apply_heal",
        "combat_start",
        "combat_action",
        "combat_end",
        "set_psychological_trait",
        "end_game",
        "skill_check",
        "attribute_check",
        "luck_check",
        "sanity_check",
    }
)
_TURN_CACHE_TOOLS = frozenset(
    {
        "state_add_clue",
        "state_add_item",
        "state_remove_item",
        "link_clues",
        "npc_reveal",
        "use_item",
        "sanity_event",
        "sanity_loss",
        "sanity_restore",
        "psychoanalysis",
        "reality_check",
        "apply_damage",
        "apply_heal",
        "combat_start",
        "combat_action",
        "combat_end",
        "set_psychological_trait",
        "end_game",
    }
)


def tool_descriptor_for(name: str, *, timeout_ms: int) -> ToolDescriptor:
    if name not in MODEL_TOOL_NAMES:
        raise ToolPolicyError("model_tool_forbidden", "该工具不能由模型调用")
    schema = TOOL_SCHEMA_BY_NAME.get(name)
    if not isinstance(schema, dict):
        raise ToolPolicyError("unknown_tool", "工具不在当前服务端目录中")
    return ToolDescriptor(
        name=name,
        input_schema=dict(schema),
        visibility="model",
        mutation_mode="turn_cache" if name in _TURN_CACHE_TOOLS else "read_only",
        idempotency_scope="semantic_turn" if name in _SEMANTIC_TURN_TOOLS else "call",
        timeout_ms=max(1, int(timeout_ms)),
    )


class ToolPipeline:
    """Single V2 execution path for structured and DSML model tool calls."""

    def __init__(self, engine: Any, *, timeout_ms: int) -> None:
        self.engine = engine
        self.timeout_ms = max(1, int(timeout_ms))
        world_id = str(getattr(getattr(engine, "context", None), "world_id", "") or "")
        turn_id = getattr(engine, "active_turn_id", None) or getattr(
            engine, "_active_turn_id", None
        )
        self.unit_of_work = ToolUnitOfWork(
            world_id=world_id,
            turn_id=turn_id,
            plan=MutationPlan(world_id=world_id, turn_id=turn_id),
        )
        ledger = getattr(engine, "_tool_pipeline_ledger", None)
        if not isinstance(ledger, ToolExecutionLedger):
            ledger = ToolExecutionLedger()
            engine._tool_pipeline_ledger = ledger
        self.ledger = ledger

    def _outcome(
        self,
        *,
        call_id: str,
        name: str,
        status: ToolOutcomeStatus,
        output: str,
        started_at: float,
        snapshot: ToolRequestSnapshot | None = None,
        args: dict[str, Any] | None = None,
        reused: bool = False,
        error_code: str | None = None,
        timeout_exceeded: bool = False,
    ) -> ToolOutcome:
        return ToolOutcome(
            call_id=call_id,
            request_id=snapshot.request_id if snapshot else None,
            world_id=self.unit_of_work.world_id,
            turn_id=self.unit_of_work.turn_id,
            step=snapshot.step if snapshot else None,
            name=name,
            status=status,
            output=output,
            duration_ms=(time.monotonic() - started_at) * 1000,
            reused=reused,
            error_code=error_code,
            timeout_exceeded=timeout_exceeded,
            args=dict(args or {}),
        )

    @staticmethod
    def _safe_error(code: str) -> str:
        return json.dumps(
            {"ok": False, "error": "tool_pipeline", "reason": code},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _validate_output(output: object, descriptor: ToolDescriptor) -> str:
        if not isinstance(output, str):
            raise ToolPolicyError("invalid_tool_output", "工具返回值不是文本")
        if len(output) > descriptor.max_output_chars:
            raise ToolPolicyError("tool_output_too_large", "工具返回结果过大")
        return output

    def _record_outcome(
        self, outcome: ToolOutcome, plan: MutationPlan | None = None
    ) -> ToolOutcome:
        record_engine_tool_outcome(
            self.engine,
            outcome.audit_dict(),
            (plan or self.unit_of_work.plan).audit_dict(),
        )
        return outcome

    def _remember(self, call: ToolCall, outcome: ToolOutcome) -> ToolOutcome:
        self.ledger.remember(call, outcome)
        return self._record_outcome(outcome)

    def execute(self, raw_call: dict[str, Any], *, player_action: str = "") -> ToolOutcome:
        """Authorize and execute one call, returning one result for every input."""
        started_at = time.monotonic()
        function = raw_call.get("function") if isinstance(raw_call, dict) else {}
        function = function if isinstance(function, dict) else {}
        raw_name = str(function.get("name") or "unknown")
        call_id = str(raw_call.get("id") or "") if isinstance(raw_call, dict) else ""
        if not call_id:
            call_id = f"invalid:{payload_digest(raw_call)[:20]}"
        try:
            snapshot = ToolRequestSnapshot.from_dict(raw_call.get(REQUEST_METADATA_KEY))
            issued = issued_model_request(self.engine, snapshot)
            ordered_catalog = issued.catalog_copy()
            snapshot, name, args = authorize_model_tool_call(
                raw_call,
                tool_schemas=schemas_for_catalog(ordered_catalog),
                ordered_catalog=ordered_catalog,
                model_allowed_tool_names={
                    str(tool.get("function", {}).get("name") or "") for tool in ordered_catalog
                },
            )
            descriptor = tool_descriptor_for(name, timeout_ms=self.timeout_ms)
            call = ToolCall(
                call_id=call_id,
                request_id=snapshot.request_id,
                world_id=self.unit_of_work.world_id,
                turn_id=self.unit_of_work.turn_id,
                step=snapshot.step,
                caller=snapshot.caller,
                descriptor=descriptor,
                args=args,
            )
        except ToolPolicyError as exc:
            from src.gameplay.turn_performance import increment_counter

            increment_counter(self.engine, "model_tool_rejected_count")
            return self._record_outcome(
                self._outcome(
                    call_id=call_id,
                    name=raw_name,
                    status="denied",
                    output=denied_tool_result(exc),
                    started_at=started_at,
                    error_code=exc.code,
                )
            )

        existing = self.ledger.find(call)
        if existing is not None:
            return self._remember(
                call,
                self._outcome(
                    call_id=call_id,
                    name=call.descriptor.name,
                    status="reused",
                    output=existing.output,
                    started_at=started_at,
                    snapshot=snapshot,
                    args=call.args,
                    reused=True,
                ),
            )

        cancellation = getattr(self.engine, "raise_if_turn_cancelled", None)
        if cancellation:
            # Cancellation must escape to ``GameEngine.handle_action`` so the
            # surrounding ``turn_cache`` discards any buffered mutation.  A
            # synthetic tool error here would let the graph finalize a turn
            # after its player connection has already departed.
            cancellation()
        # Python cannot safely kill arbitrary synchronous domain handlers.  A
        # deadline is therefore checked before a side-effecting handler starts;
        # once it begins, its turn-cache mutation must run to a coherent result
        # or be discarded by the outer cancelled/failed turn unit of work.
        deadline = started_at + call.descriptor.timeout_ms / 1000
        if time.monotonic() >= deadline:
            return self._remember(
                call,
                self._outcome(
                    call_id=call.call_id,
                    name=call.descriptor.name,
                    status="timeout",
                    output=self._safe_error("deadline_before_execution"),
                    started_at=started_at,
                    snapshot=snapshot,
                    args=call.args,
                    error_code="deadline_before_execution",
                ),
            )

        self.unit_of_work.plan.add(call)
        try:
            with execution_snapshot(self.engine, snapshot):
                execute_model_tool = getattr(self.engine, "_execute_model_tool", None)
                if execute_model_tool:
                    output = execute_model_tool(
                        call.descriptor.name,
                        call.args,
                        player_action=player_action,
                    )
                else:
                    output = self.engine._execute_tool(call.descriptor.name, call.args)
            output = self._validate_output(output, call.descriptor)
        except ToolPolicyError as exc:
            return self._remember(
                call,
                self._outcome(
                    call_id=call.call_id,
                    name=call.descriptor.name,
                    status="invalid_output",
                    output=self._safe_error(exc.code),
                    started_at=started_at,
                    snapshot=snapshot,
                    args=call.args,
                    error_code=exc.code,
                ),
            )
        except Exception as exc:
            return self._remember(
                call,
                self._outcome(
                    call_id=call.call_id,
                    name=call.descriptor.name,
                    status="error",
                    output=self._safe_error("handler_error"),
                    started_at=started_at,
                    snapshot=snapshot,
                    args=call.args,
                    error_code=type(exc).__name__,
                ),
            )

        timeout_exceeded = time.monotonic() >= deadline
        outcome = self._outcome(
            call_id=call.call_id,
            name=call.descriptor.name,
            status="ok",
            output=output,
            started_at=started_at,
            snapshot=snapshot,
            args=call.args,
            timeout_exceeded=timeout_exceeded,
        )
        # Let the outer action scope discard buffered writes if a disconnect
        # happened while a handler was running.  Do not turn that result into a
        # retryable tool error after a mutation may already be buffered.
        if cancellation:
            cancellation()
        return self._remember(call, outcome)

    def shadow(self, raw_call: dict[str, Any]) -> dict[str, object]:
        """Validate a call without executing it for old-path rollout comparison."""
        started_at = time.monotonic()
        outcome = self.execute_preflight(raw_call)
        return {
            "mode": "shadow",
            "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            **outcome,
        }

    def execute_preflight(self, raw_call: dict[str, Any]) -> dict[str, object]:
        function = raw_call.get("function") if isinstance(raw_call, dict) else {}
        function = function if isinstance(function, dict) else {}
        try:
            snapshot = ToolRequestSnapshot.from_dict(raw_call.get(REQUEST_METADATA_KEY))
            issued = issued_model_request(self.engine, snapshot)
            ordered_catalog = issued.catalog_copy()
            snapshot, name, args = authorize_model_tool_call(
                raw_call,
                tool_schemas=schemas_for_catalog(ordered_catalog),
                ordered_catalog=ordered_catalog,
                model_allowed_tool_names={
                    str(tool.get("function", {}).get("name") or "") for tool in ordered_catalog
                },
            )
            descriptor = tool_descriptor_for(name, timeout_ms=self.timeout_ms)
            return {
                "would_execute": True,
                "call_id": str(raw_call.get("id") or ""),
                "request_id": snapshot.request_id,
                "step": snapshot.step,
                "name": name,
                "args_digest": payload_digest(args),
                "descriptor": descriptor.audit_dict(),
            }
        except ToolPolicyError as exc:
            return {
                "would_execute": False,
                "call_id": str(raw_call.get("id") or ""),
                "name": str(function.get("name") or "unknown"),
                "reason": exc.code,
            }
