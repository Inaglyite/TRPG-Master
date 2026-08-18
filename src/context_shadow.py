"""GameEngine → ContextEventStore/ContextCheckpoint 影子双写协调器。

职责（全部 fail-safe，绝不打断权威游戏回合）：

- **绑定**：首次绑定旧世界只 ``seed_legacy`` 一次（幂等，由 store 的
  ``seed_digest`` 标记保证）；``reset`` 用 ``begin_fresh_epoch``；带
  checkpoint 的读档用 ``resume_from``；无 checkpoint 的旧存档建立新 root
  seed；``switch_context`` / ``adopt_message_history`` /
  ``restore_latest_committed_history`` 重新绑定。
- **每请求**：把当前 ``engine.messages`` 与 active turn 同步（
  ``sync_messages``，幂等），记录 digest-only 的 request envelope，并用绑定
  ``request_id`` 的 ``model_private`` patch 事件保存
  ``system_overlay`` / ``system_prompt_override`` / ``messages_override``
  造成的有效请求差异（实际内容，不只是 digest）。
- **最终**：回合提交前 final sync，构造 ``ContextCheckpoint`` 交给
  ``TurnJournal.complete``（DB journal 会一并合并进自动 slot_000）；
  手动 save 同样携带当前 checkpoint。

权威读取仍以 ``engine.messages`` 为准：``TRPG_CONTEXT_EVENT_READ`` 默认
关闭，本模块从不把投影当作读取源。任何事件写入 / 投影 / checkpoint 失败
只写入内部诊断（``diagnostics``，绝不含 payload），调用方不会收到异常。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .context_checkpoint import ContextCheckpoint
from .context_events import (
    EVENT_REQUEST_PATCH,
    ContextEventStore,
    messages_digest,
    shadow_writes_enabled,
)

_MAX_DIAGNOSTICS = 50


@dataclass(frozen=True)
class _VerifiedSurface:
    session_id: str
    session_epoch: int
    sequence: int
    digest: str


class ContextShadowCoordinator:
    """One world's H2 context shadow bridge attached to a GameEngine."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.world_id = str(getattr(context, "world_id", "") or "")
        database_url = getattr(context, "database_url", None)
        self.store = ContextEventStore(database_url) if database_url else None
        self.diagnostics: list[dict[str, Any]] = []

    # -- state ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Shadow double-write is on by default; disabled without a database."""
        return shadow_writes_enabled() and self.store is not None and bool(self.world_id)

    def _diagnose(self, operation: str, error: Exception | str) -> None:
        """Record a metadata-only internal diagnostic (never the payload)."""
        self.diagnostics.append(
            {
                "operation": operation,
                "error": type(error).__name__ if isinstance(error, Exception) else error,
            }
        )
        if len(self.diagnostics) > _MAX_DIAGNOSTICS:
            self.diagnostics = self.diagnostics[-_MAX_DIAGNOSTICS:]

    def _session(self) -> dict[str, Any] | None:
        try:
            return self.store.session_for_world(self.world_id)
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("session_for_world", exc)
            return None

    def _sync(
        self,
        messages: list[dict],
        turn_id: str | None,
    ) -> _VerifiedSurface | None:
        """Append a delta and prove the projected surface still matches.

        ``mismatch`` is fail-open for gameplay but fail-closed for checkpoint
        authority: callers receive ``None`` and may not sign a checkpoint.
        """
        session = self._session()
        if session is None:
            return None
        try:
            status, _sequences = self.store.sync_messages(
                str(session["id"]),
                messages,
                turn_id=turn_id,
            )
            if status == "mismatch":
                self._diagnose("sync_messages", "surface_mismatch")
                return None
            current = self.store.session_for_world(self.world_id)
            if current is None or str(current["id"]) != str(session["id"]):
                self._diagnose("sync_messages", "session_changed")
                return None
            projected = self.store.project(
                str(current["id"]),
                include_turn_id=turn_id,
            )
            projected_digest = messages_digest(projected)
            current_digest = messages_digest(messages)
            if projected_digest != current_digest:
                self._diagnose("sync_messages", "projection_mismatch")
                return None
            return _VerifiedSurface(
                session_id=str(current["id"]),
                session_epoch=int(current["session_epoch"]),
                sequence=int(current["head_sequence"]),
                digest=current_digest,
            )
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("sync_messages", exc)
            return None

    # -- lifecycle binding ------------------------------------------------

    def ensure_bound(self, messages: list[dict]) -> bool:
        """Make sure the world has an active session; seed legacy once.

        ``seed_legacy`` is idempotent on the same digest (the store refuses a
        different digest), so repeated binds never double-import.
        """
        if not self.enabled:
            return False
        try:
            session = self.store.session_for_world(self.world_id)
            if session is not None:
                return True
            if messages:
                self.store.seed_legacy(self.world_id, messages)
            else:
                self.store.ensure_session(self.world_id)
            return True
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("ensure_bound", exc)
            return False

    def ensure_turn_bound(
        self,
        messages: list[dict],
        turn_id: str | None,
        seed_messages: list[dict] | None = None,
    ) -> bool:
        """Bind a first in-flight request without making it legacy-visible.

        A fresh world's first model call already contains the player's active
        action.  Only the surface captured before ``TurnJournal.begin`` may be
        seeded without a turn id; the active delta is appended by ``_sync`` and
        will therefore disappear if the turn fails or is cancelled.
        """
        seed = seed_messages if turn_id and seed_messages is not None else messages
        return self.ensure_bound(seed)

    def bind_legacy(self, messages: list[dict]) -> bool:
        """Bind an old save without a checkpoint: establish a fresh root seed.

        The current active session (if any) is closed and a brand-new
        parentless root session is created, then the save history is imported
        exactly once as its seed.
        """
        if not self.enabled:
            return False
        try:
            self.store.begin_fresh_epoch(self.world_id)
            self.store.seed_legacy(self.world_id, messages)
            return True
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("bind_legacy", exc)
            return False

    def rebind(self, messages: list[dict]) -> bool:
        """Re-attach after the message history or world changed.

        Used by ``switch_context`` / ``adopt_message_history`` /
        ``restore_latest_committed_history``: reuse the active session when
        one exists (delta-sync), otherwise seed the history once.
        """
        if not self.enabled:
            return False
        self.ensure_bound(messages)
        return self._sync(messages, None) is not None

    def reset_session(self) -> bool:
        """Start a brand-new game: close the active session, open a fresh
        parentless epoch (old events stay but are never inherited)."""
        if not self.enabled:
            return False
        try:
            self.store.begin_fresh_epoch(self.world_id)
            return True
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("reset_session", exc)
            return False

    def resume(self, checkpoint: ContextCheckpoint, messages: list[dict]) -> bool:
        """Load a save that carries a checkpoint: resume from that cutoff.

        Falls back to a fresh root seed when the referenced session is gone
        or the resume fails (shadow side only; the authoritative load is
        unaffected).
        """
        if not self.enabled:
            return False
        if messages_digest(messages) != checkpoint.surface_digest:
            self._diagnose("resume", "checkpoint_surface_mismatch")
            return self.bind_legacy(messages)
        try:
            active = self.store.session_for_world(self.world_id)
            if (
                active is not None
                and str(active["id"]) == checkpoint.session_id
                and int(active["head_sequence"]) == checkpoint.sequence
                and self._sync(messages, None) is not None
            ):
                return True
            self.store.resume_from(
                self.world_id,
                checkpoint.session_id,
                int(checkpoint.sequence),
            )
            if self._sync(messages, None) is not None:
                return True
            self._diagnose("resume", "resumed_projection_mismatch")
            return self.bind_legacy(messages)
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("resume", exc)
            return self.bind_legacy(messages)

    # -- per-request shadow ------------------------------------------------

    def sync_turn(
        self,
        messages: list[dict],
        turn_id: str | None,
        seed_messages: list[dict] | None = None,
    ) -> bool:
        """Sync current messages with the in-flight turn (idempotent)."""
        if not self.enabled:
            return False
        self.ensure_turn_bound(messages, turn_id, seed_messages)
        return self._sync(messages, turn_id) is not None

    def record_request(
        self,
        prepared: Any,
        *,
        base_messages: list[dict],
        turn_id: str | None = None,
        seed_messages: list[dict] | None = None,
        system_overlay: str | None = None,
        system_prompt_override: str | None = None,
        messages_override: list[dict] | None = None,
    ) -> bool:
        """Record one prepared model request: digest envelope + private patch.

        The patch event is bound to ``request_id`` and stores the *actual*
        effective-request differences caused by ``system_overlay`` /
        ``system_prompt_override`` / ``messages_override`` (their content,
        not just digests).  It is ``model_private``/``private`` so it never
        surfaces in projections or public history.
        """
        if not self.enabled:
            return False
        self.ensure_turn_bound(base_messages, turn_id, seed_messages)
        verified = self._sync(base_messages, turn_id)
        session = self._session()
        if session is None:
            return False
        if verified is not None and str(session["id"]) != verified.session_id:
            self._diagnose("record_request", "session_changed")
            verified = None
        envelope = dict(getattr(prepared, "request_envelope", None) or {})
        request_id = str(envelope.get("request_id") or "")
        if not request_id:
            return False
        step = envelope.get("step")
        try:
            self.store.record_request_envelope(
                str(session["id"]),
                prepared=prepared,
                turn_id=turn_id,
            )
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("record_request_envelope", exc)
        effective_messages = [
            dict(message) for message in (getattr(prepared, "messages", None) or [])
        ]
        normalized_base = self.store._normalize_messages(base_messages)
        normalized_effective = self.store._normalize_messages(effective_messages)
        patch: dict[str, Any] = {
            "request_id": request_id,
            "step": step,
            "model": str(envelope.get("model") or ""),
            "turn_id": turn_id,
            "base_sequence": verified.sequence if verified is not None else None,
            "base_surface_digest": messages_digest(normalized_base),
            "effective_surface_digest": messages_digest(normalized_effective),
        }
        differing = [
            index
            for index, (base, effective) in enumerate(
                zip(normalized_base, normalized_effective, strict=False)
            )
            if base != effective
        ]
        system_only = (
            verified is not None
            and len(normalized_base) == len(normalized_effective)
            and differing
            and all(
                normalized_base[index].get("role") == "system"
                and normalized_effective[index].get("role") == "system"
                for index in differing
            )
        )
        if verified is not None and normalized_base == normalized_effective:
            patch["mode"] = "identity"
        elif system_only:
            patch["mode"] = "replace_indices"
            patch["replacements"] = [
                {"index": index, "message": copy.deepcopy(normalized_effective[index])}
                for index in differing
            ]
        else:
            # A failed base sync or a true messages_override cannot be rebuilt
            # from the append-only surface.  Persist the exact normalized
            # effective request privately rather than pretending a digest is
            # sufficient evidence.
            patch["mode"] = "replace_all"
            patch["messages"] = copy.deepcopy(normalized_effective)
        try:
            self.store.append(
                str(session["id"]),
                event_type=EVENT_REQUEST_PATCH,
                payload=patch,
                turn_id=turn_id,
                step=int(step) if isinstance(step, int) else None,
                source_kind="model_request",
                source_id=request_id,
                source_version="1",
                audience="model_private",
                sensitivity="private",
            )
            return True
        except Exception as exc:  # noqa: BLE001 - fail-safe
            self._diagnose("record_request_patch", exc)
            return False

    # -- checkpoint --------------------------------------------------------

    def _build_checkpoint(
        self,
        verified: _VerifiedSurface | None,
        source_turn_id: str | None,
    ) -> ContextCheckpoint | None:
        if verified is None:
            return None
        return ContextCheckpoint(
            session_id=verified.session_id,
            session_epoch=verified.session_epoch,
            sequence=verified.sequence,
            surface_digest=verified.digest,
            source_turn_id=source_turn_id,
        )

    def finalize_turn(
        self,
        messages: list[dict],
        turn_id: str,
        seed_messages: list[dict] | None = None,
    ) -> ContextCheckpoint | None:
        """Final sync before commit, then build the turn's checkpoint."""
        if not self.enabled:
            return None
        self.ensure_turn_bound(messages, turn_id, seed_messages)
        return self._build_checkpoint(self._sync(messages, turn_id), turn_id)

    def current_checkpoint(
        self,
        messages: list[dict],
        source_turn_id: str | None = None,
    ) -> ContextCheckpoint | None:
        """Checkpoint for a manual save (idempotent delta sync included)."""
        if not self.enabled:
            return None
        self.ensure_bound(messages)
        return self._build_checkpoint(
            self._sync(messages, source_turn_id),
            source_turn_id,
        )


