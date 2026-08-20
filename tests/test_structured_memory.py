"""H3 structured-memory invariants: candidate→fact boundary and shadow retrieval.

Covers the branch/audience/tier isolation contract from
``docs/DEEPSEEK_HARNESS_ADOPTION.md`` §5.5: cross-world, cross-user,
cross-branch and cross-tier wrong recall must be zero, and a verbatim NPC
transcript is never auto-promoted into an accepted fact.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from src.database import (
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
from src.structured_memory import (
    MODEL_PRIVATE_AUDIENCE,
    OWNER_AUDIENCE,
    PUBLIC_AUDIENCE,
    StructuredMemoryService,
    backfill_world_root_ids,
    fact_digest,
)


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'structured-memory.db'}"


def _db(tmp_path: Path) -> str:
    url = _url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    return url


def _add_world(url: str, world_id: str, *, parent: str | None = None) -> None:
    with session_scope(url) as session:
        metadata: dict = {}
        if parent:
            metadata["branch"] = {"parent_world_id": parent}
        session.add(
            World(id=world_id, module_name="mod", metadata_json=metadata)
        )


def _add_completed_turn(url: str, world_id: str, turn_id: str) -> None:
    with session_scope(url) as session:
        session.add(
            Turn(
                pk=new_id("turnrow"),
                id=turn_id,
                world_id=world_id,
                kind="action",
                status="completed",
                record={},
                messages=[],
            )
        )


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
    _add_world(url, "branch-a", parent="root")
    _add_world(url, "branch-b", parent="root")
    for world_id in ("root", "branch-a", "branch-b"):
        _add_completed_turn(url, world_id, f"turn-{world_id}")
    backfill_world_root_ids(url)
    service = StructuredMemoryService(url)

    _accept(service, world_id="root", turn_id="turn-root", subject_id="npc-root")
    _accept(service, world_id="branch-a", turn_id="turn-branch-a", subject_id="npc-a")
    _accept(service, world_id="branch-b", turn_id="turn-branch-b", subject_id="npc-b")

    # branch-a sees its own fact + the root ancestor's fact, never its sibling.
    result = service.retrieve(world_id="branch-a")
    subjects = {r["subject_id"] for r in result["recalled"]}
    assert subjects == {"npc-a", "npc-root"}
    blocked = {(b["subject_id"], b["reason"]) for b in result["blocked"]}
    assert ("npc-b", "sibling_branch") in blocked

    # root sees only its own fact (descendants are not ancestors).
    result = service.retrieve(world_id="root")
    assert {r["subject_id"] for r in result["recalled"]} == {"npc-root"}


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
    blocked = {(b["subject_id"], b["reason"]) for b in public_view["blocked"]}
    assert ("owner-fact", "owner_gate") in blocked
    assert ("priv", "model_private_gate") in blocked

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
    blocked = {(b["fact_type"], b["reason"]) for b in result["blocked"]}
    assert ("high", "tier_gate:2>1") in blocked


def test_retrieve_tier_gate_uses_request_world_state_child_promotion(tmp_path: Path) -> None:
    """Tier gating reflects the *requesting* world's WorldState, not the
    fact's source world: a child branch that has revealed more about the NPC
    can recall a higher-tier fact accepted in the ancestor, while a sibling
    branch (whose fact the requester is not on the ancestor chain of) is
    still rejected regardless of the sibling's own revealed level."""
    url = _db(tmp_path)
    _add_world(url, "root")
    _add_world(url, "branch-a", parent="root")
    _add_world(url, "branch-b", parent="root")
    for world_id in ("root", "branch-a", "branch-b"):
        _add_completed_turn(url, world_id, f"turn-{world_id}")
    # root only knows tier-1; branch-a has revealed the NPC to tier-2;
    # branch-b has revealed it to tier-3.
    _set_npc_revealed(url, "root", "npc-1", level=1)
    _set_npc_revealed(url, "branch-a", "npc-1", level=2)
    _set_npc_revealed(url, "branch-b", "npc-1", level=3)
    backfill_world_root_ids(url)
    service = StructuredMemoryService(url)

    # root accepts the tier-2 fact (acceptance is not gated by revealed level).
    _accept(service, world_id="root", turn_id="turn-root", subject_id="npc-1", fact_type="secret", tier=2)
    # branch-b accepts its own tier-3 fact (a sibling of branch-a).
    _accept(service, world_id="branch-b", turn_id="turn-branch-b", subject_id="npc-1", fact_type="branch-b-secret", tier=3)

    # branch-a: root's tier-2 fact is now visible (child promotion), the
    # sibling's fact stays blocked.
    result_a = service.retrieve(world_id="branch-a")
    recalled_a = {r["fact_type"] for r in result_a["recalled"]}
    assert "secret" in recalled_a
    blocked_a = {(b["fact_type"], b["reason"]) for b in result_a["blocked"]}
    assert ("branch-b-secret", "sibling_branch") in blocked_a

    # root itself: revealed level 1 blocks the tier-2 fact (source world is
    # irrelevant — the requesting world's own state gates it).
    result_root = service.retrieve(world_id="root")
    assert "secret" not in {r["fact_type"] for r in result_root["recalled"]}
    blocked_root = {(b["fact_type"], b["reason"]) for b in result_root["blocked"]}
    assert ("secret", "tier_gate:2>1") in blocked_root

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
