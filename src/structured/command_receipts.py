"""Private complete command receipts; event replay still goes through outbox."""

import copy

from sqlalchemy import select

from src.storage.database import GameCommand, PlayerRequest


def capture_request_context(
    session, world_id: str, kind: str, payload: dict, cause_id: str
) -> dict:
    ids = {cause_id} if cause_id else set()
    if kind == "resolve_intent" and payload.get("request_id"):
        ids.add(payload["request_id"])
    if kind == "resolve_draft":
        draft = session.scalar(
            select(PlayerRequest).where(
                PlayerRequest.world_id == world_id,
                PlayerRequest.request_id == payload.get("draft_id"),
                PlayerRequest.request_type == "keeper_draft",
            )
        )
        if draft and (draft.payload or {}).get("related_request_id"):
            ids.add(draft.payload["related_request_id"])
    rows = session.scalars(
        select(PlayerRequest)
        .where(
            PlayerRequest.world_id == world_id,
            PlayerRequest.request_id.in_(ids),
            PlayerRequest.request_type == "action_request",
        )
        .order_by(PlayerRequest.created_at, PlayerRequest.id)
    ).all()
    return {
        "requests": [
            {
                "request_id": r.request_id,
                "investigator_id": r.investigator_id,
                "request_type": r.request_type,
                "status_before_command": r.status,
                "payload": copy.deepcopy(r.payload),
            }
            for r in rows
        ],
        "request_is_executed_fact": False,
        "audience": {"kind": "keeper"},
        "is_public_message": False,
    }


def core_receipt(row: GameCommand) -> dict:
    return {
        "command_id": row.command_id,
        "kind": row.kind,
        "result": row.result,
        "status": row.status,
        "revision": row.revision,
        "cause_id": row.cause_id,
        "payload": row.payload,
    }


def complete_receipt(row: GameCommand, *, include_requests: bool = True) -> dict:
    return {
        **core_receipt(row),
        "emitted_events": [event.event_json for event in row.event_receipts],
        # Every newly committed command emits at least its action_status ack.
        # Old rows have no immutable events; do not pretend to reconstruct them.
        "emitted_events_complete": bool(row.event_receipts),
        **(
            {
                "originating_request_context": row.request_receipt.context_json
                if row.request_receipt
                else None,
                "originating_request_context_complete": row.request_receipt is not None,
            }
            if include_requests
            else {}
        ),
    }
