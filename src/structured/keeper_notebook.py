"""Private Markdown notes, stored with the world and changed by checked commands.

Notes are the keeper's recollection and plans, never rules, character knowledge,
or instructions. CAS prevents a late model from overwriting a human correction.
"""

from __future__ import annotations

import copy
import hashlib
import json

from sqlalchemy import select

from src.storage.database import EventOutbox, GameCommand

from .domains import CommandContext, CommandResult
from .errors import StructuredError

NOTE_TYPES = ("event", "promise", "open_question", "plan", "inference")
MAX_NOTES = 256


def record_keeper_note(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    from .validation import validate_command

    validate_command("record_keeper_note", payload)
    notes = copy.deepcopy(state.get("keeper_notebook") or {})
    note_id = payload["note_id"]
    previous = notes.get(note_id) or {}
    if payload["expected_note_revision"] != int(previous.get("note_revision", 0)):
        raise StructuredError("revision_conflict", "主持笔记已被修改，请重新读取后更正。")
    if not previous and len(notes) >= MAX_NOTES:
        raise StructuredError("invalid_action", "主持笔记最多256条，请更新已有条目。")
    sources = []
    frozen_events = copy.deepcopy(state.get("keeper_note_sources") or {})
    for source in payload.get("sources") or []:
        kind, _, key = source.partition(":")
        if kind == "receipt":
            valid = ctx.session.scalar(
                select(GameCommand.id).where(
                    GameCommand.world_id == ctx.world_id,
                    GameCommand.command_id == key,
                    GameCommand.status == "committed",
                    GameCommand.error_code != "timeline_discarded",
                )
            )
        elif kind == "event" and key.isdigit():
            event = ctx.session.scalar(
                select(EventOutbox).where(
                    EventOutbox.world_id == ctx.world_id, EventOutbox.sequence == int(key)
                )
            )
            valid = event is not None
            if event is not None:
                frozen = {
                    "origin_world": ctx.world_id,
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "payload": event.payload,
                    "audience": event.audience,
                    "revision": event.revision,
                }
                digest = hashlib.sha256(
                    json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                source = f"note-event:{digest}"
                frozen_events[source] = frozen
        elif kind == "note-event":
            valid = source in frozen_events
        else:
            valid = False
        if not valid:
            raise StructuredError("unknown_target", "笔记出处不存在于当前时间线。")
        sources.append(source)
    entry = {
        "note_id": note_id,
        "note_revision": int(previous.get("note_revision", 0)) + 1,
        "title": payload["title"],
        "summary": payload["summary"],
        "content": payload["content"],
        "category": payload["category"],
        "status": payload.get("status", "open"),
        "sources": sources,
        "world_revision": ctx.revision + 1,
        "author": ctx.principal.as_dict(),
    }
    notes[note_id] = entry
    if len(json.dumps(frozen_events, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
        raise StructuredError("invalid_action", "笔记事件出处归档超过2MiB上限，未删除或截断原文。")
    state["keeper_notebook"] = notes
    # Only explicitly referenced events are frozen. This archive follows saves
    # and branches, so a local sequence reused in a child cannot change evidence.
    state["keeper_note_sources"] = frozen_events
    return CommandResult(
        result={"note": entry, "previous_note_revision": previous.get("note_revision", 0)}
    )


def note_index(state: dict) -> list[dict]:
    return [
        {
            key: note[key]
            for key in (
                "note_id",
                "note_revision",
                "title",
                "summary",
                "category",
                "status",
                "sources",
            )
        }
        for note in sorted(
            (state.get("keeper_notebook") or {}).values(),
            key=lambda n: (n["status"] != "open", -n["world_revision"]),
        )
    ]
