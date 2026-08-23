"""Conversation history compaction policy outside the game engine."""

from __future__ import annotations

import json
import re
from typing import Any

from .context_summary import is_control_message, validate_summary_visibility
from .llm_concurrency import llm_call_slot
from .logger import summary_event as log_summary

# Tool results older than the keep-recent window are pruned in place once
# they exceed this size (raw events stay in the context event log).
TOOL_RESULT_PRUNE_MIN_CHARS = 500

# 每轮玩家行动/控制消息内嵌的当轮权威状态块起始标记；过期后可整体移除。
AUTHORITY_MARKER = "[引擎权威状态｜仅供守秘人，不得复述]"


def build_summary_input(messages: list[dict]) -> str:
    parts = []
    tool_names: dict[str, str] = {}
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content", "") or ""
        # Engine control messages contain authority snapshots, private Skill
        # text and other keeper-only material. A summary model never needs
        # them: fresh authority is rebuilt by the engine on the next turn.
        if is_control_message(message):
            continue
        if role == "assistant":
            # Provider assistant tool_calls usually have empty content.  Keep
            # the public call identity (not raw arguments) so a summary can
            # preserve event ordering without handing arbitrary tool payloads
            # to a second model.
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    function = call.get("function") if isinstance(call, dict) else None
                    call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
                    name = str(function.get("name") or "") if isinstance(function, dict) else ""
                    if call_id and name:
                        tool_names[call_id] = name
                        parts.append(f"[assistant_tool_call]: {name} (id={call_id})")
            if not content.strip():
                continue
            content = content[:500] + "..." if len(content) > 500 else content
        elif role == "tool":
            # Tool results may contain private keeper projections or output
            # from a previously unsafe tool.  Do not send raw result text to
            # the summarizer.  Canonical dice/resources/clues remain in the
            # authoritative WorldState/Turn records rebuilt each turn.
            call_id = str(message.get("tool_call_id") or "")
            name = tool_names.get(call_id, "tool")
            parts.append(
                f"[tool_result]: {name} (id={call_id or 'unknown'}), authoritative result recorded"
            )
            continue
        elif role == "user":
            if not content.strip():
                continue
            content = content[:500] + "..." if len(content) > 500 else content
        else:
            continue
        parts.append(f"[{role}]: {content}")
    text = "\n".join(parts)
    if len(text) > 6000:
        text = text[:3000] + "\n...(中间内容省略)...\n" + text[-3000:]
    return text


def parse_summary_json(raw: str) -> str | None:
    """Extract valid JSON from common provider formatting/truncation variants."""
    if not raw:
        return None
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            pass
    if start < 0:
        return None
    candidate = re.sub(r",\s*$", "", raw[start:])
    braces = brackets = 0
    in_string = escaped = False
    for character in candidate:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            in_string = not in_string
        elif not in_string:
            braces += (character == "{") - (character == "}")
            brackets += (character == "[") - (character == "]")
    candidate += "]" * max(brackets, 0) + "}" * max(braces, 0)
    try:
        json.loads(candidate)
        return candidate
    except (json.JSONDecodeError, ValueError):
        return None


