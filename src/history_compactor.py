"""Conversation history compaction policy outside the game engine."""

from __future__ import annotations

import json
import re
from typing import Any

from .llm_concurrency import llm_call_slot
from .logger import summary_event as log_summary

# Tool results older than the keep-recent window are pruned in place once
# they exceed this size (raw events stay in the context event log).
TOOL_RESULT_PRUNE_MIN_CHARS = 500


def build_summary_input(messages: list[dict]) -> str:
    parts = []
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content", "") or ""
        if not content.strip():
            continue
        if role == "tool":
            content = content[:200] + "..." if len(content) > 200 else content
        elif role in ("user", "assistant"):
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
        return (
            engine._player_turn_count > 0
            and engine._player_turn_count - engine._last_summary_player_turn
            >= engine.SUMMARY_PLAYER_TURN_INTERVAL
        )

    def maybe_after_turn(self) -> None:
        pruned = self.prune_old_tool_results()
        changed = False
        if self.should_summarize():
            current_turn = self.engine._player_turn_count
            changed = self.summarize(silent=True)
            self.engine._last_summary_player_turn = current_turn
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
        engine.messages = [system_message, replacement, *recent_messages]
        rebase_engine(engine)
        return True

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
            placeholder = {
                "role": "tool",
                "content": (
                    f"（工具结果已修剪：原 {len(content)} 字符，"
                    f"digest {payload_digest(content)[:12]}）"
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
        glm = _get_glm()
        if glm is not None:
            summary = self.try_model(glm, "glm-4-flash-250414", old_text)
            if summary is not None:
                return self.apply(
                    system_message,
                    summary,
                    recent_messages,
                    "GLM-4 Flash",
                    silent,
                    allow_rebase_fallback=allow_rebase_fallback,
                )
        if not silent:
            engine.cb.on_tension("正在用 DeepSeek Pro 压缩上下文……", "pro")
        summary = self.try_model(engine.client, engine.judgement_model, old_text)
        if summary is not None:
            return self.apply(
                system_message,
                summary,
                recent_messages,
                "DeepSeek Pro",
                silent,
                allow_rebase_fallback=allow_rebase_fallback,
            )
        dropped = len(old_messages)
        note = {
            "role": "user",
            "content": (
                f"（上下文压缩——摘要模型均不可用，已丢弃最早的 {dropped} 条消息。"
                "当前世界状态保存在 world_state.json 中，请查询线索和 NPC 状态后继续。）"
            ),
        }
        if not self._replace_window(
            note, recent_messages, allow_rebase_fallback=allow_rebase_fallback
        ):
            return False
        self.estimate_tokens()
        if not silent:
            engine.cb.on_glm_summary(f"📋 截断 {dropped} 条旧消息（摘要模型不可用）。")
        return True

    def apply(
        self,
        system_message: dict,
        summary: str,
        recent_messages: list[dict],
        model_name: str,
        silent: bool = False,
        *,
        allow_rebase_fallback: bool = True,
    ) -> bool:
        summary_message = {
            "role": "user",
            "content": (
                "（会话摘要——此前冒险的关键记录已压缩如下。"
                "技能检定、已发现线索、NPC互动记录均已保留。\n\n"
                f"{summary}\n\n——摘要结束。以下是最近的对话——）"
            ),
        }
        if not self._replace_window(
            summary_message, recent_messages, allow_rebase_fallback=allow_rebase_fallback
        ):
            return False
        self.estimate_tokens()
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
            except Exception:
                if attempt == 0:
                    continue
                return None
            parsed = parse_summary_json(raw)
            if parsed is not None:
                return parsed
            if len(raw) > 50 and attempt == 1:
                return f"（纯文本摘要）\n{raw}"
        return None
