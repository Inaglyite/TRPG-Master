"""Immutable branch history, separated from live event delivery and game state."""

from __future__ import annotations

import copy

from sqlalchemy import select

from .history_archive_capture import archive_entry_visible
from .ids import new_row_id
from .message_history import PAGE_SIZE


def source_archive_entries(session, world_id: str) -> list[dict]:
    """Trusted branch-copy input; not a client-facing unfiltered reader."""
    from src.storage.database import BranchHistoryEntry

    return [
        {
            "source_key": row.source_key,
            "message": copy.deepcopy(row.message),
            "audience": copy.deepcopy(row.audience),
            "submitted_by": row.submitted_by,
        }
        for row in session.execute(
            select(BranchHistoryEntry)
            .where(BranchHistoryEntry.world_id == world_id)
            .order_by(BranchHistoryEntry.ordinal)
        ).scalars()
    ]


def store_archive(session, world_id: str, entries: list[dict]) -> None:
    """Called only within the new branch's creation transaction."""
    from src.storage.database import BranchHistoryEntry

    seen = set()
    ordinal = 0
    for entry in entries:
        source_key = entry["source_key"]
        if source_key in seen:
            continue
        seen.add(source_key)
        ordinal += 1
        session.add(
            BranchHistoryEntry(
                id=new_row_id("archive"),
                world_id=world_id,
                source_key=source_key,
                ordinal=ordinal,
                message=copy.deepcopy(entry["message"]),
                audience=copy.deepcopy(entry["audience"]),
                submitted_by=entry.get("submitted_by"),
            )
        )


def visible_archive_history(session, world_id: str, principal, *, before_sequence=None) -> dict:
    """Read at most one authorized page; the cursor belongs to this archive only."""
    from src.storage.database import BranchHistoryEntry

    found = []
    before = before_sequence
    while len(found) <= PAGE_SIZE:
        query = select(BranchHistoryEntry).where(BranchHistoryEntry.world_id == world_id)
        if before is not None:
            query = query.where(BranchHistoryEntry.ordinal < before)
        rows = (
            session.execute(query.order_by(BranchHistoryEntry.ordinal.desc()).limit(100))
            .scalars()
            .all()
        )
        if not rows:
            break
        for row in rows:
            entry = {
                "message": row.message,
                "audience": row.audience,
                "submitted_by": row.submitted_by,
            }
            if not archive_entry_visible(entry, principal):
                continue
            message = copy.deepcopy(row.message)
            message["message_id"] = f"archive:{row.id}"
            message["sequence"] = int(row.ordinal)
            found.append(message)
            if len(found) > PAGE_SIZE:
                break
        before = int(rows[-1].ordinal)
        if len(rows) < 100:
            break
    page = found[:PAGE_SIZE]
    return {
        "messages": list(reversed(page)),
        "next_before_sequence": page[-1]["sequence"] if len(found) > PAGE_SIZE else None,
    }
