"""Deterministic construction of one provider request and its authority evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import config
from .context_capacity import CapacityDiagnostic
from .context_capacity import evaluate as evaluate_capacity
from .lorebook import estimate_text_tokens
from .persistence import normalize_tool_message_history
from .tool_pipeline import ContextSection, RequestEnvelope
from .tool_policy import MODEL_CALLER, ToolRequestSnapshot, payload_digest
from .tools import model_tools_for, tool_catalog_for_names


def _apply_skill_tool_policy(
    request_tools: list[dict],
    *,
    required: frozenset[str],
    allowed: frozenset[str] | None,
    skill_allowlist: tuple[tuple[str, str], ...],
) -> list[dict]:
    """H3.1：把激活 Skill 的 required/allowed_tools 声明落到本次请求目录。

    required 显式声明优先于 role 默认排除（缺什么并什么）；allowed 非空时
    把目录裁到声明并集。声明合法性已在 catalog 加载期 fail-closed 校验，
    这里只按名字集合做确定性投影。
    """
    present = {str((tool.get("function") or {}).get("name") or "") for tool in request_tools}
    result = list(request_tools)
    missing = sorted(name for name in required if name not in present)
    if missing:
        result.extend(tool_catalog_for_names(missing, skill_allowlist=skill_allowlist))
    if allowed is not None:
        # required 永远不受 allowed 上限裁剪（在这里并集，而不是依赖调用方
        # 预先合并，投影语义单点收口）。
        ceiling = allowed | required
        result = [
            tool
            for tool in result
            if str((tool.get("function") or {}).get("name") or "") in ceiling
        ]
    return result


@dataclass(frozen=True)
class StreamPolicy:
    dynamic_tools: bool
    stream_usage: bool
    prompt_profile: str
    thinking_type: str | None


@dataclass(frozen=True)
class PreparedModelRequest:
    """Provider arguments plus diagnostics frozen before a model call starts."""

    messages: list[dict]
    request_role: str
    request_tools: list[dict]
    request_snapshot: ToolRequestSnapshot
    context_sections: dict[str, dict[str, int]]
    envelope: RequestEnvelope
    request_envelope: dict[str, object]
    provider_kwargs: dict[str, Any]
    system_chars: int
    tool_schema_chars: int
    capacity: CapacityDiagnostic


def prepare_model_request(
    host: Any,
    model: str,
    *,
    policy: StreamPolicy,
    system_overlay: str | None,
    system_prompt_override: str | None,
    enable_tools: bool,
    temperature: float,
    messages_override: list[dict] | None,
    consume_request_step: bool = True,
) -> PreparedModelRequest:
    """Normalize messages and freeze the exact catalog visible to this request."""
    if messages_override is None:
        host.messages = normalize_tool_message_history(host.messages)
        messages = host.messages
    else:
        messages = normalize_tool_message_history([dict(message) for message in messages_override])
    if system_prompt_override or system_overlay:
        messages = [dict(message) for message in messages]
        if system_prompt_override:
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = system_prompt_override
            else:
                messages.insert(0, {"role": "system", "content": system_prompt_override})
        if system_overlay and messages and messages[0].get("role") == "system":
            messages[0]["content"] = f"{messages[0].get('content', '')}\n\n---\n\n{system_overlay}"
        elif system_overlay:
            messages.insert(0, {"role": "system", "content": system_overlay})

    request_role = "combat" if system_overlay else "story"
    skill_allowlist: tuple[tuple[str, str], ...] = ()
    required_tools: frozenset[str] = frozenset()
    allowed_tools: frozenset[str] | None = None
    if enable_tools:
        try:
            from .skill_activation import loadable_skill_allowlist, request_tool_policy

            skill_allowlist = loadable_skill_allowlist(host)
            required_tools, allowed_tools = request_tool_policy(host)
        except Exception:
            # 冻结集合取不到时宁可空集（loader fail-closed），不阻断请求构造。
            skill_allowlist = ()
            required_tools, allowed_tools = frozenset(), None
    request_tools = (
        model_tools_for(request_role, skill_allowlist=skill_allowlist) if enable_tools else []
    )
    if enable_tools and (required_tools or allowed_tools is not None):
        request_tools = _apply_skill_tool_policy(
            request_tools,
            required=required_tools,
            allowed=allowed_tools,
            skill_allowlist=skill_allowlist,
        )
    request_step = int(getattr(host, "_tool_request_step", 0)) + 1
    if consume_request_step:
        host._tool_request_step = request_step
    runtime_context = getattr(host, "context", None)
    world_id = str(getattr(runtime_context, "world_id", "") or "")
    turn_id = getattr(host, "active_turn_id", None) or getattr(host, "_active_turn_id", None)
    request_snapshot = ToolRequestSnapshot.create(
        step=request_step,
        profile=f"{request_role}:{policy.prompt_profile}",
        caller=MODEL_CALLER,
        tools=request_tools,
        world_id=world_id,
        turn_id=str(turn_id) if turn_id else None,
        skill_allowlist=skill_allowlist,
    )

    role_chars: dict[str, int] = {}
    role_tokens: dict[str, int] = {}
    for message in messages:
        role = str(message.get("role") or "unknown")
        content = str(message.get("content") or "")
        role_chars[role] = role_chars.get(role, 0) + len(content)
        role_tokens[role] = role_tokens.get(role, 0) + estimate_text_tokens(content)
    tool_schema_json = json.dumps(request_tools, ensure_ascii=False, separators=(",", ":"))
    system_chars = role_chars.get("system", 0)
    tool_schema_chars = len(tool_schema_json)
    system_content = "\n\n".join(
        str(message.get("content") or "") for message in messages if message.get("role") == "system"
    )
    history_content = "\n\n".join(
        str(message.get("content") or "") for message in messages if message.get("role") != "system"
    )
    sections = (
        ContextSection(
            section_id="system",
            audience="model_private",
            priority=100,
            content=system_content,
            source="system_prompt",
            estimated_tokens=role_tokens.get("system", 0),
        ),
        ContextSection(
            section_id="history",
            audience="model_private",
            priority=60,
            content=history_content,
            source="message_history",
            estimated_tokens=sum(value for role, value in role_tokens.items() if role != "system"),
        ),
        ContextSection(
            section_id="tool_schema",
            audience="model_private",
            priority=90,
            content=tool_schema_json,
            source="tool_catalog",
            estimated_tokens=estimate_text_tokens(tool_schema_json),
        ),
    )
    context_sections = {
        section.section_id: {
            "chars": len(section.content),
            "estimated_tokens": section.estimated_tokens,
        }
        for section in sections
    }
    # Estimate the same shape sent to an OpenAI-compatible provider, not just
    # message ``content``.  Assistant tool-call arguments and JSON framing can
    # otherwise account for a material part of a long turn.  The small
    # per-message margin makes this deliberately conservative without claiming
    # tokenizer-perfect accounting for every gateway.
    provider_wire = json.dumps(
        {"messages": messages, "tools": request_tools if enable_tools else []},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    estimated_input_tokens = estimate_text_tokens(provider_wire) + max(2, 4 * len(messages))
    max_output_tokens = config.max_output_tokens()
    capacity = evaluate_capacity(
        estimated_input_tokens,
        max_output_tokens=max_output_tokens,
    )
    envelope = RequestEnvelope(
        request_id=request_snapshot.request_id,
        world_id=world_id,
        turn_id=turn_id,
        step=request_snapshot.step,
        profile=request_snapshot.profile,
        caller=MODEL_CALLER,
        provider="openai_compatible",
        model=model,
        max_output_tokens=max_output_tokens,
        sections=sections,
        allowed_tool_names=request_snapshot.allowed_tool_names,
        tool_catalog_digest=request_snapshot.tool_catalog_digest,
        message_digest=payload_digest(messages),
        cache_metadata={"stream_usage_requested": bool(policy.stream_usage)},
        capacity_metadata=capacity.to_dict(),
    )
    # H0's digest-only shape remains for existing diagnostics and its golden
    # fixture.  The typed envelope above is the H1 source of truth.
    request_envelope = envelope.audit_dict() | {
        "context_section_digests": {
            "system": payload_digest(
                [message for message in messages if message.get("role") == "system"]
            ),
            "history": payload_digest(
                [message for message in messages if message.get("role") != "system"]
            ),
            "tools": request_snapshot.tool_catalog_digest,
        },
    }
    provider_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "stream": True,
    }
    if policy.thinking_type:
        from .provider_adapter import apply_reasoning_passback, reasoning_passback_required

        if reasoning_passback_required():
            # DeepSeek thinking 的 reasoning passback 只写 provider wire 副本；
            # host.messages（持久面/公开面）永远不携带 reasoning_content。
            provider_kwargs["messages"] = apply_reasoning_passback(host, messages)
    if enable_tools:
        provider_kwargs.update(tools=request_tools, tool_choice="auto")
    if policy.stream_usage:
        provider_kwargs["stream_options"] = {"include_usage": True}
    if policy.thinking_type:
        provider_kwargs["extra_body"] = {"thinking": {"type": policy.thinking_type}}
    return PreparedModelRequest(
        messages=messages,
        request_role=request_role,
        request_tools=request_tools,
        request_snapshot=request_snapshot,
        context_sections=context_sections,
        envelope=envelope,
        request_envelope=request_envelope,
        provider_kwargs=provider_kwargs,
        system_chars=system_chars,
        tool_schema_chars=tool_schema_chars,
        capacity=capacity,
    )