# ---------------------------------------------------------------------------
# Thin GameEngine adapter functions.  Keeping these here prevents the legacy
# engine/model-streamer adapters from growing another cross-cutting subsystem.
# Every entry point is fail-open and records metadata only.
# ---------------------------------------------------------------------------


def _remember_outer_failure(engine: Any, operation: str, exc: Exception) -> None:
    failures = engine.__dict__.setdefault("_context_shadow_failures", [])
    failures.append({"operation": operation, "error": type(exc).__name__})
    if len(failures) > _MAX_DIAGNOSTICS:
        del failures[:-_MAX_DIAGNOSTICS]


def for_engine(engine: Any) -> ContextShadowCoordinator | None:
    try:
        context = engine.context
        shadow = engine.__dict__.get("_context_shadow")
        if shadow is None or shadow.world_id != getattr(context, "world_id", None):
            shadow = ContextShadowCoordinator(context)
            engine.__dict__["_context_shadow"] = shadow
        return shadow
    except Exception as exc:  # noqa: BLE001 - shadow cannot break the engine
        _remember_outer_failure(engine, "for_engine", exc)
        return None


def forget_engine(engine: Any) -> None:
    engine.__dict__.pop("_context_shadow", None)


def rebase_engine(engine: Any) -> None:
    shadow = for_engine(engine)
    if shadow is not None:
        shadow.bind_legacy(list(engine.messages))


