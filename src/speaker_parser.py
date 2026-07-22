"""NPC 发言标签解析：【npc:id】…【/npc】。

模型按叙述契约用标签包裹 NPC 直接引语；本模块是发言者归因的唯一解析点。
规则：
- 有效 NPC id 的开标签立即开始发言单元；闭标签、空行或下一个开标签结束该单元；
- 兼容模型遗漏闭标签，以及旧提示词曾产生的 ``⟧`` 右括号；
- 未知 id 按守秘人旁白处理并剥离标签；
- 标签在所有情况下都会从输出文本中剥离，绝不泄漏给玩家或消息历史。

输出为有序的 Piece 序列（流式与定稿同一条状态机，天然幂等）：
    ("speech_start", npc_id)  — 发言段开始
    ("text", text, npc_id|None) — 文本片段（npc_id 非空表示发言段文本）
    ("speech_end", None)      — 发言段结束
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

OPEN_PREFIXES = ("【npc:", "[npc:")
CLOSE_PREFIXES = ("【/npc", "[/npc")
_BRACKETS = ("【", "[")
# 【npc: + 最长 NPC id + 】，超过即视为不可能构成开标签
_MAX_OPEN_TAG_LEN = 64

# piece = (kind, text, npc_id)
#   kind: "speech_start" | "text" | "speech_end"
Piece = tuple[str, str, str | None]


@dataclass
class Segment:
    kind: str  # "narration" | "speech"
    text: str
    npc_id: str | None = None

    def to_dict(self) -> dict:
        out = {"kind": self.kind, "text": self.text}
        if self.npc_id:
            out["npc_id"] = self.npc_id
        return out


@dataclass
class SpeakerStreamParser:
    """【npc:id】…【/npc】 增量解析器（单例喂入模型增量文本）。"""

    is_valid_npc: Callable[[str], bool] = lambda _npc_id: True
    on_unknown_npc: Callable[[str], None] | None = None
    _buf: str = field(default="", init=False)
    _in_speech: bool = field(default=False, init=False)
    _speech_npc: str | None = field(default=None, init=False)

    def feed(self, delta: str) -> list[Piece]:
        """喂入一段模型增量文本，返回有序 Piece 序列。"""
        if not delta:
            return []
        self._buf += delta
        pieces: list[Piece] = []
        while True:
            indices = [self._buf.find(char) for char in _BRACKETS]
            if self._in_speech:
                indices.append(self._buf.find("\n\n"))
            indices = [idx for idx in indices if idx >= 0]
            idx = min(indices) if indices else -1
            if idx < 0:
                # 发言末尾的单个换行先保留，下一 delta 可能补成空行边界。
                held = "\n" if self._in_speech and self._buf.endswith("\n") else ""
                visible = self._buf[: -len(held)] if held else self._buf
                self._emit_text(pieces, visible)
                self._buf = held
                break
            self._emit_text(pieces, self._buf[:idx])
            self._buf = self._buf[idx:]
            if self._in_speech and self._buf.startswith("\n\n"):
                self._in_speech = False
                self._speech_npc = None
                pieces.append(("speech_end", "", None))
                pieces.append(("text", "\n\n", None))
                self._buf = self._buf[2:]
                continue
            consumed = self._consume_construct(pieces)
            if consumed == 0:
                break  # 半个标签，等待更多输入
            self._buf = self._buf[consumed:]
        return pieces

    def _emit_text(self, pieces: list[Piece], text: str) -> None:
        if text:
            pieces.append(("text", text, self._speech_npc if self._in_speech else None))

    def _consume_construct(self, pieces: list[Piece]) -> int:
        """处理 _buf 开头的方括号构念，返回消耗字符数；0 表示需等待。"""
        buf = self._buf
        bracket = buf[0]
        if not self._in_speech:
            open_prefix = next(
                (prefix for prefix in OPEN_PREFIXES if buf.startswith(prefix)),
                None,
            )
            if open_prefix:
                closing_brackets = ("】", "⟧") if open_prefix.startswith("【") else ("]",)
                ends = [buf.find(char, len(open_prefix)) for char in closing_brackets]
                ends = [end for end in ends if end >= 0]
                end = min(ends) if ends else -1
                if end < 0:
                    return 0 if len(buf) <= _MAX_OPEN_TAG_LEN else 1
                npc_id = buf[len(open_prefix) : end].strip()
                if npc_id and self.is_valid_npc(npc_id):
                    self._in_speech = True
                    self._speech_npc = npc_id
                    pieces.append(("speech_start", npc_id, None))
                elif self.on_unknown_npc and npc_id:
                    self.on_unknown_npc(npc_id)
                return end + 1
            close_prefix = next(
                (prefix for prefix in CLOSE_PREFIXES if buf.startswith(prefix)),
                None,
            )
            if close_prefix:
                closing = "】" if close_prefix.startswith("【") else "]"
                end = buf.find(closing, len(close_prefix))
                if end < 0:
                    return 0 if len(buf) <= _MAX_OPEN_TAG_LEN else 1
                return end + 1  # 游离闭标签（含 【/npc:id】 变体），剥离
            if any(prefix.startswith(buf) for prefix in (*CLOSE_PREFIXES, *OPEN_PREFIXES)):
                return 0  # 半个标签前缀
            self._emit_text(pieces, bracket)
            return 1  # 非标签的 ⟦，按普通文本输出
        # 发言段内
        if any(buf.startswith(prefix) for prefix in OPEN_PREFIXES):
            # 模型常把开标签当作逐段 speaker marker 使用；新标记自动收束旧发言。
            self._in_speech = False
            self._speech_npc = None
            pieces.append(("speech_end", "", None))
            return self._consume_construct(pieces)
        close_prefix = next(
            (prefix for prefix in CLOSE_PREFIXES if buf.startswith(prefix)),
            None,
        )
        if close_prefix:
            closing = "】" if close_prefix.startswith("【") else "]"
            end = buf.find(closing, len(close_prefix))
            if end < 0:
                return 0 if len(buf) <= _MAX_OPEN_TAG_LEN else 1
            self._in_speech = False
            self._speech_npc = None
            pieces.append(("speech_end", "", None))
            return end + 1
        if any(prefix.startswith(buf) for prefix in CLOSE_PREFIXES):
            return 0
        self._emit_text(pieces, bracket)
        return 1  # 嵌套开标签或其他 ⟦，按文本处理

    def flush(self) -> list[Piece]:
        """流末尾释放缓冲：半个标签按普通文本释放。

        流结束即收束发言；这使仅有开标签的模型输出仍可稳定归因。
        """
        pieces: list[Piece] = []
        if self._buf:
            self._emit_text(pieces, self._buf)
            self._buf = ""
        if self._in_speech:
            pieces.append(("speech_end", "", None))
        self._in_speech = False
        self._speech_npc = None
        return pieces


def pieces_to_segments(pieces: list[Piece]) -> list[Segment]:
    """把 Piece 序列折叠为段列表（定稿口径：未闭合发言段归入旁白）。"""
    segments: list[Segment] = []
    narration: list[str] = []
    speech: list[str] = []
    speech_npc: str | None = None
    speech_closed = True

    def close_narration() -> None:
        text = "".join(narration).strip("\n")
        narration.clear()
        if text.strip():
            segments.append(Segment(kind="narration", text=text))

    def close_speech() -> None:
        text = "".join(speech).strip("\n")
        speech.clear()
        if text.strip():
            segments.append(Segment(kind="speech", text=text, npc_id=speech_npc))

    for kind, text, npc_id in pieces:
        if kind == "speech_start":
            close_narration()
            speech = []
            speech_npc = text
            speech_closed = False
        elif kind == "speech_end":
            close_speech()
            speech_npc = None
            speech_closed = True
        elif npc_id is not None:
            speech.append(text)
        else:
            narration.append(text)
    # 收尾：未闭合发言段的文本归入旁白
    if not speech_closed:
        narration.extend(speech)
    close_narration()

    # 相邻旁白合并
    merged: list[Segment] = []
    for seg in segments:
        if merged and merged[-1].kind == seg.kind == "narration":
            merged[-1].text = merged[-1].text.rstrip("\n") + "\n\n" + seg.text.lstrip("\n")
        else:
            merged.append(seg)
    return merged


def parse_segments(
    full_text: str,
    is_valid_npc: Callable[[str], bool] | None = None,
    on_unknown_npc: Callable[[str], None] | None = None,
    speaker_aliases: dict[str, str] | None = None,
) -> tuple[list[Segment], str]:
    """权威整段解析：返回 (segments, clean_text)。与增量路径同一状态机。"""
    parser = SpeakerStreamParser(
        is_valid_npc=is_valid_npc or (lambda _npc_id: True),
        on_unknown_npc=on_unknown_npc,
    )
    pieces = parser.feed(full_text) + parser.flush()
    clean_text = "".join(text for kind, text, _ in pieces if kind == "text")
    segments = pieces_to_segments(pieces)
    if speaker_aliases:
        segments = infer_named_speech(segments, speaker_aliases)
    return segments, clean_text


_NAMED_LINE = re.compile(
    r"^(?P<indent>\s*)(?:\*\*|__)?(?P<name>[^：:\n]{1,40})(?:\*\*|__)?\s*[：:]\s*(?P<text>.+)$"
)


def infer_named_speech(
    segments: list[Segment], speaker_aliases: dict[str, str]
) -> list[Segment]:
    """Recover explicit ``姓名：台词`` lines when the model omitted NPC tags.

    Only server-provided, known public names are accepted. Arbitrary labels are kept as
    narration, so headings such as ``线索：`` cannot invent a speaker identity.
    """
    normalized = {
        re.sub(r"\s+", "", name).casefold(): npc_id
        for name, npc_id in speaker_aliases.items()
        if name and npc_id
    }
    recovered: list[Segment] = []
    for segment in segments:
        if segment.kind != "narration":
            recovered.append(segment)
            continue
        narration_lines: list[str] = []

        def flush_narration(lines: list[str] = narration_lines) -> None:
            text = "\n".join(lines).strip("\n")
            lines.clear()
            if text.strip():
                recovered.append(Segment(kind="narration", text=text))

        for line in segment.text.splitlines():
            match = _NAMED_LINE.match(line)
            key = re.sub(r"\s+", "", match.group("name")).casefold() if match else ""
            npc_id = normalized.get(key)
            if not match or not npc_id:
                narration_lines.append(line)
                continue
            flush_narration()
            recovered.append(
                Segment(kind="speech", text=match.group("text").strip(), npc_id=npc_id)
            )
        flush_narration()

    merged: list[Segment] = []
    for segment in recovered:
        if (
            merged
            and merged[-1].kind == segment.kind
            and merged[-1].npc_id == segment.npc_id
        ):
            merged[-1].text = merged[-1].text.rstrip() + "\n\n" + segment.text.lstrip()
        else:
            merged.append(segment)
    return merged
