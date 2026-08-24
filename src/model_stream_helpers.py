"""Pure helpers shared by model streaming and final narrative reconciliation."""

from __future__ import annotations

import re
from typing import Any

from .npc_speaker_aliases import current_scene_npc_ids
from .speaker_parser import parse_segments as parse_speaker_segments
from .tool_protocol import strip_tool_protocol

_INTERNAL_NARRATIVE_PATTERNS = (
    re.compile(r"(?:让我|我)?先确认(?:一下)?当前(?:的)?信息边界[。.!！]?\s*"),
    re.compile(r"按玩家(?:的)?明确意图[^。！？\n]*[。！？]?\s*"),
    re.compile(
        r"需要(?:确认|记录|写入)[^。！？\n]*(?:world_state|世界状态)[^。！？\n]*[。！？]?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"当前\s*SAN\s*=\s*\d+\s*[？?][^。！？\n]*(?:应该|不对)[^。！？\n]*[。！？]?\s*",
        re.IGNORECASE,
    ),
)


def sanitize_visible_narrative(text: str) -> str:
    text = strip_tool_protocol(text)
    for pattern in _INTERNAL_NARRATIVE_PATTERNS:
        text = pattern.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def take_complete_sentences(text: str) -> tuple[str, str]:
    boundaries = list(re.finditer(r"[。！？!?\n]", text))
    if not boundaries:
        return "", text
    cutoff = boundaries[-1].end()
    return text[:cutoff], text[cutoff:]


def emit_inferred_speaker_segments(host: Any, raw: str) -> bool:
    """Emit an untagged provider block with deterministic live speakers.

    Some compatible APIs return the entire answer in one content delta
    while ignoring the requested NPC tags.  The finalizer can infer those
    novel-style quotes, but doing it here is what makes the correct bubble
    exist while the presentation queue is still typing.
    """
    aliases = getattr(host, "npc_speaker_aliases", lambda: {})()
    if not aliases or not any(mark in raw for mark in ("“", "「", '"')):
        return False
    segments, clean = parse_speaker_segments(
        raw,
        is_valid_npc=getattr(host, "is_valid_npc_id", None) or (lambda _npc_id: False),
        on_unknown_npc=getattr(host, "log_unknown_npc_speaker", None),
        speaker_aliases=aliases,
        player_text=getattr(host, "_turn_user_content", None),
        present_npc_ids=current_scene_npc_ids(host),
    )
    if not any(segment.kind == "speech" and segment.npc_id for segment in segments):
        return False
    if clean != raw:
        # Explicit tags belong to the incremental parser, whose state may
        # span provider chunks.  This fallback is only for untagged prose.
        return False
    for segment in segments:
        visible = sanitize_visible_narrative(segment.text)
        if not visible:
            continue
        if segment.kind == "speech" and segment.npc_id:
            host.cb.on_speaker_segment(segment.npc_id)
            host.cb.on_narrative(visible, segment.npc_id)
        else:
            host.cb.on_narrative(visible)
    return True


def make_visible_emitter(host: Any, speaker_parser: Any):
    """Build the streamer's visible-text emitter bound to one speaker parser."""

    def emit_visible(raw: str) -> None:
        if emit_inferred_speaker_segments(host, raw):
            return
        for kind, text, npc_id in speaker_parser.feed(raw):
            if kind == "text":
                visible = sanitize_visible_narrative(text)
                if visible:
                    # 旁白保持单参数调用（兼容既有回调签名），
                    # 发言文本才附带 npc_id 上下文。
                    if npc_id:
                        host.cb.on_narrative(visible, npc_id)
                    else:
                        host.cb.on_narrative(visible)
            elif kind == "speech_start":
                # speech_start Piece = (kind, npc_id, None)：人物 id 在 text 槽。
                host.cb.on_speaker_segment(text)

    return emit_visible


def flush_speaker_segments(host: Any, speaker_parser: Any) -> None:
    """Drain the incremental speaker parser, emitting its remaining pieces."""
    for kind, text, npc_id in speaker_parser.flush():
        if kind == "text":
            visible = sanitize_visible_narrative(text)
            if visible:
                if npc_id:
                    host.cb.on_narrative(visible, npc_id)
                else:
                    host.cb.on_narrative(visible)
        elif kind == "speech_start":
            host.cb.on_speaker_segment(text)


def stream_usage_dict(usage: object) -> dict:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        raw = usage
    elif hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    else:
        raw = {
            key: getattr(usage, key, None)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            )
        }
    allowed = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    }
    return {key: value for key, value in raw.items() if key in allowed and value is not None}
