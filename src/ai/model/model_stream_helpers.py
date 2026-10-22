"""Pure helpers shared by model streaming and final narrative reconciliation."""

from __future__ import annotations

import json
import re
from typing import Any

from src.ai.model.dialogue_output import (
    AttributionSuspicion,
    DialogueFormatError,
    DialogueValidationError,
    SpeakerDepartedError,
    SpeechBlock,
    choice_skill_hint_map,
    decode_dialogue_output,
    narration_attribution_suspicion,
    parse_dialogue_output,
)
from src.ai.model.llm_concurrency import ModelResponseError
from src.ai.model.provider_adapter import note_model_retry
from src.ai.tools.tool_protocol import strip_tool_protocol
from src.gameplay.npc_speaker_aliases import current_scene_npc_ids, turn_allowed_npc_ids
from src.gameplay.speaker_parser import SpeakerStreamParser
from src.gameplay.speaker_parser import parse_segments as parse_speaker_segments


def _guest_context(host, parsed) -> tuple[set[str], set[str], str]:
    """(本回合可用 guest id 全集, 本轮新声明 id, 当前场景 id)。

    可用集合 = 已登记且场景匹配的临时身份 ∪ 本回合离场者（仅告别）∪ 本轮新声明。
    世界状态读不到时只允许本轮新声明，不阻断叙事。
    """
    from src.gameplay.guest_speakers import guest_registry, turn_allowed_guest_ids

    declared = {block.speaker_id for block in parsed.guest_speakers}
    try:
        world = host.context.world_store.load()
    except Exception:
        return set(declared), declared, ""
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    from src.gameplay.scene_speaker_presence import departed_speakers, departure_ids_this_turn

    departed_map = departed_speakers(world, scene_id)
    farewell = departure_ids_this_turn(host) & set(world.get("guest_speakers") or {})
    redeclared = sorted(declared & (set(departed_map) - farewell))
    if redeclared:
        raise SpeakerDepartedError(
            redeclared[0],
            departed_map.get(redeclared[0]),
            message=f"人物已离场，不能通过重复声明临时身份重新出场：{redeclared[0]}",
        )
    if declared and departed_map:
        # 把已离场人物换个新 guest id 重新声明同样属于绕过：按离场事实拦截。
        registry = guest_registry(world)
        departed_names: dict[str, str] = {}
        for speaker_id in departed_map:
            entry = registry.get(speaker_id)
            if entry is not None and entry.get("display_name"):
                departed_names[str(entry["display_name"])] = speaker_id
        for npc in world.get("npcs", []) or []:
            if isinstance(npc, dict) and str(npc.get("id") or "") in departed_map:
                for key in ("name", "display_name"):
                    if npc.get(key):
                        departed_names[str(npc[key])] = str(npc["id"])
        for npc_id, asset in ((world.get("asset_map") or {}).get("npcs") or {}).items():
            if str(npc_id) in departed_map and isinstance(asset, dict):
                label = str(asset.get("label") or asset.get("name") or "")
                if label:
                    departed_names[label] = str(npc_id)
        renamed = next(
            (
                block
                for block in parsed.guest_speakers
                if block.speaker_id in declared and block.display_name in departed_names
            ),
            None,
        )
        if renamed is not None:
            departed_id = departed_names[renamed.display_name]
            raise SpeakerDepartedError(
                departed_id,
                departed_map.get(departed_id),
                message=(
                    f"临时身份 {renamed.speaker_id} 的称呼与已离场人物重复："
                    f"{renamed.display_name}（{departed_id}）"
                ),
            )
    registered = turn_allowed_guest_ids(host, world, scene_id)
    # 声明一个已存在的正式 NPC id：guest_ 前缀之外的兜底碰撞检查
    real_npc_ids = {
        str(npc.get("id") or "")
        for npc in world.get("npcs", []) or []
        if isinstance(npc, dict) and npc.get("id")
    }
    real_npc_ids |= {str(key) for key in ((world.get("asset_map") or {}).get("npcs") or {})}
    collided = declared & real_npc_ids
    if collided:
        raise ValueError(f"临时人物 id 与正式 NPC 冲突：{sorted(collided)[0]}")
    return registered | declared, declared, scene_id


