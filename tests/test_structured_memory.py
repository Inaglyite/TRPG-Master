"""H3 structured-memory invariants: candidate→fact boundary and shadow retrieval.

Covers the branch/audience/tier isolation contract from
``docs/ARCHITECTURE.md`` §7.5: cross-world, cross-user,
cross-branch and cross-tier wrong recall must be zero, and a verbatim NPC
transcript is never auto-promoted into an accepted fact.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.ai.context.structured_memory import (
    MAX_RETRIEVAL_LIMIT,
    MAX_RETRIEVAL_RESPONSE_BYTES,
    MODEL_PRIVATE_AUDIENCE,
    OWNER_AUDIENCE,
    PUBLIC_AUDIENCE,
    StructuredMemoryService,
    backfill_world_root_ids,
    fact_digest,
)
from src.storage.database import (
    MEMORY_FACT_CURRENT_INDEX,
    Base,
    MemoryFact,
    MemoryFactCandidate,
    Turn,
    User,
    World,
    WorldState,
    get_engine,
    new_id,
    session_scope,
)


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'structured-memory.db'}"


def _db(tmp_path: Path) -> str:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    return url


def _add_world(
    url: str,
    world_id: str,
    *,
    parent: str | None = None,
    source_turn_id: str | None = None,
    source_world_revision: int | None = None,
    state_revision: int = 0,
    branch_created_at: str | None = None,
    memory_cutoff_at: str | None = None,
) -> None:
    with session_scope(url) as session:
        metadata: dict = {}
        if parent:
            metadata["branch"] = {"parent_world_id": parent}
            if source_turn_id is not None:
                metadata["branch"]["source_turn_id"] = source_turn_id
            if source_world_revision is not None:
                metadata["branch"]["source_world_revision"] = source_world_revision
            # Existing branches only have ``created_at``.  Keeping the helper
            # on that legacy shape tests its supported compatibility path;
            # callers exercising new branch creation opt into the dedicated
            # ``memory_cutoff_at`` field below.
            metadata["branch"]["created_at"] = (
                branch_created_at or datetime.now(UTC).isoformat()
            )
            if memory_cutoff_at is not None:
                metadata["branch"]["memory_cutoff_at"] = memory_cutoff_at
        session.add(
            World(id=world_id, module_name="mod", metadata_json=metadata)
        )
        session.add(
            WorldState(
                world_id=world_id,
                schema_version=2,
                revision=state_revision,
                state={},
            )
        )


def _add_completed_turn(
    url: str,
    world_id: str,
    turn_id: str,
    *,
    world_revision: int | None = None,
) -> int:
    with session_scope(url) as session:
        state_row = session.get(WorldState, world_id)
        assert state_row is not None
        if world_revision is None:
            world_revision = int(state_row.revision) + 1
        state_row.revision = max(int(state_row.revision), world_revision)
        session.add(
            Turn(
                pk=new_id("turnrow"),
                id=turn_id,
                world_id=world_id,
                kind="action",
                status="completed",
                record={"world_revision": world_revision},
                messages=[],
            )
        )
    return world_revision


def _add_user(url: str, user_id: str) -> None:
    with session_scope(url) as session:
        session.add(User(id=user_id, username=user_id, password_hash="x"))


def _set_npc_revealed(url: str, world_id: str, npc_id: str, level: int) -> None:
    with session_scope(url) as session:
        row = session.get(WorldState, world_id)
        if row is None:
            row = WorldState(world_id=world_id, schema_version=2, revision=0, state={})
            session.add(row)
        state = dict(row.state or {})
        npcs = list(state.get("npcs") or [])
        npcs.append({"id": npc_id, "name": npc_id, "revealed": {"level": level, "entries": []}})
        state["npcs"] = npcs
        row.state = state


def _set_world_revision(url: str, world_id: str, revision: int) -> None:
    with session_scope(url) as session:
        row = session.get(WorldState, world_id)
        assert row is not None
        row.revision = revision


def _set_fact_decided_at(url: str, fact_id: str, decided_at: datetime) -> None:
    """Make acceptance ordering deterministic across SQLite/PostgreSQL tests."""
    with session_scope(url) as session:
        fact = session.get(MemoryFact, fact_id)
        assert fact is not None
        fact.decided_at = decided_at


def _accept(
    service: StructuredMemoryService,
    *,
    world_id: str,
    turn_id: str,
    subject_id: str = "npc-1",
    fact_type: str = "knows",
    value: object = {"fact": "a"},
    audience: str = PUBLIC_AUDIENCE,
    owner_user_id: str | None = None,
    tier: int | None = None,
    subject_kind: str = "npc",
) -> str:
    candidate_id = service.propose_candidate(
        world_id=world_id,
        source_turn_id=turn_id,
        subject_id=subject_id,
        subject_kind=subject_kind,
        fact_type=fact_type,
        value=value,
        audience=audience,
        owner_user_id=owner_user_id,
        tier=tier,
    )
    return service.accept_fact(
        candidate_id,
        source_turn_id=turn_id,
        provenance=[{"turn_id": turn_id, "tool": "test"}],
    )


# ---------------------------------------------------------------------------
# World.root_world_id backfill
# ---------------------------------------------------------------------------


def test_backfill_world_root_ids_idempotent(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_world(url, "branch-a", parent="root")
    _add_world(url, "branch-b", parent="root")
    _add_world(url, "deep", parent="branch-a")

    assert backfill_world_root_ids(url) == 4
    with session_scope(url) as session:
        roots = {w.id: w.root_world_id for w in session.query(World).all()}
    assert roots == {
        "root": "root",
        "branch-a": "root",
        "branch-b": "root",
        "deep": "root",
    }

    # Idempotent: no empty roots left, nothing to update.
    assert backfill_world_root_ids(url) == 0


# ---------------------------------------------------------------------------
# candidate → accepted fact boundary
# ---------------------------------------------------------------------------


def test_propose_accept_roundtrip(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)

    fact_id = _accept(service, world_id="root", turn_id="turn-1")

    with session_scope(url) as session:
        fact = session.get(MemoryFact, fact_id)
        assert fact is not None
        assert fact.status == "accepted"
        assert fact.root_world_id == "root"
        assert fact.digest == fact_digest("npc-1", "knows", {"fact": "a"})


def test_propose_rejects_non_completed_turn(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    with session_scope(url) as session:
        session.add(
            Turn(
                pk=new_id("turnrow"),
                id="turn-active",
                world_id="root",
                kind="action",
                status="active",
                record={},
                messages=[],
            )
        )
    service = StructuredMemoryService(url)
    with pytest.raises(ValueError, match="未完整提交"):
        service.propose_candidate(
            world_id="root",
            source_turn_id="turn-active",
            subject_id="npc-1",
            subject_kind="npc",
            fact_type="knows",
            value={"fact": "a"},
        )


def test_propose_candidate_unique_race_rereads_committed_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed concurrent winner turns the duplicate insert into a retry.

    SQLite's readers can otherwise turn a real two-writer schedule into a
    ``database is locked`` failure before its unique constraint is reached.
    This deterministic interleaving models the relevant completed schedule:
    another transaction commits a winner after our lookup but before our
    flush.  The duplicate flush is real, and the service must re-read that
    exact durable winner rather than surface an idempotency error.
    """
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)
    winner_id = service.propose_candidate(
        world_id="root",
        source_turn_id="turn-1",
        subject_id="npc-1",
        subject_kind="npc",
        fact_type="knows",
        value={"fact": "a"},
    )

    original_scalar = Session.scalar
    hide_winner_once = True

    def race_lookup(session: Session, statement: object, *args: object, **kwargs: object) -> object:
        nonlocal hide_winner_once
        result = original_scalar(session, statement, *args, **kwargs)
        if (
            hide_winner_once
            and isinstance(result, MemoryFactCandidate)
            and "memory_fact_candidates" in str(statement)
        ):
            # Present the service with the same observation it would have if
            # a second transaction committed just after this lookup.
            hide_winner_once = False
            return None
        return result

    monkeypatch.setattr(Session, "scalar", race_lookup)
    retry_id = service.propose_candidate(
        world_id="root",
        source_turn_id="turn-1",
        subject_id="npc-1",
        subject_kind="npc",
        fact_type="knows",
        value={"fact": "a"},
    )

    assert retry_id == winner_id
    with session_scope(url) as session:
        assert session.query(MemoryFactCandidate).count() == 1


