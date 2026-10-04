"""Read-only, recipient-filtered narrative history from committed outbox rows.

Finished chat and authorized declarations are replayed: never commands,
memories, grants or dice. Declarations are not execution results.
Pages use the world sequence rather than offsets so new messages cannot shift
the next page. This is recovery, not another model call or gameplay action.
"""

from __future__ import annotations

import copy

from sqlalchemy import func, or_, select

from src.gameplay.investigators import investigator_entity
from src.storage.database import EventOutbox, PlayerRequest

PAGE_SIZE = 50


def visible_message_history(session, world_id, principal, state, *, before_sequence=None):
    from .service import audience_visible

    found = []
    before = before_sequence
    # Select the earliest surviving acceptance across pages and failed retries.
    accepted = (
        select(func.min(EventOutbox.sequence))
        .where(EventOutbox.world_id == world_id, EventOutbox.event_type == "intent_pending")
        .group_by(EventOutbox.cause_request_id)
    )
    while len(found) <= PAGE_SIZE:
        query = select(EventOutbox).where(
            EventOutbox.world_id == world_id,
            or_(EventOutbox.event_type == "message_completed", EventOutbox.sequence.in_(accepted)),
        )
        if before is not None:
            query = query.where(EventOutbox.sequence < before)
        rows = (
            session.execute(query.order_by(EventOutbox.sequence.desc()).limit(100)).scalars().all()
        )
        if not rows:
            break
        for row in rows:
            entry_kind = None
            if row.event_type == "intent_pending":
                request = session.execute(
                    select(PlayerRequest).where(
                        PlayerRequest.world_id == world_id,
                        PlayerRequest.request_id == row.cause_request_id,
                        PlayerRequest.request_type == "action_request",
                    )
                ).scalar_one_or_none()
                if request is None:
                    continue
                own_request = principal.kind == "player" and (
                    (bool(principal.user_id) and request.submitted_by == principal.user_id)
                    or (
                        not principal.user_id
                        and request.submitted_by is None
                        and request.investigator_id in principal.investigator_ids
                    )
                )
                if principal.kind not in {"keeper", "agent"} and not own_request:
                    continue
                action = (request.payload or {}).get("action") or {}
                if not isinstance(action, dict) or action.get("kind") != "freeform":
                    continue
                payload = {
                    "speaker": {"kind": "investigator", "id": request.investigator_id},
                    "text": action.get("text"),
                    "message_id": f"action:{request.id}",
                }
                entry_kind = "action_request"
            else:
                if not isinstance(row.audience, dict) or not audience_visible(
                    row.audience, principal
                ):
                    continue
                payload = row.payload or {}
            speaker = payload.get("speaker")
            text = payload.get("text")
            key = payload.get("message_id")
            if (
                not isinstance(speaker, dict)
                or not isinstance(text, str)
                or not isinstance(key, str)
            ):
                continue
            kind = speaker.get("kind")
            if kind not in {"keeper", "npc", "investigator", "system"}:
                continue
            projected = {"kind": kind}
            speaker_id = str(speaker.get("id") or "")
            if speaker_id:
                projected["id"] = speaker_id
            name = "守秘人" if kind == "keeper" else "系统" if kind == "system" else "人物"
            if kind == "npc":
                npc = next(
                    (
                        npc
                        for npc in state.get("npcs", []) or []
                        if isinstance(npc, dict) and str(npc.get("id")) == speaker_id
                    ),
                    {},
                )
                name = str(npc.get("name") or "人物")
            elif kind == "investigator":
                name = str((investigator_entity(state, speaker_id) or {}).get("name") or "调查员")
            projected["name"] = name
            found.append(
                {
                    "message_id": key,
                    "sequence": int(row.sequence),
                    "speaker": projected,
                    "text": text,
                    **({"entry_kind": entry_kind} if entry_kind else {}),
                }
            )
            if len(found) > PAGE_SIZE:
                break
        before = int(rows[-1].sequence)
        if len(rows) < 100:
            break
    page = found[:PAGE_SIZE]
    return {
        "messages": copy.deepcopy(list(reversed(page))),
        "next_before_sequence": page[-1]["sequence"] if len(found) > PAGE_SIZE else None,
    }