def _departed_context(host) -> dict:
    """当前场景的已离场记录（decode 的 departed 参数）；读不到时退化为不在场错误。"""
    try:
        world = host.context.world_store.load()
    except Exception:
        return {}
    from src.gameplay.scene_speaker_presence import departed_speakers

    return departed_speakers(world)


def _speaker_display_name(host, speaker_id: str) -> str:
    if not speaker_id:
        return ""
    try:
        world = host.context.world_store.load()
    except Exception:
        return ""
    from src.gameplay.scene_speaker_presence import speaker_display_name

    return speaker_display_name(world, speaker_id)


def emit_structured_dialogue(
    host, raw, parser, *, finish_reason, log_error, report_error=True
) -> str:
    if finish_reason in {"transport_error", "length", "content_filter"}:
        raise ModelResponseError(f"模型结构化对话未完成：{finish_reason}")
    try:
        try:
            parsed = parse_dialogue_output(raw)
        except ValueError as exc:
            raise DialogueFormatError(f"对话 JSON 损坏或结构缺失：{exc}") from exc
        guest_ids, declared_guest_ids, _scene_id = _guest_context(host, parsed)
        from src.ai.model.dialogue_choices import prepare_dialogue_choices

        world = host.context.world_store.load()
        _, original_hint_drops = choice_skill_hint_map(parsed)
        wire = json.loads(raw)
        for field, attr in (("choice_skill_hints", "_turn_choice_hint_dropped"),
                            ("choice_targets", "_turn_choice_policy_dropped")):
            original = wire.get(field, [])
            discarded = max(0, len(original) - len(getattr(parsed, field))) if isinstance(original, list) else 1
            if discarded:
                host.__dict__.setdefault(attr, []).append({"choice_index": 0, "reason": "malformed_metadata", "count": discarded})
        public_history = "\n".join(str(message.get("content") or "")
                                   for message in getattr(host, "messages", [])[-12:]
                                   if message.get("role") == "assistant" and not message.get("tool_calls"))
        parsed, choice_drops = prepare_dialogue_choices(parsed, world, history_text=public_history,
                                                       extra_present=declared_guest_ids)
        raw = parsed.model_dump_json()
        if choice_drops:
            host.__dict__.setdefault("_turn_choice_policy_dropped", []).extend(choice_drops)
        host._turn_choice_targets = {target.choice_index: target.model_dump() for target in parsed.choice_targets}
        # 抵达回合放行出发场景人物的送别台词（turn_allowed_npc_ids）。
        allowed_npc_ids = turn_allowed_npc_ids(host) or set()
        text = sanitize_visible_narrative(
            decode_dialogue_output(
                raw,
                present_npc_ids=allowed_npc_ids,
                guest_speaker_ids=guest_ids,
                departed=_departed_context(host),
            )
        )
        if not text.strip():
            raise ValueError("对话正文没有有效的公开文本")
        # 校验边界统一：在场（decode 已验）还必须身份可解析，不得下游静默降级。
        is_valid_npc = getattr(host, "is_valid_npc_id", None) or parser.is_valid_npc
        for block in parsed.segments:
            if isinstance(block, SpeechBlock) and block.speaker_id not in guest_ids:
                if not is_valid_npc(block.speaker_id):
                    raise ValueError(f"发言者身份无法解析：{block.speaker_id}")
        # 归属疑点：任何 narration 块含成段直接台词（含无引号直呼）。先抛给
        # finish 做一次有界语义复核，复核确认纯旁白才放行，复核修不了就
        # fail-closed；不播出未验证归属的草稿。复核预算独立于格式修复。
        if (
            not getattr(host, "_attribution_review_used", False)
            and (allowed_npc_ids or guest_ids)
            and narration_attribution_suspicion(parsed)
        ):
            raise AttributionSuspicion(parsed)
        # 修复回合的 pre-emit 复读拦截：必须在任何 on_narrative/on_speaker_segment
        # 回调之前判定，事后 raise 拦不住已经展示给玩家的旧回复。
        replay_guard = getattr(host, "_dialogue_repair_guard", None)
        if replay_guard is not None:
            from src.ai.model.dialogue_repair_context import is_replayed_narrative

            if is_replayed_narrative(text, replay_guard):
                raise ValueError("修复结果整段复用上一轮叙事")
    except ValueError as exc:
        from src.gameplay.turn_diagnostics import record_stage

        record_stage(host, "dialogue_validation", "rejected",
                     code=getattr(exc, "category", "invalid_dialogue"),
                     speaker_id=getattr(exc, "speaker_id", ""),
                     source_turn_id=(getattr(exc, "departure", None) or {}).get("turn_id", ""))
        log_error(f"模型结构化对话无效：{exc}")
        if report_error and not isinstance(exc, AttributionSuspicion):
            if "复用上一轮叙事" in str(exc):
                host.cb.on_error(
                    "本轮叙述修复失败：修复结果与上一轮相同，未提交任何内容；请重试本轮。"
                )
            elif isinstance(exc, DialogueValidationError) and exc.category == "departed_speaker":
                name = _speaker_display_name(host, exc.speaker_id)
                who = f"「{name}」" if name else "有角色"
                host.cb.on_error(
                    f"本轮叙述未通过校验：{who}已经离场，不能再在现场发言；"
                    "本轮结果未保存，请重新发送刚才的行动。"
                )
            else:
                host.cb.on_error(
                    "本轮叙述未通过校验：对话格式损坏或发言者无效；"
                    "本轮结果未保存，请重新发送刚才的行动。"
                )
        raise ModelResponseError("模型结构化对话未通过校验") from exc
    # Canonical IDs alone own speech; no prose inference is permitted here.
    # guest id 本轮新声明时引擎身份表尚未登记，发射解析器只认 decode 已放行的集合。
    emit_parser = SpeakerStreamParser(
        is_valid_npc=lambda npc_id: npc_id in guest_ids or parser.is_valid_npc(npc_id),
        on_unknown_npc=parser.on_unknown_npc,
    )
    for kind, chunk, npc_id in emit_parser.feed(text) + emit_parser.flush():
        if kind == "speech_start":
            host.cb.on_speaker_segment(chunk)
        elif kind == "text" and chunk:
            visible = sanitize_visible_narrative(chunk)
            if visible:
                if npc_id:
                    host.cb.on_narrative(visible, npc_id)
                else:
                    host.cb.on_narrative(visible)
    host._turn_guest_speaker_decls = [
        {"speaker_id": block.speaker_id, "display_name": block.display_name}
        for block in parsed.guest_speakers
    ]
    # 选项“可能检定”提议随本轮输出走：平台在定稿时按统一有效技能视图解析
    # 可用性与数值；越界/重复按条丢弃并记入诊断，不影响整轮。
    hint_map, hint_drops = choice_skill_hint_map(parsed)
    hint_drops = original_hint_drops + hint_drops
    host._turn_choice_skill_hints = hint_map
    if hint_drops:
        for entry in hint_drops:
            log_error(f"选项技能提示丢弃：{entry['reason']}（选项序号 {entry['choice_index']}）")
        try:
            host.__dict__.setdefault("_turn_choice_hint_dropped", []).extend(hint_drops)
        except AttributeError:
            pass
    return text


