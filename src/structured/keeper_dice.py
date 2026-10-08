"""Human-only, effect-free random rolls and bounded authorized history."""

import copy

from sqlalchemy import select

from src.storage.database import EventOutbox

from .checks import roll_free
from .domains import CommandContext, CommandResult, EventSpec
from .errors import StructuredError


def keeper_roll(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    if ctx.principal.kind != "keeper":
        raise StructuredError("keeper_required", "主持普通骰仅由人类主持主动调用。")
    if not ctx.command_id:
        raise StructuredError("invalid_action", "主持普通骰缺少命令标识。")
    spec = payload.get("spec")
    visibility = payload.get("visibility", "keeper")
    if (
        not isinstance(spec, str)
        or not isinstance(visibility, str)
        or visibility not in {"keeper", "public"}
    ):
        raise StructuredError("invalid_action", "骰式或接收范围无效。")
    result, _unused = roll_free(ctx, spec)
    receipt = {"command_id": ctx.command_id, "visibility": visibility, **result}
    return CommandResult(
        result=receipt,
        events=[EventSpec("keeper_roll_resolved", receipt, {"kind": visibility})],
        bump_revision=False,
    )


def visible_keeper_rolls(session, world_id: str, *, is_keeper: bool) -> list[dict]:
    # Read the live outbox rather than immutable command receipts: a restore
    # deletes future events even when pure rolls share the checkpoint revision.
    # Branches intentionally do not copy outbox/ordinary dice history.
    query = select(EventOutbox).where(
        EventOutbox.world_id == world_id,
        EventOutbox.event_type == "keeper_roll_resolved",
    )
    if not is_keeper:
        query = query.where(EventOutbox.audience["kind"].as_string() == "public")
    rows = session.scalars(query.order_by(EventOutbox.sequence.desc()).limit(20)).all()
    return [copy.deepcopy(row.payload) for row in reversed(rows)]