def adopt_engine(engine: Any, checkpoint_data: Any = None) -> None:
    shadow = for_engine(engine)
    if shadow is None:
        return
    if isinstance(checkpoint_data, dict):
        try:
            checkpoint = ContextCheckpoint.from_mapping(checkpoint_data)
        except (TypeError, ValueError) as exc:
            shadow._diagnose("adopt_checkpoint", exc)
        else:
            shadow.resume(checkpoint, list(engine.messages))
            return
    shadow.bind_legacy(list(engine.messages))


def finalize_engine_turn(engine: Any, turn_id: str) -> ContextCheckpoint | None:
    shadow = for_engine(engine)
    seed = engine.__dict__.get("_turn_context_surface")
    if shadow is None:
        return None
    try:
        return shadow.finalize_turn(list(engine.messages), turn_id, seed)
    except Exception as exc:  # noqa: BLE001 - authoritative commit must continue
        _remember_outer_failure(engine, "finalize_engine_turn", exc)
        return None


def begin_fresh_engine_session(engine: Any) -> None:
    shadow = for_engine(engine)
    if shadow is not None:
        shadow.reset_session()


def engine_checkpoint(engine: Any) -> ContextCheckpoint | None:
    shadow = for_engine(engine)
    if shadow is None:
        return None
    try:
        return shadow.current_checkpoint(list(engine.messages))
    except Exception as exc:  # noqa: BLE001 - authoritative save must continue
        _remember_outer_failure(engine, "engine_checkpoint", exc)
        return None