def test_propose_candidate_does_not_mask_unrelated_integrity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)
    original_flush = Session.flush

    def fail_other_constraint(
        session: Session,
        *args: object,
        **kwargs: object,
    ) -> object:
        if any(isinstance(row, MemoryFactCandidate) for row in session.new):
            raise IntegrityError(
                "INSERT INTO memory_fact_candidates ...",
                {},
                Exception("FOREIGN KEY constraint failed"),
            )
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_other_constraint)
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        service.propose_candidate(
            world_id="root",
            source_turn_id="turn-1",
            subject_id="npc-1",
            subject_kind="npc",
            fact_type="knows",
            value={"fact": "a"},
        )


def test_accept_requires_matching_source_and_provenance(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)
    candidate_id = service.propose_candidate(
        world_id="root",
        source_turn_id="turn-1",
        subject_id="npc-1",
        subject_kind="npc",
        fact_type="knows",
        value={"fact": "a"},
    )

    with pytest.raises(ValueError, match="不匹配"):
        service.accept_fact(candidate_id, source_turn_id="other-turn")
    with pytest.raises(ValueError, match="provenance"):
        service.accept_fact(candidate_id, source_turn_id="turn-1", provenance=[])

    # Still proposed: nothing was accepted.
    with session_scope(url) as session:
        assert session.query(MemoryFact).count() == 0


