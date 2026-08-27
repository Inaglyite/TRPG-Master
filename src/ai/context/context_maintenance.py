"""Operator-facing H2 maintenance for append-only context timelines.

The game path never calls this module.  It is used by the fixed daily service
and by an explicit local command, so reference-aware collection cannot turn
into an accidental request-time deletion path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.ai.context.context_events import ContextEventStore
from src.storage.database import AuditEvent, World, new_id, session_scope

Scope = Literal["archived", "all"]


@dataclass(frozen=True)
class ContextGcReport:
    world_id: str
    sessions: int
    events: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "world_id": self.world_id,
            "sessions": self.sessions,
            "events": self.events,
        }


def collect_context_events(
    database_url: str,
    *,
    scope: Scope = "archived",
) -> list[ContextGcReport]:
    """Collect only already-closed, unreferenced context sessions.

    ``archived`` is the scheduled default: live worlds retain every closed
    epoch until an operator intentionally expands scope.  ``all`` is still
    reference-aware and only removes sessions that have no descendant/save/
    turn references.  Every completed maintenance run writes metadata-only
    audit records; model payloads never leave ``ContextEventStore``.
    """
    if scope not in {"archived", "all"}:
        raise ValueError("context GC scope 必须是 archived 或 all")
    with session_scope(database_url) as session:
        query = session.query(World.id)
        if scope == "archived":
            query = query.filter_by(status="archived")
        world_ids = [str(row[0]) for row in query.order_by(World.id).all()]

    store = ContextEventStore(database_url)
    reports: list[ContextGcReport] = []
    for world_id in world_ids:
        result = store.reference_aware_gc(world_id)
        report = ContextGcReport(
            world_id=world_id,
            sessions=int(result.get("sessions") or 0),
            events=int(result.get("events") or 0),
        )
        reports.append(report)
        with session_scope(database_url) as session:
            # The world may be physically deleted by a concurrent operator
            # after GC has finished.  In that case omit its FK-bound audit row
            # rather than retrying or fabricating a stale reference.
            if session.get(World, world_id) is None:
                continue
            session.add(
                AuditEvent(
                    id=new_id("audit"),
                    event_type="context_event_gc",
                    world_id=world_id,
                    success=True,
                    details={"scope": scope, "sessions": report.sessions, "events": report.events},
                )
            )
    return reports
