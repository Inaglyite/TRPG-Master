"""辅助主持草稿批准：有界批次在一个命令事务中执行，失败整体回滚。"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select

from src.storage.database import PlayerRequest, utcnow

from .domains import KEEPER, CommandContext, CommandResult, EventSpec
from .errors import StructuredError
from .validation import validate_command


def resolve_draft(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    from .service import _KIND_HANDLERS

    draft_id = str(payload.get("draft_id") or "")
    decision = str(payload.get("decision") or "")
    if decision not in {"approved", "rejected", "edited"}:
        raise StructuredError("invalid_action", "decision 只支持 approved/rejected/edited。")
    row = ctx.session.execute(
        select(PlayerRequest).where(
            PlayerRequest.world_id == ctx.world_id,
            PlayerRequest.request_id == draft_id,
            PlayerRequest.request_type == "keeper_draft",
        )
    ).scalar_one_or_none()
    if row is None:
        raise StructuredError("request_not_found", f"没有找到草稿：{draft_id}")
    if row.status != "queued":
        raise StructuredError("invalid_action", f"草稿已处理：{row.status}")
    stored = row.payload or {}
    draft = stored.get("draft") or {}
    events: list[EventSpec] = []
    results: list[dict] = []
    bump = False
    if decision == "approved":
        revision = stored.get("source_revision")
        if revision is not None and int(revision) != ctx.revision:
            raise StructuredError(
                "revision_conflict", "草稿生成后世界已变化，请重新生成草稿再批准。", retryable=True
            )
        commands = draft.get("commands") or []
        if not isinstance(commands, list) or len(commands) > 12:
            raise StructuredError("invalid_action", "草稿最多包含 12 条命令。")
        related_id = str(stored.get("related_request_id") or "")
        inner_ctx = replace(ctx, cause_id=related_id or ctx.cause_id)
        for command in commands:
            if not isinstance(command, dict):
                raise StructuredError("invalid_action", "草稿命令格式错误。")
            kind, body = str(command.get("kind") or ""), command.get("payload")
            if kind in {"resolve_draft", "control_keeper"} or kind not in _KIND_HANDLERS:
                raise StructuredError("invalid_action", f"草稿不能执行命令：{kind}")
            validate_command(kind, body)
            outcome = _KIND_HANDLERS[kind](state, body, inner_ctx)
            events.extend(outcome.events)
            bump = bump or outcome.bump_revision
            results.append({"kind": kind, "result": outcome.result})
        narration = str(draft.get("narration") or "")
        if narration:
            message = _KIND_HANDLERS["publish_message"](
                state,
                {
                    "speaker": {"kind": "keeper"},
                    "audience": {"kind": "public"},
                    "text": narration,
                },
                inner_ctx,
            )
            events.extend(message.events)
        # 明确移动请求已抵达时收尾；复合自由行动不能仅因移动完成而被关闭。
        request = (
            ctx.session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == ctx.world_id,
                    PlayerRequest.request_id == related_id,
                )
            ).scalar_one_or_none()
            if related_id
            else None
        )
        action = (request.payload or {}).get("action") or {} if request else {}
        if (
            request is not None
            and request.status in {"queued", "processing", "awaiting_player"}
            and action.get("kind") == "move"
            and action.get("destination_scene_id") == (state.get("current_scene") or {}).get("id")
        ):
            resolved = _KIND_HANDLERS["resolve_intent"](
                state,
                {
                    "request_id": related_id,
                    "resolution": "completed",
                    "outcome": "success",
                },
                inner_ctx,
            )
            events.extend(resolved.events)
            bump = bump or resolved.bump_revision
    note = str(payload.get("note") or "")[:500]
    row.status = "declined" if decision == "rejected" else "completed"
    row.detail, row.updated_at = note or f"草稿{decision}", utcnow()
    events.append(
        EventSpec(
            "keeper_draft_resolved",
            {
                "draft_id": draft_id,
                "decision": decision,
                **({"note": note} if note else {}),
            },
            dict(KEEPER),
        )
    )
    return CommandResult(
        result={
            "status": "success",
            "draft_id": draft_id,
            "decision": decision,
            "executed_commands": results,
        },
        events=events,
        bump_revision=bump,
    )
