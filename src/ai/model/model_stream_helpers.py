"""Pure helpers shared by model streaming and final narrative reconciliation."""

from __future__ import annotations

import re
from typing import Any

from src.ai.model.dialogue_output import decode_dialogue_output
from src.ai.model.llm_concurrency import ModelResponseError
from src.ai.model.provider_adapter import note_model_retry
from src.ai.tools.tool_protocol import strip_tool_protocol
from src.gameplay.npc_speaker_aliases import current_scene_npc_ids
from src.gameplay.speaker_parser import parse_segments as parse_speaker_segments


def emit_structured_dialogue(host, raw, parser, *, finish_reason, log_error, report_error=True) -> str:
    if finish_reason in {"transport_error", "length", "content_filter"}:
        raise ModelResponseError(f"模型结构化对话未完成：{finish_reason}")
    try:
        text = sanitize_visible_narrative(decode_dialogue_output(raw, present_npc_ids=current_scene_npc_ids(host) or set()))
        if not text.strip():
            raise ValueError("对话正文没有有效的公开文本")
        # 修复回合的 pre-emit 复读拦截：必须在任何 on_narrative/on_speaker_segment
        # 回调之前判定，事后 raise 拦不住已经展示给玩家的旧回复。
        replay_guard = getattr(host, "_dialogue_repair_guard", None)
        if replay_guard is not None:
            from src.ai.model.dialogue_repair_context import is_replayed_narrative

            if is_replayed_narrative(text, replay_guard):
                raise ValueError("修复结果整段复用上一轮叙事")
    except ValueError as exc:
        log_error(f"模型结构化对话无效：{exc}")
        if report_error:
            if "复用上一轮叙事" in str(exc):
                host.cb.on_error("本轮叙述修复失败：修复结果与上一轮相同，未提交任何内容；请重试本轮。")
            else:
                host.cb.on_error("模型对话格式或发言者无效，请重试本轮。")
        raise ModelResponseError("模型结构化对话未通过校验") from exc
    # Canonical IDs alone own speech; no prose inference is permitted here.
    for kind, chunk, npc_id in parser.feed(text) + parser.flush():
        if kind == "speech_start":
            host.cb.on_speaker_segment(chunk)
        elif kind == "text" and chunk:
            visible = sanitize_visible_narrative(chunk)
            if visible:
                if npc_id:
                    host.cb.on_narrative(visible, npc_id)
                else:
                    host.cb.on_narrative(visible)
    return text


def finish_structured_dialogue(streamer, model, raw, parser, *, finish_reason, request_options):
    """One bounded protocol repair, after the previous LLM slot is released."""
    host = streamer.host
    if request_options.pop("required_tool", False):
        host.cb.on_error("战斗行动尚未生成执行请求，请重试本轮。")
        raise ModelResponseError("模型未提交必需的战斗工具请求")
    repair_allowed = request_options["retry_on_empty"] and finish_reason not in {"transport_error", "length", "content_filter"}
    try:
        return emit_structured_dialogue(host, raw, parser, finish_reason=finish_reason, log_error=streamer.log_error, report_error=not repair_allowed), []
    except ModelResponseError as exc:
        if not repair_allowed:
            raise
        validation_error = str(exc.__cause__ or exc)
    note_model_retry(host, "invalid_dialogue", "unknown", 0)
    source = request_options["messages_override"]
    from src.ai.model.dialogue_repair_context import (
        has_substantive_content,
        previous_assistant_narrative,
        repair_messages,
    )

    source = source if source is not None else host.messages
    messages = repair_messages(host, source, raw, validation_error)
    # 原始输出无有效剧情（空白/通用占位尾句）时，修复结果不得整段照搬上一轮
    # 长篇叙事。守卫挂在 host 上由 emit_structured_dialogue 在解码后、任何
    # 玩家可见回调之前判定；try/finally 保证不泄漏到后续请求。修复请求本身
    # enable_tools=False，不会重复执行已结算的工具或检定。
    guard = None
    if not has_substantive_content(raw):
        previous = previous_assistant_narrative(source)
        if previous:
            guard = previous
            host._dialogue_repair_guard = previous
    try:
        return streamer.stream(model, **(request_options | {"messages_override": messages,
            "system_overlay": None, "system_prompt_override": None, "enable_tools": False,
            "temperature": 0.0, "retry_on_empty": False}))
    finally:
        if guard is not None:
            try:
                del host._dialogue_repair_guard
            except AttributeError:
                pass

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
