"""H2 scheduled-management boundary for reference-aware context GC."""

from __future__ import annotations

from pathlib import Path

from src.context_events import ContextEventStore
from src.context_maintenance import collect_context_events
from src.database import (
    AuditEvent,
    Base,
    ContextSession,
    ModelContextEvent,
    World,
    get_engine,
    session_scope,
)


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'context-maintenance.db'}"


def _closed_epoch(store: ContextEventStore, world_id: str) -> None:
    store.ensure_session(world_id)
    store.begin_fresh_epoch(world_id)


def test_archived_context_gc_is_reference_aware_and_audited(tmp_path: Path) -> None:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add_all(
            [
                World(id="archived", module_name="module", status="archived"),
                World(id="active", module_name="module", status="active"),
            ]
        )
    store = ContextEventStore(url)
    _closed_epoch(store, "archived")
    _closed_epoch(store, "active")

    reports = collect_context_events(url)
    assert [report.to_dict() for report in reports] == [
        {"world_id": "archived", "sessions": 1, "events": 0}
    ]
    # The live world's old epoch is deliberately left to an explicit --all
    # operator run, even though the underlying collector would be safe.
    with session_scope(url) as session:
        assert session.query(ContextSession).filter_by(world_id="active").count() == 2
    with session_scope(url) as session:
        audit = session.query(AuditEvent).filter_by(event_type="context_event_gc").one()
        assert audit.world_id == "archived"
        assert audit.details == {"scope": "archived", "sessions": 1, "events": 0}


def test_all_scope_is_idempotent_and_keeps_referenced_sessions(tmp_path: Path) -> None:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="active", module_name="module", status="active"))
    store = ContextEventStore(url)
    first = store.ensure_session("active")
    store.begin_epoch("active")
    # A child session is an explicit reference: the old session must survive
    # even an all-world sweep.
    assert store.session_for_world("active")["parent_session_id"] == first["id"]

    reports = collect_context_events(url, scope="all")
    assert reports[0].sessions == 0
    assert reports[0].events == 0
    again = collect_context_events(url, scope="all")
    assert again[0].sessions == 0
    with session_scope(url) as session:
        assert session.query(ContextSession).filter_by(world_id="active").count() == 2


def test_physical_world_purge_cascades_private_context_events(tmp_path: Path) -> None:
    """A physical purge cannot leave model-private context rows behind.

    Normal game/archive flows are intentionally logical deletes so their
    timelines remain recoverable.  When an operator later performs the
    explicit physical World deletion, the DB FK chain must remove both the
    sessions and their private payload-bearing events in the same transaction.
    """
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="purged", module_name="module", status="archived"))
    store = ContextEventStore(url)
    active = store.ensure_session("purged")
    store.append(
        active["id"],
        event_type="assistant_message",
        payload={"role": "assistant", "content": "private keeper context"},
        source_kind="test",
    )

    with session_scope(url) as session:
        session.delete(session.get(World, "purged"))

    with session_scope(url) as session:
        assert session.query(ContextSession).filter_by(world_id="purged").count() == 0
        assert session.query(ModelContextEvent).filter_by(world_id="purged").count() == 0
