"""Exact receipt/event membership for save timelines, independent of revisions."""

import hashlib
import json

from sqlalchemy import select

from src.storage.database import EventOutbox, GameCommand

from .errors import StructuredError


def capture_receipts(session, world_id: str, revision: int) -> dict:
    return {
        "world_id": world_id,
        "revision": revision,
        "event_signatures": {
            str(event.sequence): event_signature(event)
            for event in session.scalars(
                select(EventOutbox).where(EventOutbox.world_id == world_id)
            ).yield_per(256)
        },
        "row_ids": list(
            session.scalars(
                select(GameCommand.id).where(
                    GameCommand.world_id == world_id,
                    GameCommand.status == "committed",
                    GameCommand.error_code != "timeline_discarded",
                )
            )
        ),
    }


def receipt_membership(metadata: dict, world_id: str, revision: int) -> set[str] | None:
    cursor = metadata.get("structured_receipt_cursor")
    if cursor is None:
        return None  # Older saves use the event/revision boundary, fail closed.
    if (
        not isinstance(cursor, dict)
        or cursor.get("world_id") != world_id
        or type(cursor.get("revision")) is not int
        or cursor["revision"] != revision
        or not isinstance(cursor.get("row_ids"), list)
        or any(not isinstance(key, str) or len(key) > 48 for key in cursor["row_ids"])
    ):
        raise StructuredError("invalid_action", "存档回执游标与世界不一致，已拒绝读档。")
    return set(cursor["row_ids"])


def event_signature(event) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "sequence": event.sequence,
                "revision": event.revision,
                "type": event.event_type,
                "payload": event.payload,
                "audience": event.audience,
                "cause_id": event.cause_request_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def event_membership(metadata) -> dict | None:
    cursor = metadata.get("structured_receipt_cursor") or {}
    signatures = cursor.get("event_signatures")
    if signatures is not None and (
        not isinstance(signatures, dict)
        or any(
            not str(key).isdigit() or not isinstance(value, str) or len(value) != 64
            for key, value in signatures.items()
        )
    ):
        raise StructuredError("invalid_action", "存档事件身份无效，已拒绝读档。")
    return signatures
