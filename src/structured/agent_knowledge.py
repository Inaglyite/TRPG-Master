"""Bounded knowledge tools with durable, world-scoped sources and continuations.

Compression changes the context surface, never the source ledger. Full receipts,
notes and history remain readable after restart. No model-selected filesystem path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable

from sqlalchemy import String, cast, func, or_, select

from src.storage.database import (
    BranchHistoryEntry,
    CommandEventReceipt,
    EventOutbox,
    GameCommand,
    World,
    WorldState,
    session_scope,
)

from .errors import StructuredError
from .keeper_notebook import note_index
from .principal import Principal, check_command_authority, resolve_keeper_principal

MAX_SOURCE_BYTES = 1024 * 1024


def text_matches(field, text):
    # SQLite's JSON serializer may escape Chinese; PostgreSQL's JSON text uses
    # Unicode. Search both representations with literal wildcard escaping.
    return or_(
        cast(field, String).icontains(text, autoescape=True),
        cast(field, String).icontains(json.dumps(text, ensure_ascii=True)[1:-1], autoescape=True),
    )


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def page(source_id: str, content: str, query: dict, budget: int) -> dict:
    digest = hashlib.sha256(content.encode()).hexdigest()
    if query.get("sha256") and query["sha256"] != digest:
        raise StructuredError("stale_target", "资料已变化，请从头重新读取。")
    offset = int(query.get("offset", 0))
    if offset < 0 or offset > len(content):
        raise StructuredError("invalid_action", "读取位置超出原文范围。")
    end = min(len(content), offset + budget)
    return {
        "source_id": source_id,
        "sha256": digest,
        "offset": offset,
        "total_chars": len(content),
        "content": content[offset:end],
        "complete": end == len(content),
        "next_offset": end if end < len(content) else None,
    }


class AgentKnowledge:
    def __init__(self, database_url: str, *, module_resolver: Callable | None = None):
        self.database_url = database_url
        self.module_resolver = module_resolver

    def _module_documents(self, world: World) -> list[dict]:
        resolver = self.module_resolver
        if resolver is None:
            from src.app.config import PROJECT_ROOT, RUNTIME_ROOT
            from src.modules.module_registry import ModuleRegistry

            resolver = ModuleRegistry(PROJECT_ROOT, RUNTIME_ROOT).resolve
        try:
            record = resolver(world.module_name)
        except (OSError, ValueError):
            raise StructuredError("unknown_target", "当前模组资料不可用。") from None
        if world.module_version and world.module_version != record.version:
            raise StructuredError("stale_target", "安装的模组版本与世界绑定版本不同，不能混用。")
        root = record.path.resolve()
        paths = [("module", root / "module.md")]
        scenes = root / "scenes"
        if scenes.resolve().is_relative_to(root) and scenes.is_dir():
            paths.extend((f"scene:{p.stem}", p) for p in sorted(scenes.glob("*.md")))
        documents = []
        used = 0
        for key, path in paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            data = resolved.read_bytes() if resolved.stat().st_size <= MAX_SOURCE_BYTES else None
            if data is None or used + len(data) > MAX_SOURCE_BYTES or len(documents) >= 200:
                raise StructuredError("invalid_action", "模组资料超过读取上限，未静默截断。")
            used += len(data)
            documents.append({"id": key, "content": data.decode("utf-8-sig")})
        lore = root / "lorebook.json"
        if lore.resolve().is_relative_to(root) and lore.is_file():
            if lore.stat().st_size + used > MAX_SOURCE_BYTES:
                raise StructuredError("invalid_action", "知识库超过读取上限。")
            entries = (
                json.loads(lore.read_text(encoding="utf-8-sig")).get("data", {}).get("entries", [])
            )
            for index, entry in enumerate(entries):
                if isinstance(entry, dict) and isinstance(entry.get("content"), str):
                    if len(documents) >= 200:
                        raise StructuredError(
                            "invalid_action", "模组资料超过200份上限，未静默截断。"
                        )
                    documents.append(
                        {
                            "id": f"lore:{index}",
                            "content": entry["content"],
                            "enabled": entry.get("enabled", True),
                        }
                    )
        return documents

    def query(
        self, world_id: str, principal: Principal, query: dict, *, char_budget: int = 4000
    ) -> dict:
        budget = min(max(int(char_budget), 256), 16000)
        if not isinstance(query, dict):
            raise StructuredError("invalid_action", "查询必须是对象。")
        if set(query) - {
            "kind",
            "text",
            "source_id",
            "offset",
            "sha256",
            "limit",
        }:
            raise StructuredError("invalid_action", "查询含未声明字段。")
        for key in ("offset", "limit"):
            if key in query and (type(query[key]) is not int or query[key] < 0):
                raise StructuredError("invalid_action", "查询位置和条数必须是非负整数。")
        for key in ("text", "source_id", "sha256"):
            if key in query and not isinstance(query[key], str):
                raise StructuredError("invalid_action", "查询关键词、资料ID和摘要必须是字符串。")
        text = str(query.get("text") or "")
        if len(text) > 200:
            raise StructuredError("invalid_action", "查询关键词最多200字。")
        source_id = str(query.get("source_id") or "")
        kind = query.get("kind")
        if int(query.get("offset", 0)) < 0:
            raise StructuredError("invalid_action", "读取位置不能为负数。")
        with session_scope(self.database_url) as session:
            if principal.kind == "keeper":
                resolve_keeper_principal(session, world_id, principal.user_id)
            elif principal.kind == "agent":
                check_command_authority(session, world_id, principal)
            else:
                raise StructuredError("keeper_required", "资料查询需要主持权限。")
            world = session.get(World, world_id)
            state_row = session.get(WorldState, world_id)
            if world is None or state_row is None:
                raise StructuredError("unknown_world", "世界不存在。")
            state = state_row.state or {}
            if kind == "chapters":
                from .receipt_compaction import valid_chapters

                chapters = valid_chapters(session, world_id, state)
                if source_id:
                    chapter = next((c for c in chapters if c["chapter_id"] == source_id), None)
                    if chapter is None:
                        stored = (state.get("harness_chapters") or {}).get(source_id)
                        if stored and stored.get("superseded_by"):
                            probe = {
                                "harness_chapters": {
                                    source_id: {
                                        k: v for k, v in stored.items() if k != "superseded_by"
                                    }
                                }
                            }
                            if valid_chapters(session, world_id, probe):
                                chapter = stored  # Explicit historical read, never active index.
                    if chapter is None:
                        raise StructuredError("unknown_target", "阶段记忆不存在于当前时间线。")
                    return page(source_id, encoded(chapter), query, budget)
                return self._hits(
                    [
                        {
                            "source_id": c["chapter_id"],
                            "title": c["title"],
                            "summary": c["summary"][:240],
                            "keywords": c["keywords"],
                            "through_revision": c["through_revision"],
                            "requires_read": True,
                        }
                        for c in reversed(chapters)
                        if not text or text.casefold() in encoded(c).casefold()
                    ],
                    query,
                    budget,
                )
            if kind == "module":
                docs = self._module_documents(world)
                if source_id:
                    doc = next((d for d in docs if d["id"] == source_id), None)
                    if doc is None:
                        raise StructuredError("unknown_target", "模组文档ID不存在。")
                    return {
                        **page(source_id, doc["content"], query, budget),
                        "module_version": world.module_version,
                        "enabled": doc.get("enabled", True),
                    }
                hits = []
                for doc in docs:
                    content = doc["content"]
                    terms = re.split(r"\s+", text.strip()) if text else []
                    positions = set()
                    for term in terms:
                        positions.update(
                            match.start()
                            for match in re.finditer(re.escape(term), content, re.IGNORECASE)
                        )
                    for start in sorted(positions):
                        excerpt = content[max(0, start - 80) : start + 240]
                        hits.append(
                            {
                                "source_id": doc["id"],
                                "offset": max(0, start - 80),
                                "excerpt": excerpt,
                                "enabled": doc.get("enabled", True),
                                "matched_terms": [
                                    term for term in terms if term.casefold() in excerpt.casefold()
                                ],
                            }
                        )
                    if not text:
                        hits.append(
                            {
                                "source_id": doc["id"],
                                "offset": 0,
                                "total_chars": len(content),
                                "enabled": doc.get("enabled", True),
                            }
                        )
                if text:
                    hits.sort(key=lambda hit: len(hit["matched_terms"]), reverse=True)
                return self._hits(hits, query, budget)
            if kind == "notes":
                notes = state.get("keeper_notebook") or {}
                if source_id:
                    note = notes.get(source_id)
                    if note is None:
                        raise StructuredError("unknown_target", "主持笔记不存在。")
                    return page(source_id, encoded(note), query, budget)
                return self._hits(
                    [
                        n
                        for n in note_index(state)
                        if not text or text.casefold() in encoded(notes[n["note_id"]]).casefold()
                    ],
                    query,
                    budget,
                )
            if kind == "receipt":
                stmt = select(GameCommand).where(
                    GameCommand.world_id == world_id,
                    GameCommand.status == "committed",
                    GameCommand.error_code != "timeline_discarded",
                )
                if source_id:
                    row = session.scalar(stmt.where(GameCommand.command_id == source_id))
                    if row is None:
                        raise StructuredError("unknown_target", "执行回执不存在于当前时间线。")
                    return page(source_id, encoded(self._receipt(row)), query, budget)
                if text:
                    stmt = stmt.where(
                        or_(
                            text_matches(GameCommand.result, text),
                            text_matches(GameCommand.payload, text),
                            GameCommand.event_receipts.any(
                                text_matches(CommandEventReceipt.event_json, text)
                            ),
                            GameCommand.kind.icontains(text, autoescape=True),
                            GameCommand.command_id.icontains(text, autoescape=True),
                            GameCommand.cause_id.icontains(text, autoescape=True),
                        )
                    )
                start = int(query.get("offset", 0))
                total = session.scalar(select(func.count()).select_from(stmt.subquery()))
                rows = session.scalars(
                    stmt.order_by(GameCommand.created_at.desc(), GameCommand.id.desc())
                    .offset(start)
                    .limit(20)
                ).all()
                return self._database_hits(
                    [
                        {
                            "source_id": row.command_id,
                            "kind": row.kind,
                            "revision": row.revision,
                            "cause_id": row.cause_id,
                        }
                        for row in rows
                    ],
                    query,
                    budget,
                    total,
                )
            if kind == "history":
                if source_id:
                    if source_id.startswith("receipt-event:"):
                        key, _, ordinal = source_id.removeprefix("receipt-event:").rpartition(":")
                        found = session.execute(
                            select(CommandEventReceipt, GameCommand.command_id)
                            .join(GameCommand, GameCommand.id == CommandEventReceipt.command_row_id)
                            .where(
                                GameCommand.world_id == world_id,
                                GameCommand.status == "committed",
                                GameCommand.error_code != "timeline_discarded",
                                CommandEventReceipt.command_row_id == key,
                                CommandEventReceipt.ordinal == int(ordinal)
                                if ordinal.isdigit()
                                else False,
                            )
                        ).first()
                        if found is None:
                            raise StructuredError(
                                "unknown_target", "历史事件回执不属于当前时间线。"
                            )
                        return page(
                            source_id,
                            encoded({"command_id": found[1], **found[0].event_json}),
                            query,
                            budget,
                        )
                    if source_id.startswith("note-event:"):
                        frozen = (state.get("keeper_note_sources") or {}).get(source_id)
                        if frozen is None:
                            raise StructuredError("unknown_target", "笔记事件出处不存在。")
                        return page(source_id, encoded(frozen), query, budget)
                    if source_id.startswith("receipt:"):
                        command = session.scalar(
                            select(GameCommand).where(
                                GameCommand.world_id == world_id,
                                GameCommand.command_id == source_id.removeprefix("receipt:"),
                                GameCommand.status == "committed",
                                GameCommand.error_code != "timeline_discarded",
                            )
                        )
                        if command is None:
                            raise StructuredError("unknown_target", "历史回执不属于当前时间线。")
                        return page(source_id, encoded(self._receipt(command)), query, budget)
                    prefix, _, key = source_id.partition(":")
                    if not key.isdigit():
                        raise StructuredError("unknown_target", "历史记录ID无效。")
                    row = (
                        session.scalar(
                            select(EventOutbox).where(
                                EventOutbox.world_id == world_id, EventOutbox.sequence == int(key)
                            )
                        )
                        if prefix == "event"
                        else None
                    )
                    if row is not None:
                        return page(
                            source_id,
                            encoded(
                                {
                                    "type": row.event_type,
                                    "payload": row.payload,
                                    "audience": row.audience,
                                    "revision": row.revision,
                                }
                            ),
                            query,
                            budget,
                        )
                    archive = (
                        session.scalar(
                            select(BranchHistoryEntry).where(
                                BranchHistoryEntry.world_id == world_id,
                                BranchHistoryEntry.ordinal == int(key),
                            )
                        )
                        if prefix == "archive"
                        else None
                    )
                    if archive is not None:
                        return page(
                            source_id,
                            encoded({"message": archive.message, "audience": archive.audience}),
                            query,
                            budget,
                        )
                    raise StructuredError("unknown_target", "历史记录不存在于当前时间线。")
                hits = []
                matched = 0
                for model, field, prefix, order in (
                    (EventOutbox, EventOutbox.payload, "event", EventOutbox.sequence),
                    (
                        BranchHistoryEntry,
                        BranchHistoryEntry.message,
                        "archive",
                        BranchHistoryEntry.ordinal,
                    ),
                    (GameCommand, GameCommand.payload, "receipt", GameCommand.created_at),
                    (
                        CommandEventReceipt,
                        CommandEventReceipt.event_json,
                        "receipt-event",
                        GameCommand.created_at,
                    ),
                ):
                    if prefix == "receipt-event":
                        stmt = (
                            select(model)
                            .join(GameCommand, GameCommand.id == model.command_row_id)
                            .where(
                                GameCommand.world_id == world_id,
                                GameCommand.status == "committed",
                                GameCommand.error_code != "timeline_discarded",
                                model.event_type == "message_completed",
                                ~select(EventOutbox.id)
                                .where(
                                    EventOutbox.world_id == world_id,
                                    EventOutbox.event_type == "message_completed",
                                    EventOutbox.payload["message_id"].as_string()
                                    == model.message_id,
                                )
                                .exists(),
                            )
                        )
                    else:
                        stmt = select(model).where(model.world_id == world_id)
                    if prefix == "receipt":
                        stmt = stmt.where(
                            GameCommand.kind == "publish_message",
                            GameCommand.status == "committed",
                            GameCommand.error_code != "timeline_discarded",
                            ~GameCommand.event_receipts.any(),
                            ~select(EventOutbox.id)
                            .where(
                                EventOutbox.world_id == world_id,
                                EventOutbox.event_type == "message_completed",
                                EventOutbox.payload["message_id"].as_string()
                                == GameCommand.result["message_id"].as_string(),
                            )
                            .correlate(GameCommand)
                            .exists(),
                        )
                    if text:
                        stmt = stmt.where(text_matches(field, text))
                    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
                    start = int(query.get("offset", 0))
                    skip = max(0, start - matched)
                    rows = (
                        session.scalars(stmt.order_by(order.desc()).offset(skip).limit(20)).all()
                        if len(hits) < 20
                        else []
                    )
                    matched += total
                    for row in rows:
                        content = (
                            row.message
                            if prefix == "archive"
                            else row.event_json
                            if prefix == "receipt-event"
                            else row.payload
                        )
                        raw = encoded(content)
                        position = max(0, raw.casefold().find(text.casefold()) - 80) if text else 0
                        hits.append(
                            {
                                "source_id": f"{prefix}:{row.sequence if prefix == 'event' else row.ordinal if prefix == 'archive' else str(row.command_row_id) + ':' + str(row.ordinal) if prefix == 'receipt-event' else row.command_id}",
                                "event_type": row.event_type
                                if prefix in {"event", "receipt-event"}
                                else "message_completed",
                                "source_is_proposal": prefix == "event"
                                and row.event_type == "keeper_draft",
                                "audience": row.audience
                                if prefix not in {"receipt", "receipt-event"}
                                else row.event_json.get("audience", {"kind": "keeper"})
                                if prefix == "receipt-event"
                                else row.payload.get("audience", {"kind": "keeper"}),
                                "excerpt": raw[position : position + 320],
                            }
                        )
                return self._database_hits(hits, query, budget, matched)
            raise StructuredError(
                "invalid_action", "查询类型须为module、notes、receipt、history或chapters。"
            )

    @staticmethod
    def _receipt(row: GameCommand) -> dict:
        from .command_receipts import complete_receipt

        return complete_receipt(row)

    @staticmethod
    def _database_hits(hits, query, budget, total):
        start = int(query.get("offset", 0))
        if start > total:
            raise StructuredError("invalid_action", "检索位置无效。")
        result = AgentKnowledge._hits(hits[:20], {**query, "offset": 0}, budget)
        end = start + len(result["items"])
        result.update(
            total_matches=total, complete=end >= total, next_offset=end if end < total else None
        )
        return result

    @staticmethod
    def _hits(hits: list[dict], query: dict, budget: int) -> dict:
        start = int(query.get("offset", 0))
        if start < 0 or start > len(hits):
            raise StructuredError("invalid_action", "检索位置无效。")
        count = min(max(int(query.get("limit", 8)), 1), 20)
        items = []
        for hit in hits[start : start + count]:
            if len(encoded([hit])) > budget:
                hit = {
                    "source_id": hit.get("source_id") or hit.get("note_id"),
                    "requires_read": True,
                }
                if len(encoded([hit])) > budget:
                    raise StructuredError(
                        "invalid_action", "资料ID超过本次查询预算，请提高读取预算。"
                    )
            if items and len(encoded(items + [hit])) > budget:
                break
            items.append(hit)
        end = start + len(items)
        return {
            "items": items,
            "total_matches": len(hits),
            "complete": end >= len(hits),
            "next_offset": end if end < len(hits) else None,
        }

    def context(
        self,
        world_id: str,
        *,
        tail_chars: int = 12000,
        index_chars: int = 6000,
        persist_checkpoint: bool = True,
    ) -> dict:
        """Deterministic closed-event checkpoint; current wait state stays in snapshot.

        The checkpoint is auxiliary metadata (no rule revision increment). It is
        saved under the same world lock as commands and is copied/restored with
        the world. Source records are never removed by compaction.
        """
        with session_scope(self.database_url) as session:
            row = session.get(WorldState, world_id, with_for_update=True)
            if row is None:
                raise StructuredError("unknown_world", "世界状态不存在。")
            state = copy.deepcopy(row.state or {})
            from .receipt_compaction import MAX_CHAPTERS, valid_chapters

            chapters = valid_chapters(session, world_id, state)
            chapter_index = self._hits(
                [
                    {
                        "source_id": c["chapter_id"],
                        "title": c["title"],
                        "summary": c["summary"][:240],
                        "keywords": c["keywords"],
                        "requires_read": True,
                    }
                    for c in reversed(chapters)
                ],
                {},
                index_chars,
            )
            messages = session.scalars(
                select(EventOutbox)
                .where(
                    EventOutbox.world_id == world_id, EventOutbox.event_type == "message_completed"
                )
                .order_by(EventOutbox.sequence.desc())
                .limit(12)
            ).all()
            tail = []
            used = 0
            for message in messages:
                item = {
                    "source_id": f"event:{message.sequence}",
                    "speaker": message.payload.get("speaker"),
                    "text": message.payload.get("text", ""),
                    "audience": message.audience,
                }
                cost = len(encoded(item))
                if tail and used + cost > tail_chars:
                    break
                if cost > tail_chars:
                    item = {
                        "source_id": item["source_id"],
                        "requires_read": True,
                        "total_chars": cost,
                        "audience": message.audience,
                    }
                tail.append(item)
                used += min(cost, tail_chars)
                if len(tail) >= 12:
                    break
            cutoff = int(tail[-1]["source_id"].split(":")[1]) - 1 if tail else 0
            older_query = select(EventOutbox).where(
                EventOutbox.world_id == world_id,
                EventOutbox.event_type == "message_completed",
                EventOutbox.sequence <= cutoff,
            )
            older_count = session.scalar(select(func.count()).select_from(older_query.subquery()))
            older = session.scalars(
                older_query.order_by(EventOutbox.sequence.desc()).limit(8)
            ).all()
            inherited_count = session.scalar(
                select(func.count())
                .select_from(BranchHistoryEntry)
                .where(BranchHistoryEntry.world_id == world_id)
            )
            # Regenerated from immutable sources: no recursively lossy summaries.
            checkpoint = {
                "version": 1,
                "through_sequence": cutoff,
                "world_revision": row.revision,
                "archived_messages": older_count,
                "inherited_history_entries": inherited_count,
                "source_kind": "event_outbox",
                "recent_topics": [
                    {
                        "source_id": f"event:{m.sequence}",
                        "audience": m.audience,
                        "excerpt": str(m.payload.get("text", ""))[:120],
                        "requires_read": True,
                    }
                    for m in older[:8]
                ],
                "history_query": {"kind": "history", "text": "关键词"},
            }
            if persist_checkpoint and state.get("harness_checkpoint") != checkpoint:
                state["harness_checkpoint"] = checkpoint
                row.state = state
            notes = self._hits(note_index(state), {}, index_chars)
            receipts = session.scalars(
                select(GameCommand)
                .where(
                    GameCommand.world_id == world_id,
                    GameCommand.status == "committed",
                    GameCommand.error_code != "timeline_discarded",
                )
                .order_by(GameCommand.created_at.desc(), GameCommand.id.desc())
                .limit(8)
            ).all()
            recent = []
            for command in reversed(receipts):
                receipt = self._receipt(command)
                raw = encoded(receipt)
                recent.append(
                    receipt
                    if len(raw) <= 2400
                    else {
                        "command_id": command.command_id,
                        "kind": command.kind,
                        "revision": command.revision,
                        "requires_read": True,
                        "total_chars": len(raw),
                        "query": {"kind": "receipt", "source_id": command.command_id},
                    }
                )
            return {
                "chapter_index": chapter_index,
                "chapter_capacity": {
                    "used": len(chapters),
                    "maximum": MAX_CHAPTERS,
                    "at_limit": len(chapters) >= MAX_CHAPTERS,
                },
                "keeper_note_index": notes,
                "memory_checkpoint": checkpoint,
                "recent_history": list(reversed(tail)),
                "execution_receipts": recent,
            }