def _allowed_speaker_briefs(host, output, present: set[str], guest_ids: set[str]) -> list[dict]:
    """复核请求里的合法发言者名单：在场正式 NPC + 已登记/本轮声明的临时身份。"""
    try:
        world = host.context.world_store.load()
    except Exception:
        world = {}
    briefs = [
        {"id": npc.get("id"), "name": npc.get("name")}
        for npc in world.get("npcs", []) or []
        if isinstance(npc, dict) and npc.get("id") in present
    ]
    from src.gameplay.guest_speakers import guest_registry

    registry = guest_registry(world)
    declared = {guest.speaker_id: guest.display_name for guest in output.guest_speakers}
    for guest_id in sorted(guest_ids):
        name = (
            declared.get(guest_id) or (registry.get(guest_id) or {}).get("display_name") or guest_id
        )
        briefs.append({"id": guest_id, "name": name})
    return briefs


def _run_attribution_review(
    streamer, model, raw, parser, *, finish_reason, suspicion, report_error
) -> str:
    """一次有界归属复核：确认纯旁白则放行原稿，给出合法修正则播出修正稿。

    复核失败/修正稿不过校验：fail-closed，不播出也不提交可疑草稿。
    """
    host = streamer.host
    host._attribution_review_used = True
    from src.ai.model.dialogue_attribution import (
        AttributionReviewError,
        run_attribution_review,
    )

    present = turn_allowed_npc_ids(host) or set()
    guest_ids, _declared, _scene_id = _guest_context(host, suspicion.output)
    try:
        fixed = run_attribution_review(
            host,
            model,
            suspicion.output,
            present_npc_ids=present,
            guest_speaker_ids=guest_ids,
            allowed_speakers=_allowed_speaker_briefs(host, suspicion.output, present, guest_ids),
            log_error=streamer.log_error,
        )
    except AttributionReviewError as exc:
        host.cb.on_error("本轮叙述的人物归属确认失败，请重试本轮。")
        raise ModelResponseError("对话归属复核未能给出合法修正") from exc
    candidate = (
        raw if fixed is None else json.dumps(fixed.model_dump(mode="json"), ensure_ascii=False)
    )
    return emit_structured_dialogue(
        host,
        candidate,
        parser,
        finish_reason=finish_reason,
        log_error=streamer.log_error,
        report_error=report_error,
    )