def save_engine(engine: Any, slot_id: str | None) -> str:
    from .persistence import save_game

    return save_game(
        engine.messages,
        slot_id,
        context=engine.context,
        checkpoint=engine_checkpoint(engine),
    )


def rebind_loaded_engine(engine: Any, metadata: Any) -> None:
    shadow = for_engine(engine)
    if shadow is None:
        return
    context_data = metadata.get("context") if isinstance(metadata, dict) else None
    if isinstance(context_data, dict):
        try:
            checkpoint = ContextCheckpoint.from_mapping(context_data)
        except (TypeError, ValueError) as exc:
            shadow._diagnose("load_checkpoint", exc)
            shadow.bind_legacy(list(engine.messages))
            return
        shadow.resume(checkpoint, list(engine.messages))
    else:
        shadow.bind_legacy(list(engine.messages))


def record_prepared_request(
    engine: Any,
    prepared: Any,
) -> None:
    shadow = for_engine(engine)
    if shadow is None:
        return
    try:
        shadow.record_request(
            prepared,
            base_messages=list(engine.messages),
            turn_id=getattr(engine, "_active_turn_id", None),
            seed_messages=engine.__dict__.get("_turn_context_surface"),
        )
    except Exception as exc:  # noqa: BLE001 - model requests must remain authoritative
        _remember_outer_failure(engine, "record_prepared_request", exc)


def capture_turn_surface(engine: Any) -> None:
    try:
        engine.__dict__["_turn_context_surface"] = copy.deepcopy(list(engine.messages))
    except Exception as exc:  # noqa: BLE001 - journal begin must remain authoritative
        engine.__dict__.pop("_turn_context_surface", None)
        _remember_outer_failure(engine, "capture_turn_surface", exc)


def commit_turn_surface(engine: Any) -> None:
    engine.__dict__.pop("_turn_context_surface", None)


def rollback_turn_surface(engine: Any) -> None:
    previous = engine.__dict__.pop("_turn_context_surface", None)
    if previous is not None:
        engine.messages = previous
