"""Freeze readable chat records for a branch, without copying transport events.

The caller must hold the source-state lock and supply its event boundary. The
result is private storage input, never a client payload: recipient constraints
must be rechecked when an archive is read in the target world.
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from src.storage.database import EventOutbox, PlayerRequest
from src.structured.message_history import visible_message_history
from src.structured.principal import Principal


def archive_entry_visible(entry: dict, principal: Principal) -> bool:
    """Re-evaluate a frozen entry against the target world's current identity."""
    from src.structured.service import audience_visible

    if principal.kind not in {"player", "keeper", "agent", "viewer"}:
        return False
    message = entry.get("message")
    audience = entry.get("audience")
    if not isinstance(message, dict) or not isinstance(audience, dict):
        return False
    if audience.get("kind") not in {"public", "keeper", "investigators"}:
        return False
    if message.get("entry_kind") == "action_request":
        if audience.get("kind") != "keeper":
            return False
        speaker = message.get("speaker")
        if not isinstance(speaker, dict) or speaker.get("kind") != "investigator":
            return False
        if principal.kind in {"keeper", "agent"}:
            return True
        if principal.kind != "player":
            return False
        submitted_by = entry.get("submitted_by")
        return bool(
            (principal.user_id and submitted_by == principal.user_id)
            or (
                not principal.user_id
                and submitted_by is None
                and speaker.get("id") in principal.investigator_ids
            )
        )
    if principal.kind == "viewer":
        return audience.get("kind") == "public"
    return audience_visible(audience, principal)


def capture_local_history(session, world_id: str, state: dict, sequence: int) -> list[dict]:
    """Capture complete entries up to an inclusive, already committed cursor.

    This is not a Keeper authorization bypass for HTTP clients. Branch creation
    uses the trusted internal projection to retain each original audience, not
    a creator-specific public copy. There are no commands or dice in this data.
    """
    pages = []
    before = sequence + 1
    while True:
        page = visible_message_history(
            session,
            world_id,
            Principal(kind="keeper"),
            state,
            before_sequence=before,
        )
        entries = page["messages"]
        if entries:
            events = {
                int(row.sequence): row
                for row in session.execute(
                    select(EventOutbox).where(
                        EventOutbox.world_id == world_id,
                        EventOutbox.sequence.in_([entry["sequence"] for entry in entries]),
                    )
                ).scalars()
            }
            captured = []
            for entry in entries:
                event = events.get(entry["sequence"])
                if event is None:
                    continue
                declaration = entry.get("entry_kind") == "action_request"
                submitted_by = None
                if declaration:
                    request = session.execute(
                        select(PlayerRequest).where(
                            PlayerRequest.world_id == world_id,
                            PlayerRequest.id == entry["message_id"].removeprefix("action:"),
                        )
                    ).scalar_one_or_none()
                    if request is None:
                        continue
                    submitted_by = request.submitted_by
                captured.append(
                    {
                        "source_key": f"{world_id}:{entry['message_id']}",
                        "message": copy.deepcopy(entry),
                        "audience": copy.deepcopy(event.audience),
                        "submitted_by": submitted_by,
                    }
                )
            pages.append(captured)
        before = page["next_before_sequence"]
        if before is None:
            break
    return [entry for page in reversed(pages) for entry in page]