def finish_structured_dialogue(streamer, model, raw, parser, *, finish_reason, request_options):
    """One bounded protocol repair, after the previous LLM slot is released."""
    host = streamer.host
    if request_options.pop("required_tool", False):
        host.cb.on_error("战斗行动尚未生成执行请求，请重试本轮。")
        raise ModelResponseError("模型未提交必需的战斗工具请求")
    repair_allowed = request_options["retry_on_empty"] and finish_reason not in {
        "transport_error",
        "length",
        "content_filter",
    }
    try:
        return emit_structured_dialogue(
            host,
            raw,
            parser,
            finish_reason=finish_reason,
            log_error=streamer.log_error,
            report_error=not repair_allowed,
        ), []
    except ModelResponseError as exc:
        suspicion = exc.__cause__ if isinstance(exc.__cause__, AttributionSuspicion) else None
        if suspicion is not None and not getattr(host, "_attribution_review_used", False):
            return _run_attribution_review(
                streamer,
                model,
                raw,
                parser,
                finish_reason=finish_reason,
                suspicion=suspicion,
                report_error=not repair_allowed,
            ), []
        if not repair_allowed:
            raise
        # 类型化的校验错误（DialogueValidationError 子类）原样传给修复上下文，
        # 修复指令按错误类别（JSON 损坏 / 已离场 / 不在场）区分。
        validation_error = exc.__cause__ if isinstance(exc.__cause__, ValueError) else exc
    note_model_retry(host, "invalid_dialogue", "unknown", 0)
    source = request_options["messages_override"]
    from src.ai.model.dialogue_repair_context import (
        previous_assistant_narrative,
        repair_messages,
    )

    source = source if source is not None else host.messages
    from src.gameplay.turn_diagnostics import record_stage

    record_stage(host, "dialogue_repair", "started", code=getattr(validation_error, "category", "invalid_dialogue"))
    messages = repair_messages(host, source, raw, validation_error)
    # 复读拦截与原草稿是否有内容解耦：任何协议修复都不得在解码后整段复制
    # 上一轮长篇叙事。守卫挂在 host 上由 emit_structured_dialogue 在任何
    # 玩家可见回调之前判定；try/finally 保证不泄漏到后续请求。修复请求本身
    # enable_tools=False，不会重复执行已结算的工具或检定。
    guard = previous_assistant_narrative(source)
    if guard:
        host._dialogue_repair_guard = guard
    try:
        return streamer.stream(
            model,
            **(
                request_options
                | {
                    "messages_override": messages,
                    "system_overlay": None,
                    "system_prompt_override": None,
                    "enable_tools": False,
                    "temperature": 0.0,
                    "retry_on_empty": False,
                }
            ),
        )
    finally:
        if guard:
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
