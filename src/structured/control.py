"""人类显式接管、归还与重试；不授予任何新的主持权限。"""

from sqlalchemy import select

from src.storage.database import PlayerRequest, World, utcnow

from .domains import KEEPER, CommandContext, CommandResult, EventSpec
from .errors import StructuredError
from .principal import current_control, resolve_keeper_principal, take_control


def cmd_control_keeper(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    if ctx.principal.kind != "keeper":
        raise StructuredError("keeper_required", "控制权只能由获授权的人类主持操作。")
    keeper = resolve_keeper_principal(ctx.session, ctx.world_id, ctx.principal.user_id)
    action = str(payload.get("action") or "")
    control = current_control(ctx.session, ctx.world_id)
    events = []
    if action == "take":
        control = take_control(ctx.session, ctx.world_id, keeper)
    elif action in {"release", "retry"}:
        if control.controller_kind == "human" and control.controller_id != keeper.user_id:
            raise StructuredError("controller_epoch_stale", "请先接管，才能归还控制权。")
        world = ctx.session.get(World, ctx.world_id)
        if (world.metadata_json or {}).get("keeper_mode") not in {"assisted", "agent"}:
            raise StructuredError("invalid_action", "本场为人类主持，不能交给 AI。")
        if action == "retry":
            request_id = str(payload.get("request_id") or "")
            request = ctx.session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == ctx.world_id,
                    PlayerRequest.request_id == request_id,
                )
            ).scalar_one_or_none()
            if (
                request is None
                or request.request_type == "keeper_draft"
                or request.status not in {"paused", "failed"}
            ):
                raise StructuredError("invalid_action", "只能重试本场已暂停或失败的玩家请求。")
            request.status, request.detail, request.updated_at = "queued", "", utcnow()
            events.append(
                EventSpec(
                    "action_status",
                    {"request_id": request_id, "status": "queued"},
                    {"kind": "investigators", "investigator_ids": [request.investigator_id]},
                )
            )
        control.controller_kind, control.controller_id = "none", ""
        control.epoch = int(control.epoch) + 1
    else:
        raise StructuredError("invalid_action", "控制权操作只支持 take/release/retry。")
    events.append(
        EventSpec(
            "keeper_control",
            {
                "controller_epoch": int(control.epoch),
                "controller": {
                    "kind": control.controller_kind,
                    "id": control.controller_id or "unassigned",
                },
                "reason": "human_takeover" if action == "take" else "returned_to_ai",
            },
            dict(KEEPER),
        )
    )
    return CommandResult(
        result={"status": "success", "action": action}, events=events, bump_revision=False
    )
