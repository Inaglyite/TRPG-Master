"""Build the same safe, speaker-aware narrative payload for every recovery path."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from typing import Any

from src.ai.tools.tool_protocol import strip_tool_protocol
from src.gameplay.npc_speaker_aliases import current_scene_npc_ids
from src.gameplay.speaker_parser import parse_segments as parse_speaker_segments
from src.web.asset_payload import SpeakerPayloadResolver, enrich_narrative_segments


def enrich_public_history_record(
    record: dict,
    engine: Any,
    *,
    resolve_speaker: Callable[[str], dict | None] | None = None,
) -> dict:
    """Sanitize one public turn and restore legacy NPC speaker attribution."""
    public = copy.deepcopy(record)
    narrative = strip_tool_protocol(str(public.get("narrative") or ""))
    public["narrative"] = narrative

    raw_segments = public.get("narrative_segments")
    clean_segments: list[dict] = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if not isinstance(segment, dict):
                continue
            clean = dict(segment)
            clean["text"] = strip_tool_protocol(str(clean.get("text") or ""))
            if clean["text"].strip():
                clean_segments.append(clean)

    events = public.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("type") == "narrative_chunk":
                event["text"] = strip_tool_protocol(str(event.get("text") or ""))

    has_speech = any(segment.get("kind") == "speech" for segment in clean_segments)
    structured = public.get("narrative_format", (public.get("diagnostics") or {}).get("narrative_format")) == "dialogue_v1"
    if narrative and not has_speech and not structured:
        reparsed, _clean = parse_speaker_segments(
            narrative,
            is_valid_npc=engine.is_valid_npc_id,
            on_unknown_npc=engine.log_unknown_npc_speaker,
            speaker_aliases=engine.npc_speaker_aliases(),
            player_text=str(public.get("player_input") or "") or None,
            present_npc_ids=current_scene_npc_ids(engine),
        )
        if any(segment.kind == "speech" for segment in reparsed):
            clean_segments = [segment.to_dict() for segment in reparsed]

    resolver = resolve_speaker or SpeakerPayloadResolver(engine)
    enriched = enrich_narrative_segments(clean_segments, resolver)
    public["narrative_segments"] = enriched
    return public


def enrich_public_history(records: Iterable[dict], engine: Any) -> list[dict]:
    """Enrich a complete lineage while sharing one immutable speaker cache."""
    resolver = SpeakerPayloadResolver(engine)
    return [
        enrich_public_history_record(record, engine, resolve_speaker=resolver)
        for record in records
        if isinstance(record, dict)
    ]


def restore_tagged_speakers(messages: list[dict], journal: Any) -> list[dict]:
    """载入/恢复路径专用：用回合记录里已验证的 narrative_segments 还原
    assistant 正文的发言标签正本。

    旧存档的 messages 只存了剥标签的干净正文，直接喂给模型会把历史教成
    “全是旁白”。回合 record 里的 narrative_segments 是定稿时已验证的结构，
    按正文归一化精确匹配回填；匹配不上（摘要、压缩、旧纯文本）保持原样，
    不臆造身份，不写回存档，不发起任何模型调用。
    """
    if journal is None:
        return messages
    try:
        records = journal.list_completed(limit=10000)
    except Exception:
        return messages
    from src.ai.model.dialogue_output import segments_to_tagged_text
    from src.app.turn_rewrite import normalize_narrative_for_match

    tagged_by_text: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        segments = record.get("narrative_segments") or []
        if not any(
            isinstance(segment, dict)
            and segment.get("kind") == "speech"
            and segment.get("npc_id")
            for segment in segments
        ):
            continue
        key = normalize_narrative_for_match(str(record.get("narrative") or ""))
        if key:
            tagged_by_text[key] = segments_to_tagged_text(segments)
    if not tagged_by_text:
        return messages
    restored: list[dict] = []
    for message in messages:
        if message.get("role") == "assistant":
            tagged = tagged_by_text.get(
                normalize_narrative_for_match(str(message.get("content") or ""))
            )
            if tagged:
                message = dict(message, content=tagged)
        restored.append(message)
    return restored
