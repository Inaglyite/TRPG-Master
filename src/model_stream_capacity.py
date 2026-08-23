"""Capacity preflight for :mod:`src.model_streamer`.

This deliberately sits beside, rather than inside, the streaming transport:
capacity evaluation may rebuild a request and use a non-destructive context
checkpoint, but it never opens a provider stream or decides how chunks are
rendered.  Keeping that boundary explicit also keeps the streaming adapter
small enough to audit independently.
"""

from __future__ import annotations

import time
from typing import Any

from .context_capacity import STATUS_COMPACT, STATUS_IRREDUCIBLE
from .model_request import StreamPolicy, prepare_model_request
from .model_stream_diagnostics import record_model_diagnostic


def _restore_deterministic_skill_surface(streamer: Any) -> int:
    """Re-inject deterministic rules lost by an in-turn compaction.

    This lives at the capacity boundary so the rebuilt provider request sees
    the restored controls in the *same* turn.  It intentionally tolerates
    lightweight non-game streamer hosts used by maintenance paths/tests.
    """

    try:
        from .skill_activation import refresh_deterministic_skills

        return refresh_deterministic_skills(streamer.host)
    except Exception as exc:  # noqa: BLE001 - compaction must not break a turn
        streamer.log_error(f"上下文压缩后恢复确定性 Skill 失败: {type(exc).__name__}")
        return 0


def _reject_irreducible_capacity(streamer: Any, prepared: Any, policy: StreamPolicy) -> None:
    """Emit metadata-only diagnostics for a request that cannot be sent."""
    streamer.log_error("模型上下文达到硬容量上限；未发起 provider 请求")
    # 标记本回合发生了容量熔断：finalize 据此回滚未应答的追加消息，
    # 否则每次重试都会再叠加一份玩家输入与注入，让历史越来越超。
    streamer.host.__dict__["_capacity_rejected_turn"] = True
    record_model_diagnostic(
        streamer.host,
        str(prepared.provider_kwargs.get("model") or ""),
        prepared.request_role,
        "capacity_irreducible",
        time.monotonic(),
        None,
        "capacity_irreducible",
        0,
        prepared.messages,
        prepared.context_sections,
        {},
        policy,
        request_snapshot=prepared.request_snapshot.to_dict(),
        request_envelope=prepared.request_envelope,
    )
    streamer.host.cb.on_error("当前规则与历史过长，无法安全继续本轮；请稍后重试。")


def prepare_with_capacity(
    streamer: Any,
    model: str,
    *,
    policy: StreamPolicy,
    system_overlay: str | None,
    system_prompt_override: str | None,
    enable_tools: bool,
    temperature: float,
    messages_override: list[dict] | None,
    compaction_attempted: bool,
) -> tuple[Any | None, bool]:
    """Build the next request and perform one non-destructive preflight.

    Capacity is evaluated against the actual provider-wire payload frozen by
    ``prepare_model_request``.  Only normal gameplay requests may compact;
    immutable rewrite/summary overrides retain their caller-owned surface and
    instead fail closed before they can open an impossible provider request.
    """

    def prepare(*, consume_request_step: bool = False) -> Any:
        return prepare_model_request(
            streamer.host,
            model,
            policy=policy,
            system_overlay=system_overlay,
            system_prompt_override=system_prompt_override,
            enable_tools=enable_tools,
            temperature=temperature,
            messages_override=messages_override,
            consume_request_step=consume_request_step,
        )

    def finalize() -> Any | None:
        """Consume exactly one request step after all capacity work is done."""
        final = prepare(consume_request_step=True)
        if final.capacity.status == STATUS_IRREDUCIBLE:
            _reject_irreducible_capacity(streamer, final, policy)
            return None
        return final

    prepared = prepare()
    if messages_override is not None:
        final = finalize()
        return final, compaction_attempted

    capacity = prepared.capacity
    if capacity.status not in {STATUS_COMPACT, STATUS_IRREDUCIBLE} or compaction_attempted:
        return finalize(), compaction_attempted

    before = capacity
    pruned = 0
    summarized = False
    ensure_compactor = getattr(streamer.host, "_ensure_history_compactor", None)
    if callable(ensure_compactor):
        try:
            compactor = ensure_compactor()
            prune = getattr(compactor, "prune_old_tool_results", None)
            if callable(prune):
                pruned = int(prune())
            # 过期权威状态块（每条玩家行动内嵌的当轮 JSON 快照）是「条数少、
            # 单条大」历史的主要体积来源，keep-recent 按条数永远够不到它们。
            prune_authority = getattr(compactor, "prune_stale_authority_blocks", None)
            if callable(prune_authority):
                pruned += int(prune_authority())
            # 历史工具结果里的 base64 图片投递载荷同理：单条即可占满整个窗口，
            # 且不属于叙事上下文，不受 keep-recent 保护。
            prune_assets = getattr(compactor, "prune_asset_payloads", None)
            if callable(prune_assets):
                pruned += int(prune_assets())
            if pruned:
                prepared = prepare()
            # A hard estimate is not automatically irreducible: it may still
            # contain an old balanced window whose verified summary reduces
            # the next actual wire request below the hard boundary.
            if prepared.capacity.status in {STATUS_COMPACT, STATUS_IRREDUCIBLE}:
                summarized = bool(
                    compactor.summarize(
                        silent=True,
                        allow_rebase_fallback=False,
                    )
                )
                if summarized:
                    prepared = prepare()
        except Exception as exc:  # noqa: BLE001 - compaction cannot break a turn
            streamer.log_error(f"上下文预压缩失败: {type(exc).__name__}")

    # Summary replacement may have removed an engine-only deterministic Skill
    # control.  Restore it before the final capacity/request rebuild, not on
    # the next player turn.  If the restored authority itself makes the prompt
    # irreducible, ``finalize`` below safely refuses the provider call.
    if pruned or summarized:
        _restore_deterministic_skill_surface(streamer)
        prepared = prepare()

    after = prepared.capacity
    diagnostic = getattr(streamer.host, "_append_model_diagnostic", None)
    if callable(diagnostic):
        diagnostic(
            {
                "event": "context_capacity_preflight",
                "before": before.to_dict(),
                "after": after.to_dict(),
                "pruned_tool_results": pruned,
                "summarized": summarized,
            }
        )
    if after.status == STATUS_IRREDUCIBLE:
        _reject_irreducible_capacity(streamer, prepared, policy)
        return None, True
    return finalize(), True
