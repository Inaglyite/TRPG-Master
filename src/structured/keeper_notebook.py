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
    from .receipt_compaction import CHAPTER_NOTE_PREFIX, chapter_note_id

    notes = copy.deepcopy(state.get("keeper_notebook") or {})
    note_id = payload["note_id"]
    previous = notes.get(note_id) or {}
    overridden = None
    if note_id.startswith(CHAPTER_NOTE_PREFIX):
        if ctx.principal.kind != "keeper":
            raise StructuredError("keeper_required", "自动阶段摘要的更正需要人类主持批准。")
        overridden = next(
            (
                chapter
                for chapter in (state.get("harness_chapters") or {}).values()
                if note_id
                in {
                    chapter_note_id(chapter["chapter_id"]),
                    CHAPTER_NOTE_PREFIX + chapter["chapter_id"].split(":")[1][:24],
                }
            ),
            None,
        )
        if overridden is None and not previous:
            try:
                candidate = json.loads(payload["content"])
                valid_candidate = (
                    candidate.get("format") == "keeper_chapter_v1"
                    and chapter_note_id(candidate["chapter_id"]) == note_id
                )
            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                valid_candidate = False
            if not valid_candidate:
                raise StructuredError("unknown_target", "自动阶段摘要不存在，请先从资料库读取。")
    if payload["expected_note_revision"] != int(previous.get("note_revision", 0)):
        raise StructuredError("revision_conflict", "主持笔记已被修改，请重新读取后更正。")
    if not previous and len(notes) >= MAX_NOTES:
        raise StructuredError("invalid_action", "主持笔记最多256条，请更新已有条目。")
    sources = []
    frozen_events = copy.deepcopy(state.get("keeper_note_sources") or {})
    source_inputs = payload.get("sources") or (
        ["receipt:" + s["command_id"] for s in overridden["sources"]] if overridden else []
    )
    for source in source_inputs:
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
    if overridden:
        chapters = copy.deepcopy(state["harness_chapters"])
        chapters[overridden["chapter_id"]]["superseded_by"] = note_id
        state["harness_chapters"] = chapters
        entry["supersedes_chapter"] = overridden["chapter_id"]
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
