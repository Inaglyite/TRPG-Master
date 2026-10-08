"""End a case's outstanding work in the same transaction as its rewards.

Cancelling remaining work is not a claim that previous committed effects never
happened. Keep completed receipts and do not invent dice results for pending checks.
"""

from sqlalchemy import select

from src.storage.database import CheckRequest, PlayerRequest, utcnow

from . import interactions
from .checks import decline_pending_check
from .domains import CommandContext, EventSpec

REASON = "游戏已结束，剩余事项不再执行；此前已提交的结果保留。"


def close_case_work(ctx: CommandContext) -> list[EventSpec]:
    if ctx.session is None:
        return []  # Pure domain callers have no persistent request ledger.
    session = ctx.session
    events = []
    checks = session.scalars(
        select(CheckRequest).where(
            CheckRequest.world_id == ctx.world_id, CheckRequest.status == "pending"
        )
    ).all()
    for check in checks:
        # Reuse decline's push-link cleanup and original public/private visibility.
        events.extend(
            decline_pending_check(
                session, ctx.world_id, check.check_request_id, reason=REASON
            ).events
        )
    requests = session.scalars(
        select(PlayerRequest).where(
            PlayerRequest.world_id == ctx.world_id,
            PlayerRequest.status.in_(
                ["queued", "processing", "awaiting_player", "paused", "failed"]
            ),
        )
    ).all()
    for request in requests:
        request.status = "cancelled"
        request.outcome = ""  # A compound request might already have committed effects.
        request.detail, request.updated_at = REASON, utcnow()
        request.payload = {k: v for k, v in (request.payload or {}).items() if k != "awaiting"}
        audience = (
            {"kind": "investigators", "investigator_ids": [request.investigator_id]}
            if request.investigator_id
            else {"kind": "keeper"}
        )
        events.append(
            EventSpec(
                "action_status",
                {"request_id": request.request_id, "status": "cancelled", "detail": REASON},
                audience,
            )
        )
        if request.request_type == "keeper_draft":
            events.append(
                EventSpec(
                    "keeper_draft_resolved",
                    {"draft_id": request.request_id, "decision": "rejected", "note": REASON},
                    {"kind": "keeper"},
                )
            )
    for thread in interactions.list_open_threads(session, ctx.world_id):
        interactions.close_thread(
            thread, status="cancelled", note=REASON, revision=ctx.revision + 1
        )
        events.append(EventSpec(*interactions.thread_event_with_audience(thread)))
    return events
