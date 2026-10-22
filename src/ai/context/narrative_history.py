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
    history = [
        enrich_public_history_record(record, engine, resolve_speaker=resolver)
        for record in records
        if isinstance(record, dict)
    ]
    if history:
        history[-1]["current_choices"] = current_history_choices(history, engine.context.world_store.load())
    return history


def current_history_choices(history: list[dict], world: dict) -> list[dict]:
    """Historical menus remain unchanged; only the actionable menu is refreshed."""
    from src.ai.model.dialogue_output import _strip_trailing_menu
    from src.gameplay.choice_policy import filter_choice_targets

    if not history or world.get("game_over"):
        return []
    choices = history[-1].get("choices") or []
    targets = [dict(choice["target"], choice_index=index)
               for index, choice in enumerate(choices, 1)
               if isinstance(choice.get("target"), dict)]
    public_text = "\n".join(_strip_trailing_menu(str(row.get("narrative") or ""))[0]
                            for row in history[-12:])
    kept, _ = filter_choice_targets([str(choice.get("label") or "") for choice in choices], targets, world, public_text)
    return [copy.deepcopy(choices[index - 1]) for index in kept]


def restore_tagged_speakers(messages: list[dict], journal: Any) -> list[dict]:
    """载入/恢复路径专用：用回合记录里已验证的 narrative_segments 还原
    assistant 正文的发言标签正本。

    旧存档的 messages 只存了剥标签的干净正文，直接喂给模型会把历史教成
    “全是旁白”。回合 record 里的 narrative_segments 是定稿时已验证的结构，
    新历史按稳定回合 ID 关联；无来源的旧正文仅在归属唯一时精确回填。
    同文异人、摘要与不可恢复旧文绝不猜身份，不写回存档，不调用模型。
    """
    if journal is None:
        return messages
    try:
        records = journal.list_completed(limit=10000)
    except Exception:
        return messages
    from src.ai.context.message_provenance import HISTORY_KIND_KEY, TURN_SOURCE_KEY
    from src.ai.model.dialogue_output import segments_to_tagged_text
    from src.app.turn_rewrite import normalize_narrative_for_match

    by_id: dict[str, dict] = {}
    by_text: dict[str, list[dict]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("turn_id") or "")
        if record_id:
            by_id[record_id] = record
        key = normalize_narrative_for_match(str(record.get("narrative") or ""))
        if key:
            by_text.setdefault(key, []).append(record)

    def tagged(record: dict) -> str | None:
        segments = record.get("narrative_segments") or []
        if not any(
            isinstance(segment, dict)
            and segment.get("kind") == "speech"
            and segment.get("npc_id")
            for segment in segments
        ):
            return None
        return segments_to_tagged_text(segments)

    restored: list[dict] = []
    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            content = str(message.get("content") or "")
            key = normalize_narrative_for_match(content)
            source_id = message.get(TURN_SOURCE_KEY)
            candidates = (
                [by_id[source_id]] if isinstance(source_id, str) and source_id in by_id
                else [] if source_id else by_text.get(key, [])
            )
            candidates = [record for record in candidates if
                          normalize_narrative_for_match(str(record.get("narrative") or "")) == key]
            variants = {body for record in candidates if (body := tagged(record))}
            if len(variants) == 1 and all(tagged(record) for record in candidates):
                message = dict(message, content=variants.pop(), **{HISTORY_KIND_KEY: "dialogue"})
                if len(candidates) == 1 and candidates[0].get("turn_id"):
                    message[TURN_SOURCE_KEY] = candidates[0]["turn_id"]
            elif len(candidates) == 1 and candidates[0].get("narrative_format") == "dialogue_v1":
                # A verified narration-only turn is a valid structured example.
                message = dict(message, **{HISTORY_KIND_KEY: "dialogue"})
                if candidates[0].get("turn_id"):
                    message[TURN_SOURCE_KEY] = candidates[0]["turn_id"]
            elif "【npc:" not in content and message.get(HISTORY_KIND_KEY) != "dialogue":
                message = dict(message, **{HISTORY_KIND_KEY: "legacy_unattributed"})
        restored.append(message)
    return restored
