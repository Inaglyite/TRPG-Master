"""Private investigator-state revocation after multiplayer membership changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.gameplay.investigators import (
    reconcile_investigator_roster,
    release_investigator_controller,
)
from src.storage.database import WorldInvestigator, WorldMember, session_scope
from src.storage.database_store import DatabaseWorldStore


def authoritative_world_roster(database_url: str, world_id: str) -> list[dict]:
    """Return only currently claimed investigators owned by playable members."""
    with session_scope(database_url) as session:
        playable_members = {
            row.user_id
            for row in session.query(WorldMember).filter_by(world_id=world_id).all()
            if row.role in {"owner", "player"}
        }
        claims = (
            session.query(WorldInvestigator)
            .filter_by(world_id=world_id, status="claimed")
            .order_by(WorldInvestigator.created_at, WorldInvestigator.id)
            .all()
        )
        return [
            {
                "investigator_id": claim.id,
                "user_id": claim.controller_user_id,
                "character_ref": dict(claim.character_ref or {}),
            }
            for claim in claims
            if claim.controller_user_id in playable_members
        ]


def reconcile_world_investigator_roster(
    database_url: str,
    context: Any,
    world_id: str,
    *,
    preferred_user_id: str | None = None,
) -> dict[str, str]:
    """Apply current DB ownership after loading snapshot-owned character state."""
    roster = authoritative_world_roster(database_url, world_id)
    preferred_investigator_id = next(
        (
            str(entry["investigator_id"])
            for entry in roster
            if str(entry.get("user_id") or "") == str(preferred_user_id or "")
        ),
        None,
    )
    return reconcile_investigator_roster(
        context,
        roster,
        preferred_investigator_id=preferred_investigator_id,
    )


def release_world_controller(
    database_url: str,
    runtime_root: Path,
    world_id: str,
    user_id: str,
    room: Any | None,
) -> None:
    """Clear a former player's JSON/JSONB controller projection."""
    context = (
        room.engine.context
        if room is not None
        else SimpleNamespace(
            world_store=DatabaseWorldStore(
                database_url,
                world_id,
                runtime_root / "worlds" / world_id,
            )
        )
    )
    release_investigator_controller(context, user_id)
