"""Build the same safe, speaker-aware narrative payload for every recovery path."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from typing import Any

from .asset_payload import SpeakerPayloadResolver, enrich_narrative_segments
from .npc_speaker_aliases import current_scene_npc_ids
from .speaker_parser import parse_segments as parse_speaker_segments
from .tool_protocol import strip_tool_protocol


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
    if narrative and not has_speech:
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