def test_accept_same_candidate_is_retry_idempotent(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)
    candidate_id = service.propose_candidate(
        world_id="root",
        source_turn_id="turn-1",
        subject_id="npc-1",
        subject_kind="npc",
        fact_type="knows",
        value={"fact": "a"},
    )

    first = service.accept_fact(
        candidate_id,
        source_turn_id="turn-1",
        provenance=[{"turn_id": "turn-1"}],
    )
    retry = service.accept_fact(
        candidate_id,
        source_turn_id="turn-1",
        provenance=[{"turn_id": "turn-1"}],
    )
    assert retry == first
    with session_scope(url) as session:
        assert session.query(MemoryFact).count() == 1


def test_proposal_validates_json_owner_and_source_revision(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1", world_revision=1)
    service = StructuredMemoryService(url)

    with pytest.raises(ValueError, match="JSON"):
        service.propose_candidate(
            world_id="root",
            source_turn_id="turn-1",
            subject_id="npc-1",
            subject_kind="npc",
            fact_type="bad-json",
            value={"number": float("nan")},
        )
    with pytest.raises(ValueError, match="owner_user_id"):
        service.propose_candidate(
            world_id="root",
            source_turn_id="turn-1",
            subject_id="npc-1",
            subject_kind="npc",
            fact_type="missing-owner",
            value={"x": 1},
            audience=OWNER_AUDIENCE,
        )
    _add_completed_turn(url, "root", "turn-2", world_revision=2)
    _set_world_revision(url, "root", 1)
    with pytest.raises(ValueError, match="超出当前世界 revision"):
        service.propose_candidate(
            world_id="root",
            source_turn_id="turn-2",
            subject_id="npc-1",
            subject_kind="npc",
            fact_type="future-source",
            value={"x": 2},
        )


def test_accept_idempotent_on_same_digest(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    _add_completed_turn(url, "root", "turn-2")
    service = StructuredMemoryService(url)

    first = _accept(service, world_id="root", turn_id="turn-1")
    # Same content via a different source turn → same fact, no duplicate.
    again = _accept(service, world_id="root", turn_id="turn-2")

    assert first == again
    with session_scope(url) as session:
        assert session.query(MemoryFact).count() == 1


def test_accept_conflict_supersedes_previous(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)

    old_id = _accept(service, world_id="root", turn_id="turn-1", value={"fact": "v1"})
    new_id = _accept(service, world_id="root", turn_id="turn-1", value={"fact": "v2"})

    assert new_id != old_id
    with session_scope(url) as session:
        old = session.get(MemoryFact, old_id)
        new = session.get(MemoryFact, new_id)
        assert old.status == "superseded"
        assert new.status == "accepted"
        assert new.revision == 2
        assert new.supersedes_id == old_id


def test_accept_records_tier_fact_without_gating(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    _set_npc_revealed(url, "root", "npc-1", level=1)
    service = StructuredMemoryService(url)

    # tier is a visibility attribute, not a fact-legality gate: a tier-2 fact
    # may be accepted even when only tier 1 is revealed; retrieval gates it.
    fact_id = _accept(service, world_id="root", turn_id="turn-1", tier=2)
    with session_scope(url) as session:
        assert session.get(MemoryFact, fact_id).tier == 2


def test_tier_only_valid_for_npc_subject(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    service = StructuredMemoryService(url)
    with pytest.raises(ValueError, match="只有 npc"):
        service.propose_candidate(
            world_id="root",
            source_turn_id="turn-1",
            subject_id="item-1",
            subject_kind="item",
            fact_type="broken",
            value={"x": 1},
            tier=1,
        )


# ---------------------------------------------------------------------------
# shadow retrieval: branch / audience / tier / world isolation
# ---------------------------------------------------------------------------


def test_retrieve_branch_isolates_siblings_and_descendants(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-root")
    service = StructuredMemoryService(url)
    # Accepted before either child is forked, so it belongs to both snapshots.
    _accept(service, world_id="root", turn_id="turn-root", subject_id="npc-root")
    _add_world(
        url,
        "branch-a",
        parent="root",
        source_turn_id="turn-root",
        source_world_revision=1,
        state_revision=1,
    )
    _add_world(
        url,
        "branch-b",
        parent="root",
        source_turn_id="turn-root",
        source_world_revision=1,
        state_revision=1,
    )
    _add_completed_turn(url, "branch-a", "turn-branch-a")
    _add_completed_turn(url, "branch-b", "turn-branch-b")
    backfill_world_root_ids(url)

    _accept(service, world_id="branch-a", turn_id="turn-branch-a", subject_id="npc-a")
    _accept(service, world_id="branch-b", turn_id="turn-branch-b", subject_id="npc-b")

    # branch-a sees its own fact + the root ancestor's fact, never its sibling.
    result = service.retrieve(world_id="branch-a")
    subjects = {r["subject_id"] for r in result["recalled"]}
    assert subjects == {"npc-a", "npc-root"}
    # Public/shadow views never disclose a sibling fact's metadata merely to
    # explain why it was blocked.
    assert result["blocked"] == []

    # root sees only its own fact (descendants are not ancestors).
    result = service.retrieve(world_id="root")
    assert {r["subject_id"] for r in result["recalled"]} == {"npc-root"}


def test_child_branch_does_not_see_parent_facts_after_fork(tmp_path: Path) -> None:
    """An ancestor is visible only through the exact fork snapshot revision."""
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    _accept(
        service,
        world_id="root",
        turn_id="root-1",
        subject_id="before-fork",
        fact_type="clue",
        value={"when": "before"},
    )
    _add_world(
        url,
        "child",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=1,
        state_revision=1,
    )
    _add_completed_turn(url, "root", "root-2", world_revision=2)
    _accept(
        service,
        world_id="root",
        turn_id="root-2",
        subject_id="after-fork",
        fact_type="clue",
        value={"when": "after"},
    )

    child = service.retrieve(world_id="child")
    assert {item["subject_id"] for item in child["recalled"]} == {"before-fork"}
    assert child["blocked"] == []


def test_child_recovers_parent_fact_superseded_after_fork(tmp_path: Path) -> None:
    """A post-fork replacement must not hide the historical fact the child inherited."""
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    original_id = _accept(
        service,
        world_id="root",
        turn_id="root-1",
        subject_id="safe",
        fact_type="location",
        value={"room": "study"},
    )
    _add_world(
        url,
        "child",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=1,
        state_revision=1,
    )
    _add_completed_turn(url, "root", "root-2", world_revision=2)
    replacement_id = _accept(
        service,
        world_id="root",
        turn_id="root-2",
        subject_id="safe",
        fact_type="location",
        value={"room": "cellar"},
    )

    child = service.retrieve(world_id="child")
    assert [(item["fact_id"], item["value"]) for item in child["recalled"]] == [
        (original_id, {"room": "study"})
    ]
    root = service.retrieve(world_id="root")
    assert [(item["fact_id"], item["value"]) for item in root["recalled"]] == [
        (replacement_id, {"room": "cellar"})
    ]


@pytest.mark.parametrize(
    "cutoff_field",
    ["memory_cutoff_at", "created_at"],
)
def test_child_excludes_late_acceptance_from_old_source_turn_and_supersede(
    tmp_path: Path,
    cutoff_field: str,
) -> None:
    """Fork time applies to acceptance, not only a fact's source turn.

    A fact may be accepted after a fork while truthfully citing an old source
    turn.  It must not enter the child, and its late supersede must not hide
    the pre-fork version that the child did inherit.  Parameterizing the
    metadata key proves both new branches and pre-H3 ``created_at`` branches
    use the same conservative visibility rule.
    """
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    original_id = _accept(
        service,
        world_id="root",
        turn_id="root-1",
        subject_id="cabinet",
        fact_type="location",
        value={"room": "study"},
    )
    fork_at = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    _set_fact_decided_at(url, original_id, fork_at - timedelta(seconds=1))
    branch_kwargs = (
        {"memory_cutoff_at": fork_at.isoformat()}
        if cutoff_field == "memory_cutoff_at"
        else {"branch_created_at": fork_at.isoformat()}
    )
    _add_world(
        url,
        "child",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=1,
        state_revision=1,
        **branch_kwargs,
    )

    # This acceptance happens after the child exists but cites the *same*
    # old source turn.  Revision-only filtering would leak it into the child.
    replacement_id = _accept(
        service,
        world_id="root",
        turn_id="root-1",
        subject_id="cabinet",
        fact_type="location",
        value={"room": "cellar"},
    )
    _set_fact_decided_at(url, replacement_id, fork_at + timedelta(seconds=1))

    child = service.retrieve(world_id="child")
    assert [(item["fact_id"], item["value"]) for item in child["recalled"]] == [
        (original_id, {"room": "study"})
    ]
    root = service.retrieve(world_id="root")
    assert [(item["fact_id"], item["value"]) for item in root["recalled"]] == [
        (replacement_id, {"room": "cellar"})
    ]


def test_nested_branch_uses_each_ancestor_cutoff_and_excludes_sibling(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    _accept(service, world_id="root", turn_id="root-1", subject_id="root-early")
    _add_world(
        url,
        "branch-a",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=1,
        state_revision=1,
    )
    _add_completed_turn(url, "branch-a", "a-2", world_revision=2)
    _accept(service, world_id="branch-a", turn_id="a-2", subject_id="a-early")
    _add_world(
        url,
        "branch-b",
        parent="branch-a",
        source_turn_id="a-2",
        source_world_revision=2,
        state_revision=2,
    )
    _add_world(
        url,
        "sibling",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=1,
        state_revision=1,
    )
    _add_completed_turn(url, "sibling", "sibling-2", world_revision=2)
    _accept(service, world_id="sibling", turn_id="sibling-2", subject_id="sibling-only")
    _add_completed_turn(url, "root", "root-3", world_revision=3)
    _accept(service, world_id="root", turn_id="root-3", subject_id="root-late")
    _add_completed_turn(url, "branch-a", "a-3", world_revision=3)
    _accept(service, world_id="branch-a", turn_id="a-3", subject_id="a-late")

    result = service.retrieve(world_id="branch-b")
    assert {item["subject_id"] for item in result["recalled"]} == {"root-early", "a-early"}
    assert result["blocked"] == []


def test_nested_branch_with_impossible_timestamp_order_fails_closed(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    _add_world(
        url,
        "branch-a",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=1,
        state_revision=1,
        memory_cutoff_at="2030-01-02T03:04:05+00:00",
    )
    _add_completed_turn(url, "branch-a", "a-1", world_revision=1)
    _add_world(
        url,
        "branch-b",
        parent="branch-a",
        source_turn_id="a-1",
        source_world_revision=1,
        state_revision=1,
        memory_cutoff_at="2030-01-02T03:04:04+00:00",
    )

    assert StructuredMemoryService(url).retrieve(world_id="branch-b") == {
        "root_world_id": "",
        "recalled": [],
        "blocked": [],
    }


def test_old_save_revision_recalls_historical_superseded_fact(tmp_path: Path) -> None:
    """A restored older WorldState never reads a fact sourced in its future."""
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    original_id = _accept(
        service,
        world_id="root",
        turn_id="root-1",
        subject_id="door",
        fact_type="state",
        value={"locked": True},
    )
    _add_completed_turn(url, "root", "root-5", world_revision=5)
    _accept(
        service,
        world_id="root",
        turn_id="root-5",
        subject_id="door",
        fact_type="state",
        value={"locked": False},
    )
    # Simulate opening an older save after both accepted rows already exist.
    _set_world_revision(url, "root", 1)

    result = service.retrieve(world_id="root")
    assert [(item["fact_id"], item["value"]) for item in result["recalled"]] == [
        (original_id, {"locked": True})
    ]


@pytest.mark.parametrize(
    "branch",
    [
        pytest.param(None, id="branch-key-null"),
        pytest.param([], id="branch-key-list"),
        {"parent_world_id": "missing", "source_turn_id": "root-1", "source_world_revision": 1},
        {"parent_world_id": "child", "source_turn_id": "root-1", "source_world_revision": 1},
        {"parent_world_id": "root", "source_turn_id": "", "source_world_revision": 1},
        {"parent_world_id": "root", "source_turn_id": "root-1", "source_world_revision": "1"},
        pytest.param(
            {
                "parent_world_id": "root",
                "source_turn_id": "root-1",
                "source_world_revision": 1,
            },
            id="missing-memory-cutoff",
        ),
        pytest.param(
            {
                "parent_world_id": "root",
                "source_turn_id": "root-1",
                "source_world_revision": 1,
                "created_at": "not-a-timestamp",
            },
            id="malformed-legacy-cutoff",
        ),
        pytest.param(
            {
                "parent_world_id": "root",
                "source_turn_id": "root-1",
                "source_world_revision": 1,
                "created_at": "2030-01-02T03:04:05",
            },
            id="timezone-less-legacy-cutoff",
        ),
        pytest.param(
            {
                "parent_world_id": "root",
                "source_turn_id": "root-1",
                "source_world_revision": 1,
                "created_at": "2030-01-02T03:04:05+00:00",
                "memory_cutoff_at": None,
            },
            id="explicit-malformed-new-cutoff-does-not-fallback",
        ),
    ],
)
def test_malformed_or_orphan_branch_metadata_fails_closed(
    tmp_path: Path,
    branch: object,
) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    _accept(service, world_id="root", turn_id="root-1", subject_id="root-fact")
    _add_world(url, "child", state_revision=1)
    with session_scope(url) as session:
        child = session.get(World, "child")
        assert child is not None
        child.metadata_json = {"branch": branch}

    public = service.retrieve(world_id="child")
    assert public == {"root_world_id": "", "recalled": [], "blocked": []}
    internal = service.retrieve(world_id="child", internal=True)
    assert internal["recalled"] == []
    assert internal["blocked"] == [{"reason": "invalid_lineage"}]


def test_cycle_lineage_fails_closed(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "a", state_revision=1)
    _add_world(url, "b", state_revision=1)
    with session_scope(url) as session:
        a = session.get(World, "a")
        b = session.get(World, "b")
        assert a is not None and b is not None
        a.metadata_json = {
            "branch": {
                "parent_world_id": "b",
                "source_turn_id": "b-1",
                "source_world_revision": 1,
                "created_at": "2030-01-02T03:04:05+00:00",
            }
        }
        b.metadata_json = {
            "branch": {
                "parent_world_id": "a",
                "source_turn_id": "a-1",
                "source_world_revision": 1,
                "created_at": "2030-01-02T03:04:05+00:00",
            }
        }
        session.add_all(
            [
                Turn(
                    pk=new_id("turnrow"),
                    id="a-1",
                    world_id="a",
                    kind="action",
                    status="completed",
                    record={"world_revision": 1},
                    messages=[],
                ),
                Turn(
                    pk=new_id("turnrow"),
                    id="b-1",
                    world_id="b",
                    kind="action",
                    status="completed",
                    record={"world_revision": 1},
                    messages=[],
                ),
            ]
        )

    service = StructuredMemoryService(url)
    assert service.retrieve(world_id="a") == {
        "root_world_id": "",
        "recalled": [],
        "blocked": [],
    }


def test_branch_cutoff_must_match_its_named_source_turn(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    _add_completed_turn(url, "root", "root-2", world_revision=2)
    _add_world(
        url,
        "child",
        parent="root",
        source_turn_id="root-1",
        source_world_revision=2,
        state_revision=2,
    )

    result = StructuredMemoryService(url).retrieve(world_id="child")
    assert result == {"root_world_id": "", "recalled": [], "blocked": []}


def test_retrieval_order_and_limit_are_deterministic_and_bounded(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    for index in range(MAX_RETRIEVAL_LIMIT + 5):
        _accept(
            service,
            world_id="root",
            turn_id="root-1",
            subject_id=f"npc-{index:03d}",
            fact_type="memory",
            value={"index": index},
        )

    first = service.retrieve(world_id="root", limit=3)
    second = service.retrieve(world_id="root", limit=3)
    assert first == second
    assert [item["subject_id"] for item in first["recalled"]] == [
        "npc-000",
        "npc-001",
        "npc-002",
    ]
    capped = service.retrieve(world_id="root", limit=MAX_RETRIEVAL_LIMIT + 100)
    assert len(capped["recalled"]) == MAX_RETRIEVAL_LIMIT
    with pytest.raises(ValueError, match="大于零"):
        service.retrieve(world_id="root", limit=0)


def test_retrieval_enforces_aggregate_response_budget(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "root-1", world_revision=1)
    service = StructuredMemoryService(url)
    for index in range(5):
        _accept(
            service,
            world_id="root",
            turn_id="root-1",
            subject_id=f"payload-{index}",
            fact_type="large",
            value={"text": "x" * 30_000},
        )

    result = service.retrieve(world_id="root", limit=10)
    assert 0 < len(result["recalled"]) < 5
    rendered_bytes = sum(
        len(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for item in result["recalled"]
    )
    assert rendered_bytes <= MAX_RETRIEVAL_RESPONSE_BYTES


def test_retrieve_cross_world_zero_recall(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "tree-1")
    _add_world(url, "tree-2")
    _add_completed_turn(url, "tree-1", "turn-1")
    backfill_world_root_ids(url)
    service = StructuredMemoryService(url)

    _accept(service, world_id="tree-1", turn_id="turn-1", subject_id="npc-1")

    result = service.retrieve(world_id="tree-2")
    assert result["recalled"] == []
    assert result["blocked"] == []


def test_retrieve_audience_gates(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    _add_user(url, "u1")
    backfill_world_root_ids(url)
    service = StructuredMemoryService(url)

    _accept(service, world_id="root", turn_id="turn-1", subject_id="pub")
    _accept(
        service,
        world_id="root",
        turn_id="turn-1",
        subject_id="owner-fact",
        fact_type="owns",
        audience=OWNER_AUDIENCE,
        owner_user_id="u1",
    )
    _accept(
        service,
        world_id="root",
        turn_id="turn-1",
        subject_id="priv",
        fact_type="secret",
        audience=MODEL_PRIVATE_AUDIENCE,
    )

    public_view = service.retrieve(world_id="root", owner_user_id="u2")
    public_subjects = {r["subject_id"] for r in public_view["recalled"]}
    assert public_subjects == {"pub"}
    # The default result is intentionally not a private-fact existence oracle.
    assert public_view["blocked"] == []

    diagnostic_view = service.retrieve(
        world_id="root",
        owner_user_id="u2",
        internal=True,
    )
    blocked = {(b["subject_id"], b["reason"]) for b in diagnostic_view["blocked"]}
    assert ("owner-fact", "owner_gate") in blocked

    # The owner sees their own fact; engine-internal sees model_private too.
    owner_view = service.retrieve(world_id="root", owner_user_id="u1")
    assert {r["subject_id"] for r in owner_view["recalled"]} == {"pub", "owner-fact"}
    internal_view = service.retrieve(world_id="root", owner_user_id="u2", internal=True)
    assert {r["subject_id"] for r in internal_view["recalled"]} == {"pub", "priv"}


def test_retrieve_tier_gate_and_tierless_recall(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    _set_npc_revealed(url, "root", "npc-1", level=1)
    backfill_world_root_ids(url)
    service = StructuredMemoryService(url)

    _accept(service, world_id="root", turn_id="turn-1", subject_id="npc-1", fact_type="low", tier=1)
    _accept(service, world_id="root", turn_id="turn-1", subject_id="npc-1", fact_type="high", tier=2)
    _accept(
        service,
        world_id="root",
        turn_id="turn-1",
        subject_id="npc-1",
        fact_type="tierless",
        value={"note": "plain"},
        tier=None,
    )

    result = service.retrieve(world_id="root")
    recalled_types = {r["fact_type"] for r in result["recalled"]}
    assert recalled_types == {"low", "tierless"}  # tierless (tier=None) is not misblocked
    assert result["blocked"] == []


def test_retrieve_tier_gate_uses_request_world_state_child_promotion(tmp_path: Path) -> None:
    """Tier gating reflects the *requesting* world's WorldState, not the
    fact's source world: a child branch that has revealed more about the NPC
    can recall a higher-tier fact accepted in the ancestor, while a sibling
    branch (whose fact the requester is not on the ancestor chain of) is
    still rejected regardless of the sibling's own revealed level."""
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-root")
    # Root accepts this before the two children fork.  Its tier is then gated
    # by the requesting child's state, not by the root's state.
    _set_npc_revealed(url, "root", "npc-1", level=1)
    service = StructuredMemoryService(url)
    _accept(
        service,
        world_id="root",
        turn_id="turn-root",
        subject_id="npc-1",
        fact_type="secret",
        tier=2,
    )
    _add_world(
        url,
        "branch-a",
        parent="root",
        source_turn_id="turn-root",
        source_world_revision=1,
        state_revision=1,
    )
    _add_world(
        url,
        "branch-b",
        parent="root",
        source_turn_id="turn-root",
        source_world_revision=1,
        state_revision=1,
    )
    _add_completed_turn(url, "branch-a", "turn-branch-a")
    _add_completed_turn(url, "branch-b", "turn-branch-b")
    # branch-a has revealed the NPC to tier-2; branch-b has revealed it to
    # tier-3.  Root still only knows tier-1.
    _set_npc_revealed(url, "branch-a", "npc-1", level=2)
    _set_npc_revealed(url, "branch-b", "npc-1", level=3)
    backfill_world_root_ids(url)

    # branch-b accepts its own tier-3 fact (a sibling of branch-a).
    _accept(service, world_id="branch-b", turn_id="turn-branch-b", subject_id="npc-1", fact_type="branch-b-secret", tier=3)

    # branch-a: root's tier-2 fact is now visible (child promotion), the
    # sibling's fact stays blocked.
    result_a = service.retrieve(world_id="branch-a")
    recalled_a = {r["fact_type"] for r in result_a["recalled"]}
    assert "secret" in recalled_a
    assert result_a["blocked"] == []

    # root itself: revealed level 1 blocks the tier-2 fact (source world is
    # irrelevant — the requesting world's own state gates it).
    result_root = service.retrieve(world_id="root")
    assert "secret" not in {r["fact_type"] for r in result_root["recalled"]}
    assert result_root["blocked"] == []

    # branch-b: root's tier-2 fact is visible (its revealed level is 3 >= 2),
    # but branch-a's own fact would be a sibling — branch-a has none here, so
    # recall only includes the ancestor fact.
    result_b = service.retrieve(world_id="branch-b")
    recalled_b = {r["fact_type"] for r in result_b["recalled"]}
    assert {"secret", "branch-b-secret"} <= recalled_b


def test_transcript_is_not_auto_promoted(tmp_path: Path) -> None:
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_completed_turn(url, "root", "turn-1")
    backfill_world_root_ids(url)
    # A verbatim transcript lands in WorldState like the legacy npc_conversations
    # path does — but no structured-memory candidate/fact may appear from it.
    with session_scope(url) as session:
        row = session.get(WorldState, "root")
        if row is None:
            row = WorldState(world_id="root", schema_version=2, revision=0, state={})
            session.add(row)
        state = dict(row.state or {})
        state["npc_conversations"] = {
            "npc-1": [{"id": "d1", "text": "the butler said he saw nothing"}]
        }
        row.state = state

    service = StructuredMemoryService(url)
    result = service.retrieve(world_id="root")
    assert result["recalled"] == []
    with session_scope(url) as session:
        assert session.query(MemoryFactCandidate).count() == 0
        assert session.query(MemoryFact).count() == 0


def test_structured_memory_is_not_wired_to_model_or_network_surfaces() -> None:
    """H3 is storage/diagnostic-only until a separately reviewed integration."""
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "server.py",
        "src/app/engine.py",
        "src/ai/model/model_streamer.py",
        "src/ai/tools/registry.py",
        "src/multiplayer/http.py",
        "src/multiplayer/ws.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "StructuredMemoryService" not in source
        assert "propose_candidate(" not in source
        assert "accept_fact(" not in source


# ---------------------------------------------------------------------------
# PostgreSQL idempotency constraints (opt-in via TRPG_TEST_POSTGRES_URL)
# ---------------------------------------------------------------------------

POSTGRES_URL = os.environ.get("TRPG_TEST_POSTGRES_URL", "").strip()


@pytest.mark.skipif(not POSTGRES_URL, reason="set TRPG_TEST_POSTGRES_URL to run PostgreSQL checks")
def test_postgresql_memory_fact_idempotency_constraints() -> None:
    """Both idempotency guards hold on PostgreSQL (not only SQLite)."""
    suffix = secrets.token_hex(5)
    world_id = f"pg-mem-{suffix}"
    # Idempotent create_all builds the H3 tables if the 0010 migration has not
    # run yet in the target database.
    Base.metadata.create_all(get_engine(POSTGRES_URL))

    def fact(digest: str) -> MemoryFact:
        return MemoryFact(
            id=new_id("memfact"),
            world_id=world_id,
            root_world_id=world_id,
            source_turn_id="turn-pg",
            subject_id="npc-1",
            subject_kind="npc",
            fact_type="knows",
            value={"digest": digest},
            digest=digest,
            audience=PUBLIC_AUDIENCE,
            status="accepted",
        )

    with session_scope(POSTGRES_URL) as session:
        session.add(World(id=world_id, module_name="mod"))
        # No ORM relationship connects these independently-created rows.
        # Flush the FK parent explicitly so PostgreSQL never depends on unit-
        # of-work insertion ordering (SQLite's local test path can mask this).
        session.flush()
        session.add(fact("d1"))

    # Same content digest → uq_memory_fact_digest rejects the duplicate.
    with pytest.raises(IntegrityError):
        with session_scope(POSTGRES_URL) as session:
            session.add(fact("d1"))

    # Different digest, same (world, subject, fact_type), still accepted →
    # partial unique index rejects a second current fact.
    with pytest.raises(IntegrityError):
        with session_scope(POSTGRES_URL) as session:
            session.add(fact("d2"))

    indexes = {
        index["name"]: index
        for index in inspect(get_engine(POSTGRES_URL)).get_indexes("memory_facts")
    }
    assert indexes[MEMORY_FACT_CURRENT_INDEX]["unique"]