class HistoryCompactor:
    def __init__(self, engine: Any):
        self.engine = engine

    def estimate_tokens(self) -> int:
        total = 0
        for message in self.engine.messages:
            total += len(message.get("content", "") or "") // 2
            if "tool_calls" in message:
                total += len(json.dumps(message["tool_calls"])) // 2
        self.engine._summary_token_estimate = total
        return total

    def should_summarize(self) -> bool:
        engine = self.engine
        if not (
            engine._player_turn_count > 0
            and engine._player_turn_count - engine._last_summary_player_turn
            >= engine.SUMMARY_PLAYER_TURN_INTERVAL
        ):
            return False
        # A transient provider failure should not suppress compaction for 50
        # more turns, nor should it cause a summary request after every
        # action.  This session-local backoff is intentionally small and
        # never changes authoritative state.
        failed_at = engine.__dict__.get("_last_summary_failure_player_turn")
        return not isinstance(failed_at, int) or engine._player_turn_count - failed_at >= 3

    def maybe_after_turn(self) -> None:
        pruned = self.prune_old_tool_results()
        changed = False
        if self.should_summarize():
            current_turn = self.engine._player_turn_count
            changed = self.summarize(silent=True)
            # A failed summary leaves the surface untouched.  Do not pretend
            # it ran successfully and postpone the next opportunity by the
            # full 50-turn interval; retry after a small, in-memory backoff.
            if changed:
                self.engine._last_summary_player_turn = current_turn
                self.engine.__dict__.pop("_last_summary_failure_player_turn", None)
            else:
                self.engine.__dict__["_last_summary_failure_player_turn"] = current_turn
        if pruned or changed:
            self.engine.save("slot_000")

    def _compaction_cutoff(self) -> int:
        """Window end for compaction: walked back to a user-message boundary.

        Cutting immediately before a user message can never split an
        assistant ``tool_calls`` batch from its ``tool`` results, because a
        batch's results always precede the next user message.
        """
        messages = self.engine.messages
        cutoff = len(messages) - self.engine.SUMMARY_KEEP_RECENT_MESSAGES
        while cutoff > 1 and messages[cutoff].get("role") != "user":
            cutoff -= 1
        return cutoff

    def _replace_window(
        self,
        replacement: dict,
        recent_messages: list[dict],
        *,
        allow_rebase_fallback: bool,
    ) -> bool:
        """Swap the old window for ``replacement``, non-destructively first.

        Preferred path: one ``replace`` checkpoint masks the window in the
        context event log (raw events survive).  Only when the shadow cannot
        prove projection == messages do we fall back to the legacy rebase
        (fresh root + seed), and never during an active turn.
        """
        from .context_shadow import compact_engine, rebase_engine

        engine = self.engine
        system_message = engine.messages[0]
        cutoff = len(engine.messages) - len(recent_messages)
        if compact_engine(engine, 1, cutoff, replacement):
            engine.messages = [system_message, replacement, *recent_messages]
            return True
        if not allow_rebase_fallback:
            return False
        original_messages = list(engine.messages)
        candidate = [system_message, replacement, *recent_messages]
        engine.messages = candidate
        if rebase_engine(engine):
            return True
        # ``rebase_engine`` is the durable fallback only.  If the shadow
        # store cannot prove the new epoch, keep the prior model surface
        # exactly intact rather than turning a storage incident into history
        # loss.
        engine.messages = original_messages
        return False

    def prune_old_tool_results(self) -> int:
        """Replace bulky old tool results with digest placeholders in place.

        Only messages outside the keep-recent window are pruned; the
        placeholder keeps ``tool_call_id`` so call/result pairing survives.
        Each prune is one single-source ``replace`` checkpoint — the raw
        result stays in the event log.  Returns the number of pruned results.
        """
        from .context_events import payload_digest
        from .context_shadow import prune_engine

        engine = self.engine
        messages = engine.messages
        cutoff = self._compaction_cutoff()
        if cutoff <= 1:
            return 0
        replacements: dict[int, dict] = {}
        for index in range(1, cutoff):
            message = messages[index]
            if message.get("role") != "tool":
                continue
            content = str(message.get("content") or "")
            if len(content) <= TOOL_RESULT_PRUNE_MIN_CHARS:
                continue
            # Preserve a bounded public head/tail so the next narration can
            # still see dice values, object ids and final state.  Raw output
            # remains in the append-only event log; the marker makes that
            # provenance explicit without duplicating an unbounded payload.
            head = content[:160]
            tail = content[-160:] if len(content) > 320 else ""
            excerpt = head
            if tail:
                excerpt += "\n…（中间已修剪）…\n" + tail
            placeholder = {
                "role": "tool",
                "content": (
                    f"（工具结果已修剪：原 {len(content)} 字符，"
                    f"digest {payload_digest(content)[:12]}）\n{excerpt}"
                ),
            }
            if message.get("tool_call_id"):
                placeholder["tool_call_id"] = message["tool_call_id"]
            replacements[index] = placeholder
        applied = prune_engine(engine, replacements)
        for index in applied:
            messages[index] = replacements[index]
        if applied:
            self.estimate_tokens()
        return len(applied)

    def prune_stale_authority_blocks(self) -> int:
        """Strip stale authority snapshots from all but the latest user message.

        每条玩家行动/控制消息都内嵌一份当轮的引擎权威状态 JSON（数千字符），
        它只对当轮有效；留在历史里既是死重也是过期状态。keep-recent 下限按
        消息条数计算，够不到这种「条数少但单条大」的历史，必须按内容修剪。
        非破坏：每处修剪都是一条 shadow replace checkpoint，原文留在事件日志。
        """
        from .context_shadow import prune_engine

        engine = self.engine
        messages = engine.messages
        last_user_index = -1
        for index, message in enumerate(messages):
            if message.get("role") == "user":
                last_user_index = index
        replacements: dict[int, dict] = {}
        for index, message in enumerate(messages):
            if index >= last_user_index or message.get("role") != "user":
                continue
            content = str(message.get("content") or "")
            marker_at = content.find(AUTHORITY_MARKER)
            if marker_at < 0:
                continue
            kept = content[:marker_at].rstrip()
            placeholder = {
                **message,
                "content": kept + "\n\n（该轮权威状态快照已过期并移除）",
            }
            replacements[index] = placeholder
        applied = prune_engine(engine, replacements)
        for index in applied:
            messages[index] = replacements[index]
        if applied:
            self.estimate_tokens()
        return len(applied)

    def prune_asset_payloads(self) -> int:
        """Strip historical asset_data_uri payloads from tool results.

        base64 图片载荷只在当次 WS 投递时有意义（投递在工具执行期间已完成），
        留在历史里每条都是数百 KB 死重，且单条即可超过整个上下文窗口。
        它不属于叙事上下文，因此不受 keep-recent 窗口保护——全部历史都剥。
        非破坏：每处剥离都是一条 shadow replace checkpoint，原文留在事件日志。
        """
        from .context_shadow import prune_engine
        from .tool_aux_handlers import strip_asset_payloads

        engine = self.engine
        messages = engine.messages
        replacements: dict[int, dict] = {}
        for index in range(1, len(messages)):
            message = messages[index]
            if message.get("role") != "tool":
                continue
            content = str(message.get("content") or "")
            if "asset_data_uri" not in content:
                continue
            stripped = strip_asset_payloads(content)
            if stripped == content:
                continue
            replacements[index] = {**message, "content": stripped}
        applied = prune_engine(engine, replacements)
        for index in applied:
            messages[index] = replacements[index]
        if applied:
            self.estimate_tokens()
        return len(applied)

    def summarize(self, *, silent: bool = False, allow_rebase_fallback: bool = True) -> bool:
        from .llm import _get_glm

        engine = self.engine
        cutoff = self._compaction_cutoff()
        if cutoff <= 1:
            return False
        old_messages = engine.messages[1:cutoff]
        if len(old_messages) < 3:
            return False
        recent_messages = engine.messages[cutoff:]
        system_message = engine.messages[0]
        old_text = build_summary_input(old_messages)

        def safe_summary(candidate: str | None) -> str | None:
            if candidate is None:
                return None
            try:
                world = engine.context.world_store.load()
            except Exception:  # noqa: BLE001 - visibility guard must fail closed
                self._record_summary_rejection("world_state_unavailable")
                return None
            result = validate_summary_visibility(candidate, world)
            if not result.allowed:
                self._record_summary_rejection(result.reason or "visibility_guard")
                return None
            return candidate

        glm = _get_glm()
        if glm is not None:
            summary = safe_summary(self.try_model(glm, "glm-4-flash-250414", old_text))
            if summary is not None:
                return self.apply(
                    system_message,
                    summary,
                    recent_messages,
                    "GLM-4 Flash",
                    silent,
                    allow_rebase_fallback=allow_rebase_fallback,
                    replaced_messages=old_messages,
                )
        if not silent:
            engine.cb.on_tension("正在用 DeepSeek Pro 压缩上下文……", "pro")
        summary = safe_summary(self.try_model(engine.client, engine.judgement_model, old_text))
        if summary is not None:
            return self.apply(
                system_message,
                summary,
                recent_messages,
                "DeepSeek Pro",
                silent,
                allow_rebase_fallback=allow_rebase_fallback,
                replaced_messages=old_messages,
            )
        # H2 deliberately rejects the legacy "truncate on failure" fallback.
        # Raw events surviving in the timeline is not sufficient if the active
        # model surface was replaced by a fabricated truncation note. Failures,
        # timeouts and visibility rejects leave the surface unchanged.
        self._record_summary_rejection("summarizer_unavailable")
        return False

    def _record_summary_rejection(self, reason: str) -> None:
        """Record metadata only; never persist candidate summary text."""
        diagnostic = getattr(self.engine, "_append_model_diagnostic", None)
        if callable(diagnostic):
            diagnostic({"event": "context_summary_rejected", "reason": reason})

    def apply(
        self,
        system_message: dict,
        summary: str,
        recent_messages: list[dict],
        model_name: str,
        silent: bool = False,
        *,
        allow_rebase_fallback: bool = True,
        replaced_messages: list[dict] | None = None,
    ) -> bool:
        summary_message = {
            "role": "user",
            "content": (
                "（会话摘要——此前冒险的关键记录已压缩如下。"
                "技能检定、已发现线索、NPC互动记录均已保留。\n\n"
                f"{summary}\n\n——摘要结束。以下是最近的对话——）"
            ),
        }
        if replaced_messages is not None:
            original_chars = len(
                json.dumps(replaced_messages, ensure_ascii=False, separators=(",", ":"))
            )
            replacement_chars = len(summary_message["content"])
            if replacement_chars >= original_chars:
                self._record_summary_rejection("summary_not_smaller")
                return False
        if not self._replace_window(
            summary_message, recent_messages, allow_rebase_fallback=allow_rebase_fallback
        ):
            return False
        self.estimate_tokens()
        from .turn_performance import increment_counter

        increment_counter(self.engine, "context_compactions")
        if replaced_messages:
            # 压缩比（%）：替换后字符数 / 被替换范围字符数，只记比率不落摘要文本。
            increment_counter(
                self.engine,
                "context_compaction_ratio_pct",
                max(0, min(100, replacement_chars * 100 // max(1, original_chars))),
            )
        log_summary(model_name, "成功")
        if not silent:
            self.engine.cb.on_glm_summary(f"📋 上下文已压缩（{model_name}）。")
        return True

    @staticmethod
    def try_model(client: Any, model: str, old_text: str) -> str | None:
        prompt = (
            "你是TRPG记录员。将以下对话历史压缩为结构化摘要。按时间顺序保留"
            "关键事件、PC已知信息、已发现线索、当前目标、最后场景、技能检定和骰子结果；"
            "不得编造信息。优先输出JSON。\n\n"
            f"{old_text}"
        )
        for attempt in range(2):
            try:
                with llm_call_slot(model=model):
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "你是TRPG记录员。保证信息完整。"},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.3,
                        max_tokens=3000,
                    )
                raw = response.choices[0].message.content.strip()
            except Exception as exc:
                if attempt == 0:
                    continue
                # H4：摘要调用的失败也归_adapter 稳定错误类，便于离线诊断。
                from .provider_adapter import classify_provider_error

                log_summary(model, f"失败: {classify_provider_error(exc)}")
                return None
            parsed = parse_summary_json(raw)
            if parsed is not None:
                return parsed
        return None
