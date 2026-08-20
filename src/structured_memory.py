"""H3 structured memory: candidate → accepted fact + shadow retrieval.

Boundary (half-side, not wired to GameEngine yet):

- The model / engine may only *propose* a :class:`MemoryFactCandidate`; nothing
  in that table is authoritative.
- Only the trusted engine may *accept* a candidate into :class:`MemoryFact`,
  and only through an explicit ``source_turn_id`` + non-empty ``provenance``
  handshake.  There is no model-facing tool for acceptance, so the model can
  never write authority directly.
- ``npc_conversations`` (verbatim transcript) is deliberately untouched: spoken
  text is never auto-promoted to an accepted fact here.
- ``retrieve`` is a **shadow retriever**: it returns candidate references and
  gate diagnostics only, never injects into a model request and never mutates
  ``WorldState``.

Branch/audience/tier gates are all fail-closed: a fact is recalled only when
its tree matches the current world's root, its world lies on the current
world's ancestor chain (siblings are excluded), its audience admits the
caller, and its tier is within the subject's revealed level.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .database import (
    MemoryFact,
    MemoryFactCandidate,
    Turn,
    World,
    WorldState,
    new_id,
    session_scope,
)

PUBLIC_AUDIENCE = "public"
MODEL_PRIVATE_AUDIENCE = "model_private"
OWNER_AUDIENCE = "owner"
VALID_AUDIENCES = {PUBLIC_AUDIENCE, MODEL_PRIVATE_AUDIENCE, OWNER_AUDIENCE}

PROPOSED_STATUS = "proposed"
ACCEPTED_STATUS = "accepted"
SUPERSEDED_STATUS = "superseded"
REJECTED_STATUS = "rejected"

TIER_SUBJECT_KIND = "npc"


def fact_digest(subject_id: str, fact_type: str, value: Any) -> str:
    """Stable content digest over (subject, fact_type, value)."""
    canonical = json.dumps(
        {"subject_id": subject_id, "fact_type": fact_type, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _branch_parent(metadata: dict[str, Any]) -> str:
    branch = metadata.get("branch")
    if not isinstance(branch, dict):
        return ""
    return str(branch.get("parent_world_id") or "").strip()


def _derive_root_locked(session: Any, world_id: str, worlds_by_id: dict[str, World]) -> str:
    """Walk the branch parent chain to its root, cycle-safe."""
    seen: set[str] = set()
    current = world_id
    while current and current not in seen:
        seen.add(current)
        world = worlds_by_id.get(current)
        if world is None:
            break
        parent = _branch_parent(dict(world.metadata_json or {}))
        if not parent or parent not in worlds_by_id:
            return current
        current = parent
    return world_id


def backfill_world_root_ids(url: str) -> int:
    """Idempotently fill empty ``World.root_world_id`` values.

    Only rows whose ``root_world_id`` is empty are touched; existing values are
    never overwritten.  Returns the number of rows backfilled.
    """
    updated = 0
    with session_scope(url) as session:
        worlds = session.query(World).all()
        worlds_by_id = {w.id: w for w in worlds}
        for world in worlds:
            if not world.root_world_id:
                world.root_world_id = _derive_root_locked(
                    session, world.id, worlds_by_id
                )
                updated += 1
    return updated


def _ancestor_chain(session: Any, world_id: str, worlds_by_id: dict[str, World]) -> list[str]:
    """Ancestor chain (root → … → world_id), cycle-safe, includes both ends."""
    chain: list[str] = []
    seen: set[str] = set()
    current = world_id
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        world = worlds_by_id.get(current)
        if world is None:
            break
        parent = _branch_parent(dict(world.metadata_json or {}))
        if not parent or parent not in worlds_by_id:
            break
        current = parent
    return chain


def _revealed_level(session: Any, world_id: str, subject_id: str, subject_kind: str) -> int | None:
    """Revealed tier level for a subject (None = no tier gate applies)."""
    if subject_kind != TIER_SUBJECT_KIND:
        return None
    state_row = session.get(WorldState, world_id)
    if state_row is None:
        return 0
    for npc in (state_row.state or {}).get("npcs") or []:
        if isinstance(npc, dict) and str(npc.get("id") or "") == subject_id:
            revealed = npc.get("revealed") or {}
            return int(revealed.get("level") or 0)
    return 0


class StructuredMemoryService:
    """Candidate proposal, trusted acceptance, and shadow retrieval."""

    def __init__(self, url: str) -> None:
        self.url = url

    # -- validation helpers ------------------------------------------------

    @staticmethod
    def _validate_fields(
        *,
        subject_id: str,
        fact_type: str,
        audience: str,
        tier: int | None,
        subject_kind: str,
    ) -> None:
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ValueError("subject_id 不能为空")
        if not isinstance(fact_type, str) or not fact_type.strip():
            raise ValueError("fact_type 不能为空")
        if audience not in VALID_AUDIENCES:
            raise ValueError(f"非法 audience: {audience!r}")
        if tier is not None:
            if isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 3:
                raise ValueError("tier 必须是 1..3 的整数或 None")
            if subject_kind != TIER_SUBJECT_KIND:
                raise ValueError("只有 npc subject 可携带 tier")

    @staticmethod
    def _completed_turn(session: Any, turn_id: str, world_id: str) -> bool:
        turn = (
            session.query(Turn)
            .filter_by(id=turn_id, world_id=world_id)
            .one_or_none()
        )
        return turn is not None and turn.status == "completed"

    def _world_root(self, session: Any, world_id: str) -> str:
        worlds_by_id = {w.id: w for w in session.query(World).all()}
        world = worlds_by_id.get(world_id)
        if world is None:
            raise ValueError(f"world 不存在: {world_id}")
        if world.root_world_id:
            return world.root_world_id
        return _derive_root_locked(session, world_id, worlds_by_id)

    # -- candidate proposal ------------------------------------------------

    def propose_candidate(
        self,
        *,
        world_id: str,
        source_turn_id: str,
        subject_id: str,
        subject_kind: str,
        fact_type: str,
        value: Any,
        audience: str = PUBLIC_AUDIENCE,
        owner_user_id: str | None = None,
        tier: int | None = None,
        provenance: list[Any] | None = None,
    ) -> str:
        """Record a proposed fact.  Requires a completed source turn.

        The model / engine may propose here; this never writes authority.
        Idempotent on ``(world, source_turn, subject, fact_type, digest)``.
        """
        self._validate_fields(
            subject_id=subject_id,
            fact_type=fact_type,
            audience=audience,
            tier=tier,
            subject_kind=subject_kind,
        )
        digest = fact_digest(subject_id, fact_type, value)
        with session_scope(self.url) as session:
            if not self._completed_turn(session, source_turn_id, world_id):
                raise ValueError("source turn 未完整提交，无法提出候选")
            existing = (
                session.query(MemoryFactCandidate)
                .filter_by(
                    world_id=world_id,
                    source_turn_id=source_turn_id,
                    subject_id=subject_id,
                    fact_type=fact_type,
                    digest=digest,
                )
                .one_or_none()
            )
            if existing is not None:
                return existing.id
            root = self._world_root(session, world_id)
            candidate = MemoryFactCandidate(
                id=new_id("memcand"),
                world_id=world_id,
                root_world_id=root,
                source_turn_id=source_turn_id,
                subject_id=subject_id.strip(),
                subject_kind=subject_kind,
                fact_type=fact_type.strip(),
                value=copy.deepcopy(value),
                digest=digest,
                audience=audience,
                owner_user_id=owner_user_id,
                tier=tier,
                provenance=list(provenance or []),
                status=PROPOSED_STATUS,
            )
            session.add(candidate)
            session.flush()
            return candidate.id

    # -- trusted acceptance ------------------------------------------------

    def accept_fact(
        self,
        candidate_id: str,
        *,
        source_turn_id: str | None = None,
        provenance: list[Any] | None = None,
    ) -> str:
        """Trusted-engine acceptance: promote a candidate to authority.

        Fails closed unless the caller supplies the candidate's own completed
        ``source_turn_id`` and a non-empty ``provenance``.  Re-accepting the
        same content is a no-op; a conflicting current fact is superseded
        (``revision``+1, ``supersedes_id`` chain).
        """
        provenance = provenance or []
        with session_scope(self.url) as session:
            candidate = session.get(MemoryFactCandidate, candidate_id)
            if candidate is None:
                raise ValueError("candidate 不存在")
            if candidate.status != PROPOSED_STATUS:
                raise ValueError(f"candidate 已处理: {candidate.status}")
            if source_turn_id is None or source_turn_id != candidate.source_turn_id:
                raise ValueError("source_turn_id 与候选不匹配")
            if not self._completed_turn(session, candidate.source_turn_id, candidate.world_id):
                raise ValueError("source turn 未完整提交，拒绝接受")
            if not provenance:
                raise ValueError("provenance 不能为空")

            existing = (
                session.query(MemoryFact)
                .filter_by(
                    world_id=candidate.world_id,
                    subject_id=candidate.subject_id,
                    fact_type=candidate.fact_type,
                    digest=candidate.digest,
                )
                .one_or_none()
            )
            if existing is not None:
                candidate.status = ACCEPTED_STATUS
                return existing.id

            current = (
                session.query(MemoryFact)
                .filter_by(
                    world_id=candidate.world_id,
                    subject_id=candidate.subject_id,
                    fact_type=candidate.fact_type,
                    status=ACCEPTED_STATUS,
                )
                .one_or_none()
            )
            revision = 1
            supersedes_id = None
            if current is not None:
                revision = int(current.revision) + 1
                supersedes_id = current.id
                current.status = SUPERSEDED_STATUS

            fact = MemoryFact(
                id=new_id("memfact"),
                world_id=candidate.world_id,
                root_world_id=candidate.root_world_id,
                source_turn_id=candidate.source_turn_id,
                subject_id=candidate.subject_id,
                subject_kind=candidate.subject_kind,
                fact_type=candidate.fact_type,
                value=copy.deepcopy(candidate.value),
                digest=candidate.digest,
                audience=candidate.audience,
                owner_user_id=candidate.owner_user_id,
                tier=candidate.tier,
                provenance=copy.deepcopy(provenance),
                revision=revision,
                supersedes_id=supersedes_id,
                status=ACCEPTED_STATUS,
            )
            session.add(fact)
            candidate.status = ACCEPTED_STATUS
            session.flush()
            return fact.id

    # -- shadow retrieval ---------------------------------------------------

    @staticmethod
    def _audience_block(
        fact: MemoryFact, *, internal: bool, owner_user_id: str | None
    ) -> str | None:
        if fact.audience == PUBLIC_AUDIENCE:
            return None
        if fact.audience == MODEL_PRIVATE_AUDIENCE:
            return None if internal else "model_private_gate"
        if fact.audience == OWNER_AUDIENCE:
            if owner_user_id and fact.owner_user_id == owner_user_id:
                return None
            return "owner_gate"
        return f"unknown_audience:{fact.audience}"

    def retrieve(
        self,
        *,
        world_id: str,
        owner_user_id: str | None = None,
        internal: bool = False,
    ) -> dict[str, Any]:
        """Shadow recall: references + gate diagnostics, never injection.

        Recalled facts pass every gate (tree, branch lineage, audience, tier);
        blocked facts are reported with a reason.  No ``WorldState`` mutation
        happens anywhere in this path.
        """
        with session_scope(self.url) as session:
            worlds_by_id = {w.id: w for w in session.query(World).all()}
            world = worlds_by_id.get(world_id)
            if world is None:
                raise ValueError(f"world 不存在: {world_id}")
            root = world.root_world_id or _derive_root_locked(
                session, world_id, worlds_by_id
            )
            ancestors = set(_ancestor_chain(session, world_id, worlds_by_id))

            recalled: list[dict[str, Any]] = []
            blocked: list[dict[str, Any]] = []
            facts = (
                session.query(MemoryFact)
                .filter(MemoryFact.root_world_id == root, MemoryFact.status == ACCEPTED_STATUS)
                .all()
            )
            for fact in facts:
                reason = None
                if fact.root_world_id != root:
                    reason = "different_tree"
                elif fact.world_id not in ancestors:
                    reason = "sibling_branch"
                else:
                    reason = self._audience_block(
                        fact, internal=internal, owner_user_id=owner_user_id
                    )
                if reason is None and fact.tier is not None:
                    # Tier gating reflects what the *requesting* world
                    # currently knows about the subject (its own WorldState),
                    # not where the fact was born: a child branch that has
                    # revealed more about the NPC can recall the higher-tier
                    # fact, while a sibling branch is still excluded earlier
                    # by the branch-lineage gate.
                    level = _revealed_level(
                        session, world_id, fact.subject_id, fact.subject_kind
                    )
                    if level is None:
                        reason = "tier_on_non_npc"
                    elif level < fact.tier:
                        reason = f"tier_gate:{fact.tier}>{level}"
                if reason is not None:
                    blocked.append(
                        {
                            "fact_id": fact.id,
                            "subject_id": fact.subject_id,
                            "fact_type": fact.fact_type,
                            "reason": reason,
                        }
                    )
                    continue
                recalled.append(
                    {
                        "fact_id": fact.id,
                        "world_id": fact.world_id,
                        "subject_id": fact.subject_id,
                        "subject_kind": fact.subject_kind,
                        "fact_type": fact.fact_type,
                        "value": copy.deepcopy(fact.value),
                        "audience": fact.audience,
                        "owner_user_id": fact.owner_user_id,
                        "tier": fact.tier,
                        "revision": int(fact.revision),
                    }
                )
        return {
            "root_world_id": root,
            "recalled": recalled,
            "blocked": blocked,
        }
