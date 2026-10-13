"""Private complete command receipts; event replay still goes through outbox."""

from src.storage.database import GameCommand


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


def complete_receipt(row: GameCommand) -> dict:
    return {
        **core_receipt(row),
        "emitted_events": [event.event_json for event in row.event_receipts],
        # Every newly committed command emits at least its action_status ack.
        # Old rows have no immutable events; do not pretend to reconstruct them.
        "emitted_events_complete": bool(row.event_receipts),
    }
