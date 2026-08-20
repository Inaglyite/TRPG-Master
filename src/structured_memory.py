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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .database import (
    MemoryFact,
    MemoryFactCandidate,
    Turn,
    User,
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

# Structured memory remains deliberately small and shadow-only.  These bounds
# are service boundaries rather than a product-facing recall policy: without
# them a corrupt row or an accidental bulk importer could make diagnostic
# retrieval unbounded even though it is not wired into the game loop.
MAX_WORLD_ID_LENGTH = 160
MAX_TURN_ID_LENGTH = 80
MAX_SUBJECT_ID_LENGTH = 200
MAX_SUBJECT_KIND_LENGTH = 32
MAX_FACT_TYPE_LENGTH = 64
MAX_USER_ID_LENGTH = 48
MAX_FACT_VALUE_BYTES = 32 * 1024
MAX_PROVENANCE_BYTES = 16 * 1024
MAX_PROVENANCE_ITEMS = 32
DEFAULT_RETRIEVAL_LIMIT = 32
MAX_RETRIEVAL_LIMIT = 128
MAX_RETRIEVAL_SCAN = 512
MAX_RETRIEVAL_RESPONSE_BYTES = 128 * 1024
MAX_BLOCKED_DIAGNOSTICS = 64
MAX_LINEAGE_DEPTH = 64


class _LineageUnavailable(ValueError):
    """A branch timeline cannot be proved safe enough to inherit facts."""


@dataclass(frozen=True)
class _LineageNode:
    """One visible world plus its revision and accepted-time horizons."""

    world_id: str
    cutoff_revision: int
    accepted_cutoff_at: datetime | None
    depth: int


def _clean_text(value: object, *, field: str, maximum: int, required: bool = True) -> str:
    """Normalize a persisted identifier-like field without silently coercing it."""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise ValueError(f"{field} 不能为空")
        return ""
    if len(cleaned) > maximum:
        raise ValueError(f"{field} 过长")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{field} 含有控制字符")
    return cleaned


def _strict_revision(value: object) -> int | None:
    """Return a persisted world revision only when it is a real non-negative int."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _parse_branch_memory_cutoff(branch: dict[str, Any]) -> datetime:
    """Read one branch's explicit accepted-fact cutoff as UTC.

    New branches write ``memory_cutoff_at``.  ``created_at`` is accepted only
    as a compatibility fallback for branches written before that field was
    introduced.  An explicit but malformed new field must not silently fall
    back to the legacy timestamp: doing so would turn metadata corruption into
    a broader memory visibility window.
    """
    field = "branch.memory_cutoff_at"
    value = branch.get("memory_cutoff_at")
    if "memory_cutoff_at" not in branch:
        field = "branch.created_at"
        value = branch.get("created_at")
    text = _clean_text(value, field=field, maximum=80)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 必须是带时区的 ISO 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} 必须带 UTC/offset 时区")
    return parsed.astimezone(UTC)


def _accepted_at_utc(value: object) -> datetime:
    """Normalize durable ``decided_at`` for branch-cutoff comparison.

    PostgreSQL preserves the UTC-aware value produced by ``utcnow``.  SQLite's
    ``DateTime(timezone=True)`` adapter round-trips that same application-owned
    UTC value without tzinfo, so a naive persisted datetime is interpreted as
    UTC rather than local time.  Anything other than a real datetime remains a
    fail-closed corrupt fact.
    """
    if not isinstance(value, datetime):
        raise ValueError("memory fact decided_at 不可验证")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_candidate_dedupe_conflict(exc: IntegrityError) -> bool:
    """Whether an insert lost only the candidate's documented idempotency race."""
    original = getattr(exc, "orig", None)
    constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
    if constraint == "uq_memory_candidate_dedupe":
        return True
    message = str(original or exc).lower()
    if "uq_memory_candidate_dedupe" in message:
        return True
    # SQLite reports the full constrained column list instead of the named
    # constraint.  Match every column so an unrelated integrity failure cannot
    # be converted into a successful idempotent response.
    return "unique constraint failed:" in message and all(
        f"memory_fact_candidates.{column}" in message
        for column in (
            "world_id",
            "source_turn_id",
            "subject_id",
            "fact_type",
            "digest",
        )
    )


def _canonical_json(value: Any, *, field: str, maximum_bytes: int) -> tuple[Any, str]:
    """Validate and normalize JSON input before it reaches a JSON database column."""
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是可序列化 JSON") from exc
    if len(canonical.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{field} 过大")
    # A JSON round trip makes the database payload and the digest use the same
    # normalized representation (for example, numeric dict keys become JSON
    # strings instead of being silently different in Python memory).
    return json.loads(canonical), canonical


def _normalise_provenance(value: object, *, require_non_empty: bool) -> list[Any]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("provenance 必须是列表")
    if require_non_empty and not items:
        raise ValueError("provenance 不能为空")
    if len(items) > MAX_PROVENANCE_ITEMS:
        raise ValueError("provenance 条目过多")
    normalized, _ = _canonical_json(
        items,
        field="provenance",
        maximum_bytes=MAX_PROVENANCE_BYTES,
    )
    if not isinstance(normalized, list):  # Defensive: json.dumps/list guarantees this.
        raise ValueError("provenance 必须是列表")
    return normalized


def _bounded_retrieval_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit 必须是整数")
    if value < 1:
        raise ValueError("limit 必须大于零")
    return min(value, MAX_RETRIEVAL_LIMIT)


def fact_digest(subject_id: str, fact_type: str, value: Any) -> str:
    """Stable content digest over (subject, fact_type, value)."""
    canonical = json.dumps(
        {"subject_id": subject_id, "fact_type": fact_type, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
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


def _world_state_revision(session: Any, world_id: str) -> int | None:
    row = session.get(WorldState, world_id)
    if row is None:
        return None
    return _strict_revision(row.revision)


def _completed_turn_revision(session: Any, world_id: str, turn_id: str) -> int | None:
    """Read a turn's authoritative post-commit world revision, or nothing.

    The memory tables intentionally do not duplicate ``world_revision``.  A
    fact is anchored to its completed source turn, whose record is the
    authority for the revision at which it became true.  Missing/legacy or
    malformed turn records are not guessed: callers must fail closed instead
    of letting a child timeline see an unprovable ancestor fact.
    """
    turn = session.scalar(
        select(Turn).where(Turn.id == turn_id, Turn.world_id == world_id).limit(1)
    )
    if turn is None or turn.status != "completed" or not isinstance(turn.record, dict):
        return None
    return _strict_revision(turn.record.get("world_revision"))


def _branch_metadata(world: World) -> dict[str, Any] | None:
    """Return a valid branch mapping, ``None`` for a root, else fail closed."""
    metadata = world.metadata_json
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise _LineageUnavailable("world metadata is malformed")
    if "branch" not in metadata:
        return None
    branch = metadata.get("branch")
    if not isinstance(branch, dict):
        raise _LineageUnavailable("branch metadata is malformed")
    return branch


def _strict_lineage(
    session: Any,
    world_id: str,
    worlds_by_id: dict[str, World],
) -> tuple[str, list[_LineageNode]]:
    """Build ``root → target`` visibility nodes with temporal cutoffs.

    A branch's state is a snapshot of its parent at
    ``source_world_revision`` and at its persisted accepted-memory instant.
    Every ancestor therefore gets the revision and accepted-time cutoffs from
    its child link; only the target world is read at its current WorldState
    revision and current accepted-fact horizon.  All metadata, the recorded
    source turn and the participating state revisions must agree.  Any
    ambiguity (orphan, self-parent, cycle, stale snapshot, malformed revision
    or cutoff) makes the entire lineage unreadable.
    """
    requested = worlds_by_id.get(world_id)
    if requested is None:
        raise ValueError(f"world 不存在: {world_id}")

    reverse_nodes: list[_LineageNode] = []
    seen: set[str] = set()
    current = requested
    current_cutoff = _world_state_revision(session, current.id)
    if current_cutoff is None:
        raise _LineageUnavailable("target WorldState is unavailable")
    current_accepted_cutoff: datetime | None = None

    while True:
        if current.id in seen or len(reverse_nodes) >= MAX_LINEAGE_DEPTH:
            raise _LineageUnavailable("branch lineage is cyclic or too deep")
        if current.status != "active":
            raise _LineageUnavailable("inactive world cannot provide memory lineage")
        seen.add(current.id)
        reverse_nodes.append(
            _LineageNode(
                world_id=current.id,
                cutoff_revision=current_cutoff,
                accepted_cutoff_at=current_accepted_cutoff,
                depth=len(reverse_nodes),
            )
        )

        branch = _branch_metadata(current)
        if branch is None:
            root = current.id
            break

        try:
            parent_id = _clean_text(
                branch.get("parent_world_id"),
                field="branch.parent_world_id",
                maximum=MAX_WORLD_ID_LENGTH,
            )
            source_turn_id = _clean_text(
                branch.get("source_turn_id"),
                field="branch.source_turn_id",
                maximum=MAX_TURN_ID_LENGTH,
            )
            source_revision = _strict_revision(branch.get("source_world_revision"))
            accepted_cutoff = _parse_branch_memory_cutoff(branch)
        except ValueError as exc:
            raise _LineageUnavailable("branch metadata is malformed") from exc
        if source_revision is None:
            raise _LineageUnavailable("branch.source_world_revision is malformed")
        if parent_id == current.id or parent_id in seen:
            raise _LineageUnavailable("branch parent is self-referential or cyclic")
        parent = worlds_by_id.get(parent_id)
        if parent is None:
            raise _LineageUnavailable("branch parent is missing")
        if current_cutoff < source_revision:
            # A child cannot be a snapshot of a revision it does not contain.
            raise _LineageUnavailable("child state predates its declared fork")
        parent_revision = _world_state_revision(session, parent_id)
        if parent_revision is None or parent_revision < source_revision:
            raise _LineageUnavailable("parent state predates its declared fork")
        turn_revision = _completed_turn_revision(session, parent_id, source_turn_id)
        if turn_revision is None or turn_revision != source_revision:
            raise _LineageUnavailable("branch source turn does not prove its cutoff")
        # A nested child cannot have been forked before the branch it forks
        # from.  ``min`` below is conservative for visibility, but accepting
        # an impossible timestamp ordering would still let corrupt lineage
        # metadata masquerade as a real history.
        if (
            current_accepted_cutoff is not None
            and current_accepted_cutoff < accepted_cutoff
        ):
            raise _LineageUnavailable("nested branch cutoff predates its parent branch")
        current = parent
        current_cutoff = source_revision
        current_accepted_cutoff = (
            accepted_cutoff
            if current_accepted_cutoff is None
            else min(current_accepted_cutoff, accepted_cutoff)
        )

    for node in reverse_nodes:
        stored_root = str(worlds_by_id[node.world_id].root_world_id or "").strip()
        if stored_root and stored_root != root:
            raise _LineageUnavailable("denormalized root does not match branch lineage")

    nodes = list(reversed(reverse_nodes))
    return root, [
        _LineageNode(
            node.world_id,
            node.cutoff_revision,
            node.accepted_cutoff_at,
            depth,
        )
        for depth, node in enumerate(nodes)
    ]


def _revealed_level(session: Any, world_id: str, subject_id: str, subject_kind: str) -> int | None:
    """Revealed tier level for a subject (None = no tier gate applies)."""
    if subject_kind != TIER_SUBJECT_KIND:
        return None
    state_row = session.get(WorldState, world_id)
    if state_row is None:
        return 0
    state = state_row.state
    if not isinstance(state, dict):
        return 0
    for npc in state.get("npcs") or []:
        if isinstance(npc, dict) and str(npc.get("id") or "") == subject_id:
            revealed = npc.get("revealed") or {}
            if not isinstance(revealed, dict):
                return 0
            level = _strict_revision(revealed.get("level") or 0)
            return level if level is not None and level <= 3 else 0
    return 0


class StructuredMemoryService:
    """Candidate proposal, trusted acceptance, and shadow retrieval."""

    def __init__(self, url: str) -> None:
        self.url = url

    # -- validation helpers ------------------------------------------------

    @staticmethod
    def _normalise_fact_fields(
        *,
        subject_id: object,
        fact_type: object,
        audience: object,
        tier: object,
        subject_kind: object,
    ) -> tuple[str, str, str, str, int | None]:
        clean_subject_id = _clean_text(
            subject_id,
            field="subject_id",
            maximum=MAX_SUBJECT_ID_LENGTH,
        )
        clean_subject_kind = _clean_text(
            subject_kind,
            field="subject_kind",
            maximum=MAX_SUBJECT_KIND_LENGTH,
        )
        clean_fact_type = _clean_text(
            fact_type,
            field="fact_type",
            maximum=MAX_FACT_TYPE_LENGTH,
        )
        if not isinstance(audience, str) or audience not in VALID_AUDIENCES:
            raise ValueError(f"非法 audience: {audience!r}")
        if tier is not None:
            if isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 3:
                raise ValueError("tier 必须是 1..3 的整数或 None")
            if clean_subject_kind != TIER_SUBJECT_KIND:
                raise ValueError("只有 npc subject 可携带 tier")
        return clean_subject_id, clean_subject_kind, clean_fact_type, audience, tier

    @staticmethod
    def _normalise_owner(audience: str, owner_user_id: object) -> str | None:
        if audience == OWNER_AUDIENCE:
            return _clean_text(
                owner_user_id,
                field="owner_user_id",
                maximum=MAX_USER_ID_LENGTH,
            )
        if owner_user_id is not None:
            raise ValueError("只有 owner audience 可以携带 owner_user_id")
        return None

    @staticmethod
    def _lock_world(session: Any, world_id: str) -> World:
        """Serialize propose/accept for one world on PostgreSQL.

        SQLite deliberately ignores ``FOR UPDATE``; its unique constraints
        remain the final guard there.  On the production PostgreSQL path this
        lock avoids a pair of acceptors both deciding that the same fact is
        current before one can supersede it.
        """
        world = session.scalar(
            select(World).where(World.id == world_id).with_for_update().limit(1)
        )
        if world is None:
            raise ValueError(f"world 不存在: {world_id}")
        return world

    @staticmethod
    def _lineage_for_write(
        session: Any,
        world_id: str,
    ) -> tuple[str, list[_LineageNode]]:
        worlds_by_id = {w.id: w for w in session.query(World).all()}
        try:
            return _strict_lineage(session, world_id, worlds_by_id)
        except _LineageUnavailable as exc:
            raise ValueError("world 分支谱系不可验证，拒绝写入影子记忆") from exc

    @staticmethod
    def _valid_source_revision(
        session: Any,
        *,
        world_id: str,
        source_turn_id: str,
        current_cutoff: int,
    ) -> int:
        revision = _completed_turn_revision(session, world_id, source_turn_id)
        if revision is None:
            raise ValueError("source turn 未完整提交或缺少有效 world_revision")
        if revision > current_cutoff:
            raise ValueError("source turn 超出当前世界 revision，拒绝写入影子记忆")
        return revision

    @staticmethod
    def _assert_owner_exists(session: Any, owner_user_id: str | None) -> None:
        if owner_user_id is not None and session.get(User, owner_user_id) is None:
            raise ValueError("owner_user_id 不存在")

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
        clean_world_id = _clean_text(
            world_id,
            field="world_id",
            maximum=MAX_WORLD_ID_LENGTH,
        )
        clean_turn_id = _clean_text(
            source_turn_id,
            field="source_turn_id",
            maximum=MAX_TURN_ID_LENGTH,
        )
        (
            clean_subject_id,
            clean_subject_kind,
            clean_fact_type,
            clean_audience,
            clean_tier,
        ) = self._normalise_fact_fields(
            subject_id=subject_id,
            fact_type=fact_type,
            audience=audience,
            tier=tier,
            subject_kind=subject_kind,
        )
        clean_owner_user_id = self._normalise_owner(clean_audience, owner_user_id)
        clean_value, _ = _canonical_json(
            value,
            field="value",
            maximum_bytes=MAX_FACT_VALUE_BYTES,
        )
        clean_provenance = _normalise_provenance(provenance, require_non_empty=False)
        digest = fact_digest(clean_subject_id, clean_fact_type, clean_value)
        try:
            with session_scope(self.url) as session:
                self._lock_world(session, clean_world_id)
                root, lineage = self._lineage_for_write(session, clean_world_id)
                self._valid_source_revision(
                    session,
                    world_id=clean_world_id,
                    source_turn_id=clean_turn_id,
                    current_cutoff=lineage[-1].cutoff_revision,
                )
                self._assert_owner_exists(session, clean_owner_user_id)
                existing = session.scalar(
                    select(MemoryFactCandidate)
                    .where(
                        MemoryFactCandidate.world_id == clean_world_id,
                        MemoryFactCandidate.source_turn_id == clean_turn_id,
                        MemoryFactCandidate.subject_id == clean_subject_id,
                        MemoryFactCandidate.fact_type == clean_fact_type,
                        MemoryFactCandidate.digest == digest,
                    )
                    .with_for_update()
                    .limit(1)
                )
                if existing is not None:
                    return existing.id
                candidate = MemoryFactCandidate(
                    id=new_id("memcand"),
                    world_id=clean_world_id,
                    root_world_id=root,
                    source_turn_id=clean_turn_id,
                    subject_id=clean_subject_id,
                    subject_kind=clean_subject_kind,
                    fact_type=clean_fact_type,
                    value=clean_value,
                    digest=digest,
                    audience=clean_audience,
                    owner_user_id=clean_owner_user_id,
                    tier=clean_tier,
                    provenance=clean_provenance,
                    status=PROPOSED_STATUS,
                )
                session.add(candidate)
                session.flush()
                return candidate.id
        except IntegrityError as exc:
            # PostgreSQL's world-row lock normally serializes contenders, but
            # SQLite intentionally has no ``FOR UPDATE`` and an external
            # writer can still win the unique candidate race.  Treat *only*
            # the documented five-column dedupe constraint as idempotent;
            # any FK, check, different unique constraint, or database error
            # remains visible to the caller instead of being misreported as a
            # successful proposal.
            if not _is_candidate_dedupe_conflict(exc):
                raise
            with session_scope(self.url) as session:
                winner = session.scalar(
                    select(MemoryFactCandidate)
                    .where(
                        MemoryFactCandidate.world_id == clean_world_id,
                        MemoryFactCandidate.source_turn_id == clean_turn_id,
                        MemoryFactCandidate.subject_id == clean_subject_id,
                        MemoryFactCandidate.fact_type == clean_fact_type,
                        MemoryFactCandidate.digest == digest,
                    )
                    .limit(1)
                )
            if winner is not None:
                return winner.id
            # The exact winner must be durable by the time the uniqueness
            # failure is observed.  Do not turn an unexpected rollback/deleted
            # row into a successful idempotent response.
            raise

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
        clean_candidate_id = _clean_text(
            candidate_id,
            field="candidate_id",
            maximum=MAX_USER_ID_LENGTH,
        )
        with session_scope(self.url) as session:
            # Acquire the world lock before the candidate row.  ``propose``
            # already takes that order, so retries cannot deadlock by each
            # holding one side of the same world/candidate pair.
            candidate_hint = session.get(MemoryFactCandidate, clean_candidate_id)
            if candidate_hint is None:
                raise ValueError("candidate 不存在")
            self._lock_world(session, candidate_hint.world_id)
            candidate = session.scalar(
                select(MemoryFactCandidate)
                .where(MemoryFactCandidate.id == clean_candidate_id)
                .with_for_update()
                .limit(1)
            )
            if candidate is None:
                raise ValueError("candidate 不存在")
            if source_turn_id is None:
                raise ValueError("source_turn_id 与候选不匹配")
            clean_turn_id = _clean_text(
                source_turn_id,
                field="source_turn_id",
                maximum=MAX_TURN_ID_LENGTH,
            )
            if clean_turn_id != candidate.source_turn_id:
                raise ValueError("source_turn_id 与候选不匹配")
            clean_provenance = _normalise_provenance(provenance, require_non_empty=True)

            (
                clean_subject_id,
                clean_subject_kind,
                clean_fact_type,
                clean_audience,
                clean_tier,
            ) = self._normalise_fact_fields(
                subject_id=candidate.subject_id,
                subject_kind=candidate.subject_kind,
                fact_type=candidate.fact_type,
                audience=candidate.audience,
                tier=candidate.tier,
            )
            clean_owner_user_id = self._normalise_owner(
                clean_audience,
                candidate.owner_user_id,
            )
            clean_value, _ = _canonical_json(
                candidate.value,
                field="candidate.value",
                maximum_bytes=MAX_FACT_VALUE_BYTES,
            )
            expected_digest = fact_digest(clean_subject_id, clean_fact_type, clean_value)
            if (
                candidate.subject_id != clean_subject_id
                or candidate.subject_kind != clean_subject_kind
                or candidate.fact_type != clean_fact_type
                or candidate.owner_user_id != clean_owner_user_id
                or candidate.digest != expected_digest
            ):
                raise ValueError("candidate 内容或 digest 不可验证")

            root, lineage = self._lineage_for_write(session, candidate.world_id)
            if candidate.root_world_id != root:
                raise ValueError("candidate root 与当前分支谱系不匹配")
            self._valid_source_revision(
                session,
                world_id=candidate.world_id,
                source_turn_id=clean_turn_id,
                current_cutoff=lineage[-1].cutoff_revision,
            )
            self._assert_owner_exists(session, clean_owner_user_id)
            existing = session.scalar(
                select(MemoryFact)
                .where(
                    MemoryFact.world_id == candidate.world_id,
                    MemoryFact.subject_id == clean_subject_id,
                    MemoryFact.fact_type == clean_fact_type,
                    MemoryFact.digest == expected_digest,
                )
                .with_for_update()
                .limit(1)
            )
            if candidate.status == ACCEPTED_STATUS:
                if existing is not None:
                    return existing.id
                raise ValueError("candidate 已接受但对应 fact 不存在")
            if candidate.status != PROPOSED_STATUS:
                raise ValueError(f"candidate 已处理: {candidate.status}")
            if existing is not None:
                candidate.status = ACCEPTED_STATUS
                return existing.id

            current = session.scalar(
                select(MemoryFact)
                .where(
                    MemoryFact.world_id == candidate.world_id,
                    MemoryFact.subject_id == clean_subject_id,
                    MemoryFact.fact_type == clean_fact_type,
                    MemoryFact.status == ACCEPTED_STATUS,
                )
                .with_for_update()
                .limit(1)
            )
            revision = 1
            supersedes_id = None
            if current is not None:
                current_revision = _strict_revision(current.revision)
                if current_revision is None or current_revision < 1:
                    raise ValueError("current fact revision 不可验证")
                revision = current_revision + 1
                supersedes_id = current.id
                current.status = SUPERSEDED_STATUS

            fact = MemoryFact(
                id=new_id("memfact"),
                world_id=candidate.world_id,
                root_world_id=candidate.root_world_id,
                source_turn_id=candidate.source_turn_id,
                subject_id=clean_subject_id,
                subject_kind=clean_subject_kind,
                fact_type=clean_fact_type,
                value=clean_value,
                digest=expected_digest,
                audience=clean_audience,
                owner_user_id=clean_owner_user_id,
                tier=clean_tier,
                provenance=clean_provenance,
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

    @staticmethod
    def _validated_fact_for_recall(fact: MemoryFact) -> dict[str, Any]:
        """Validate an existing row before a shadow reader can use it.

        Tables are durable state and can outlive a buggy import, manual repair,
        or a prior application version.  Retrieval therefore treats persisted
        rows as untrusted input too; it never lets a malformed row become a
        shortcut around the service's normal proposal/acceptance validation.
        """
        (
            subject_id,
            subject_kind,
            fact_type,
            audience,
            tier,
        ) = StructuredMemoryService._normalise_fact_fields(
            subject_id=fact.subject_id,
            subject_kind=fact.subject_kind,
            fact_type=fact.fact_type,
            audience=fact.audience,
            tier=fact.tier,
        )
        owner_user_id = StructuredMemoryService._normalise_owner(
            audience,
            fact.owner_user_id,
        )
        value, _ = _canonical_json(
            fact.value,
            field="fact.value",
            maximum_bytes=MAX_FACT_VALUE_BYTES,
        )
        source_turn_id = _clean_text(
            fact.source_turn_id,
            field="fact.source_turn_id",
            maximum=MAX_TURN_ID_LENGTH,
        )
        fact_revision = _strict_revision(fact.revision)
        accepted_at = _accepted_at_utc(fact.decided_at)
        expected_digest = fact_digest(subject_id, fact_type, value)
        if (
            fact.subject_id != subject_id
            or fact.subject_kind != subject_kind
            or fact.fact_type != fact_type
            or fact.owner_user_id != owner_user_id
            or fact.digest != expected_digest
            or fact_revision is None
            or fact_revision < 1
        ):
            raise ValueError("memory fact 内容或 digest 不可验证")
        return {
            "fact": fact,
            "subject_id": subject_id,
            "subject_kind": subject_kind,
            "fact_type": fact_type,
            "audience": audience,
            "owner_user_id": owner_user_id,
            "tier": tier,
            "value": value,
            "source_turn_id": source_turn_id,
            "fact_revision": fact_revision,
            "accepted_at": accepted_at,
        }

    @staticmethod
    def _fact_rank(entry: dict[str, Any]) -> tuple[int, int, str, str]:
        fact = entry["fact"]
        return (
            int(entry["source_revision"]),
            int(entry["fact_revision"]),
            entry["accepted_at"].isoformat(),
            str(fact.id),
        )

    def retrieve(
        self,
        *,
        world_id: str,
        owner_user_id: str | None = None,
        internal: bool = False,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
    ) -> dict[str, Any]:
        """Shadow recall: references + gate diagnostics, never injection.

        Recalled facts pass every gate (tree, temporal branch lineage,
        audience, tier).  The public/non-internal result intentionally does
        *not* report blocked fact identifiers or metadata: knowing that an
        owner-only/private/sibling fact exists is itself information.  Trusted
        internal diagnostics retain bounded gate reasons.  No ``WorldState``
        mutation happens anywhere in this path.
        """
        clean_world_id = _clean_text(
            world_id,
            field="world_id",
            maximum=MAX_WORLD_ID_LENGTH,
        )
        clean_owner_user_id = (
            _clean_text(
                owner_user_id,
                field="owner_user_id",
                maximum=MAX_USER_ID_LENGTH,
            )
            if owner_user_id is not None
            else None
        )
        if not isinstance(internal, bool):
            raise ValueError("internal 必须是布尔值")
        result_limit = _bounded_retrieval_limit(limit)
        with session_scope(self.url) as session:
            worlds_by_id = {w.id: w for w in session.query(World).all()}
            world = worlds_by_id.get(clean_world_id)
            if world is None:
                raise ValueError(f"world 不存在: {clean_world_id}")
            blocked: list[dict[str, Any]] = []

            def add_blocked(fact: MemoryFact | None, reason: str) -> None:
                if not internal or len(blocked) >= MAX_BLOCKED_DIAGNOSTICS:
                    return
                item: dict[str, Any] = {"reason": reason}
                if fact is not None:
                    # These fields are intentionally internal-only.  Keeping
                    # the public shape empty avoids existence side channels.
                    item.update(
                        {
                            "fact_id": fact.id,
                            "subject_id": fact.subject_id,
                            "fact_type": fact.fact_type,
                        }
                    )
                blocked.append(item)

            try:
                root, lineage = _strict_lineage(session, clean_world_id, worlds_by_id)
            except _LineageUnavailable:
                add_blocked(None, "invalid_lineage")
                return {
                    "root_world_id": "",
                    "recalled": [],
                    "blocked": blocked,
                }

            source_revisions: dict[tuple[str, str], int | None] = {}

            def source_revision_for(entry: dict[str, Any]) -> int | None:
                fact = entry["fact"]
                key = (fact.world_id, entry["source_turn_id"])
                if key not in source_revisions:
                    source_revisions[key] = _completed_turn_revision(session, *key)
                return source_revisions[key]

            scanned = 0
            selected_by_world: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {}
            # Scan the target first so a large root cannot starve a branch's
            # own facts.  The global cap is deterministic and fail-closed:
            # omitted rows are never substituted with less safe data.
            for node in reversed(lineage):
                remaining = MAX_RETRIEVAL_SCAN - scanned
                if remaining <= 0:
                    add_blocked(None, "scan_budget_exhausted")
                    break
                rows = session.scalars(
                    select(MemoryFact)
                    .where(
                        MemoryFact.world_id == node.world_id,
                        MemoryFact.root_world_id == root,
                        MemoryFact.status.in_((ACCEPTED_STATUS, SUPERSEDED_STATUS)),
                    )
                    .order_by(
                        MemoryFact.revision.desc(),
                        MemoryFact.decided_at.desc(),
                        MemoryFact.id.desc(),
                    )
                    .limit(remaining + 1)
                ).all()
                if len(rows) > remaining:
                    rows = rows[:remaining]
                    add_blocked(None, "scan_budget_exhausted")
                scanned += len(rows)
                visible_at_node: dict[tuple[str, str, str], dict[str, Any]] = {}
                for fact in rows:
                    try:
                        entry = self._validated_fact_for_recall(fact)
                    except ValueError:
                        add_blocked(fact, "invalid_fact")
                        continue
                    source_revision = source_revision_for(entry)
                    if source_revision is None:
                        add_blocked(fact, "invalid_source_turn")
                        continue
                    if source_revision > node.cutoff_revision:
                        add_blocked(fact, "after_branch_cutoff")
                        continue
                    if (
                        node.accepted_cutoff_at is not None
                        and entry["accepted_at"] > node.accepted_cutoff_at
                    ):
                        add_blocked(fact, "after_branch_acceptance_cutoff")
                        continue
                    entry["source_revision"] = source_revision
                    key = (
                        entry["subject_kind"],
                        entry["subject_id"],
                        entry["fact_type"],
                    )
                    previous = visible_at_node.get(key)
                    if previous is None or self._fact_rank(entry) > self._fact_rank(previous):
                        visible_at_node[key] = entry
                selected_by_world[node.world_id] = visible_at_node

            # A more specific branch replaces the same subject/fact type from
            # an ancestor.  This preserves ordinary state evolution while the
            # per-node selection above restores a superseded ancestor version
            # when its replacement happened after a child's fork cutoff.
            effective: dict[tuple[str, str, str], dict[str, Any]] = {}
            for node in lineage:
                effective.update(selected_by_world.get(node.world_id, {}))

            recalled: list[tuple[dict[str, Any], int]] = []
            revealed_levels: dict[tuple[str, str], int | None] = {}
            for entry in effective.values():
                fact = entry["fact"]
                reason = self._audience_block(
                    fact,
                    internal=internal,
                    owner_user_id=clean_owner_user_id,
                )
                if reason is None and entry["tier"] is not None:
                    tier_key = (entry["subject_kind"], entry["subject_id"])
                    if tier_key not in revealed_levels:
                        revealed_levels[tier_key] = _revealed_level(
                            session,
                            clean_world_id,
                            entry["subject_id"],
                            entry["subject_kind"],
                        )
                    level = revealed_levels[tier_key]
                    if level is None:
                        reason = "tier_on_non_npc"
                    elif level < entry["tier"]:
                        reason = f"tier_gate:{entry['tier']}>{level}"
                if reason is not None:
                    add_blocked(fact, reason)
                    continue
                rendered = {
                    "fact_id": fact.id,
                    "world_id": fact.world_id,
                    "subject_id": entry["subject_id"],
                    "subject_kind": entry["subject_kind"],
                    "fact_type": entry["fact_type"],
                    "value": copy.deepcopy(entry["value"]),
                    "audience": entry["audience"],
                    "owner_user_id": entry["owner_user_id"],
                    "tier": entry["tier"],
                    "revision": entry["fact_revision"],
                }
                # Value size was checked when the row was read; calculating
                # the whole rendered record gives the response a real global
                # byte cap too (not merely a cap on number of facts).
                rendered_bytes = len(
                    json.dumps(
                        rendered,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                )
                recalled.append((rendered, rendered_bytes))

            recalled.sort(
                key=lambda pair: (
                    pair[0]["subject_kind"],
                    pair[0]["subject_id"],
                    pair[0]["fact_type"],
                    pair[0]["world_id"],
                    pair[0]["revision"],
                    pair[0]["fact_id"],
                )
            )
            bounded_recalled: list[dict[str, Any]] = []
            response_bytes = 0
            for item, item_bytes in recalled:
                if len(bounded_recalled) >= result_limit:
                    break
                if item_bytes > MAX_RETRIEVAL_RESPONSE_BYTES - response_bytes:
                    # A diagnostic retriever must never grow a response past
                    # its bounded budget.  Omission is safer than truncating
                    # JSON/fact values or exposing a partially valid fact.
                    continue
                bounded_recalled.append(item)
                response_bytes += item_bytes
            return {
                "root_world_id": root,
                "recalled": bounded_recalled,
                "blocked": blocked,
            }
