"""NPC 发言标签解析：【npc:id】…【/npc】。

模型按叙述契约用标签包裹 NPC 直接引语；本模块是发言者归因的唯一解析点。
规则：
- 有效 NPC id 的开标签立即开始发言单元；闭标签、空行或下一个开标签结束该单元；
- 兼容模型遗漏闭标签，以及旧提示词曾产生的 ``⟧`` 右括号；
- 未知 id 按守秘人旁白处理并剥离标签；
- 标签在所有情况下都会从输出文本中剥离，绝不泄漏给玩家或消息历史。
- 定稿兼容缺失标签时，只恢复同一行、已知 NPC 作为明确说话主语的引语；
  不从上一行继承发言者，也不把调查员对 NPC 的话猜成 NPC 台词。

输出为有序的 Piece 序列（流式与定稿同一条状态机，天然幂等）：
    ("speech_start", npc_id)  — 发言段开始
    ("text", text, npc_id|None) — 文本片段（npc_id 非空表示发言段文本）
    ("speech_end", None)      — 发言段结束
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
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
    player_text: str | None = None,
    present_npc_ids: Iterable[str] | None = None,
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
        segments = infer_named_speech(
            segments,
            speaker_aliases,
            player_text=player_text,
            present_npc_ids=present_npc_ids,
        )
    return segments, clean_text


_NAMED_LINE = re.compile(
    r"^(?P<indent>\s*)(?:\*\*|__)?(?P<name>[^：:\n]{1,40})(?:\*\*|__)?\s*[：:]\s*(?P<text>.+)$"
)
_CJK_QUOTED_SPEECH_PATTERN = (
    r"“(?P<fullwidth>.*?)”"
    r"|「(?P<corner>.*?)」"
    r"|『(?P<double_corner>.*?)』"
)
_CJK_QUOTED_SPEECH = re.compile(_CJK_QUOTED_SPEECH_PATTERN)


@dataclass(frozen=True)
class _QuotedSpeech:
    start: int
    end: int
    text: str
    body: str


def _paired_quoted_speech(line: str) -> list[_QuotedSpeech]:
    """Return matching quote pairs; ASCII quotes pair sequentially.

    模型实际上从不使用发言标签，且大量混用直引号（一行多对）。
    直引号按出现顺序两两配对；奇数个说明引号不配对（撇号/嵌套损坏），
    边界不可信，整行 ASCII 引号放弃（fail closed），CJK 引号不受影响。
    """
    cjk_matches = list(_CJK_QUOTED_SPEECH.finditer(line))
    cjk_spans = [match.span() for match in cjk_matches]

    def in_cjk_span(index: int) -> bool:
        return any(start <= index < end for start, end in cjk_spans)

    quotes: list[_QuotedSpeech] = [
        _QuotedSpeech(
            start=match.start(),
            end=match.end(),
            text=match.group(0),
            body=next(
                value
                for value in (
                    match.group("fullwidth"),
                    match.group("corner"),
                    match.group("double_corner"),
                )
                if value is not None
            ),
        )
        for match in cjk_matches
    ]

    unescaped: list[int] = []
    for index, char in enumerate(line):
        if char != '"' or in_cjk_span(index):
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            unescaped.append(index)
    if len(unescaped) % 2 == 0:
        for start, end in zip(unescaped[0::2], unescaped[1::2], strict=True):
            quotes.append(
                _QuotedSpeech(
                    start=start,
                    end=end + 1,
                    text=line[start : end + 1],
                    body=line[start + 1 : end],
                )
            )
    return sorted(quotes, key=lambda quote: quote.start)


# 未加标签的小说体台词只能在归属足够明确时恢复为 NPC 气泡。这里故意
# 保守：错把玩家对 NPC 说的话渲染成 NPC 发言，比少恢复一条 NPC 气泡更糟。
_SPEECH_CUE = r"(?:说(?:道)?|答(?:道)?|回答(?:道)?|问(?:道)?|道|表示|解释(?:道)?|告诉|回应(?:道)?|应声|喊(?:道)?|叫(?:道)?|喃喃(?:道|说)?|补充(?:道)?|承认(?:道)?|提醒(?:道)?|吩咐(?:道)?)"
_SPEECH_MANNER = (
    r"(?:低声|轻声|缓缓|冷冷|平静地|郑重地|迟疑地|不紧不慢地|压低声音|开口|终于|随即|接着|又)"
)
_STAGE_DIRECTION = r"(?:望向[^，。！？!?\n]{0,12}|看向[^，。！？!?\n]{0,12}|看着[^，。！？!?\n]{0,12}|抬头|低下头|皱眉|叹了口气|沉默片刻|停顿片刻|顿了顿|微笑着|笑了笑|轻叹一声)"
_ADDRESS_MARKER = r"(?:对|向|朝|跟|同)"
# Do not accept an arbitrary short Chinese phrase as the addressee.  Chinese
# verbs such as ``答应`` / ``问候`` / ``回应`` otherwise get split into a
# speaking verb plus a fictional target, which is exactly the kind of false
# NPC attribution this fallback must avoid.  Actual character names are
# deliberately not guessed here: the tagged / ``姓名：`` paths remain the
# reliable route for them.
_TRUSTED_ADDRESS_TARGET = (
    r"(?:你(?:们)?|您(?:们)?|调查员(?:们)?|各位|大家|[一-龥]{1,3}(?:先生|女士|医生|警官))"
)
_DIRECT_SPEECH_BOUNDARY = r"(?=\s*(?:$|[，,。！？!?；;:：]))"
_TARGET_COMPLETION = r"(?=\s*(?:$|[。！？!?；;:：]))"
# A direct cue can be followed by a short stage direction (``法伦答道，示意…``)
# because the known NPC is still unambiguously the grammatical speaker.
_TERMINAL_SPEECH_CUE = rf"{_SPEECH_CUE}{_DIRECT_SPEECH_BOUNDARY}"
_ADDRESS_SPEECH = (
    rf"{_ADDRESS_MARKER}{_TRUSTED_ADDRESS_TARGET}"
    rf"{_SPEECH_CUE}{_TARGET_COMPLETION}"
)


def _is_explicit_npc_speaker_phrase(text: str, name: str, *, at_start: bool) -> bool:
    """Return whether ``text`` explicitly attributes nearby speech to ``name``.

    ``at_start`` is used for the prose after a closing quote.  Before a quote we
    require the NPC name to begin a clause, so ``黄千陆对/向/问法伦说`` cannot
    accidentally match the addressed NPC as the speaker.
    """
    escaped_name = re.escape(name)
    # A preceding closing CJK quote is also a clause boundary.  This lets
    # ``“玩家原话。”法伦说道：“NPC 台词。”`` attach the attribution to the
    # following quote, while the caller separately prevents it from relabelling
    # the preceding one.
    prefix = r"^\s*" if at_start else r"(?:^|[。！？!?；;，,」』”\n])\s*"
    ending = r"\s*[:：]?" if at_start else r"\s*[:：]?$"
    speaker_phrase = (
        rf"(?:{_SPEECH_MANNER}\s*)?"
        rf"(?:{_TERMINAL_SPEECH_CUE}|{_ADDRESS_SPEECH})"
    )
    direct = rf"{prefix}{escaped_name}\s*{speaker_phrase}{ending}"
    matcher = re.match if at_start else re.search
    if matcher(direct, text):
        return True

    # Keep a narrow, still explicit novel form such as
    # ``法伦望向窗外，低声说：‘……’``.  Arbitrary prose is not permitted here:
    # it is too easy for another character to become the actual grammatical
    # subject before the speaking verb.
    staged = (
        rf"{prefix}{escaped_name}\s*{_STAGE_DIRECTION}\s*[，,]\s*"
        rf"(?:{_SPEECH_MANNER}\s*)?{_TERMINAL_SPEECH_CUE}{ending}"
    )
    return bool(matcher(staged, text))


def _explicit_npc_owner_before_quote(before: str, aliases: dict[str, str]) -> str | None:
    """Find a same-line, clause-subject attribution ending at a quote."""
    for name in sorted(aliases, key=len, reverse=True):
        if _is_explicit_npc_speaker_phrase(before.rstrip(), name, at_start=False):
            return aliases[name]
    return None


def _explicit_npc_owner_after_quote(after: str, aliases: dict[str, str]) -> str | None:
    """Find a same-line attribution immediately following a quote."""
    for name in sorted(aliases, key=len, reverse=True):
        if _is_explicit_npc_speaker_phrase(after.lstrip(), name, at_start=True):
            return aliases[name]
    return None


_CROSS_LINE_SPEECH_CUE = (
    r"(?:说|道|答|回答|问|告诉|开口|出声|打破沉默|解释|补充|承认|提醒|回应|吩咐|表示|喃喃|喊|叫)"
)
# 玩家台词守卫：玩家输入的重述、以 NPC 名/称呼开头的呼语，绝不能归给 NPC。
_PLAYER_VOCATIVE = re.compile(
    r"^(?:医生|主任|教授|警官|警长|管家|老师|神父|夫人|先生|女士|小姐)[，,、]?(?:你|您|你们|诸位|我|我们)"
)
# 兜底归属（跨行/唯一对话者）比行内显式归属可信度低，要求更长的引语体，
# 避免把碎片语气词吹成气泡。
_FALLBACK_MIN_BODY_CHARS = 8


def _is_player_speech(body: str, literal_aliases: dict[str, str], player_text: str | None) -> bool:
    """判断引语实为玩家台词：重述玩家输入，或以 NPC 名/称呼开头对 NPC 说话。"""
    compact = re.sub(r"\s+", "", body).strip("。！？!?…—")
    if not compact:
        return False
    if player_text:
        player_compact = re.sub(r"\s+", "", player_text)
        if len(compact) >= 6 and compact in player_compact:
            return True
    for name in literal_aliases:
        stripped = re.sub(r"\s+", "", name)
        # 呼语必须以称呼标点/第二人称收尾才算在对 NPC 说话；
        # “惠特克罗夫特医生……是校医”这种 NPC 转述不算。
        if stripped and compact.startswith(stripped):
            rest = compact[len(stripped) :]
            if not rest or re.match(r"^[，,、：:！？]|^(?:你|您)", rest):
                return True
    return bool(_PLAYER_VOCATIVE.match(compact))


# “黄千陆对法伦说”里的法伦是被搭话对象，不是说话人。
_NPC_ADDRESSED = (
    r"(?:[对向跟朝]\s*__NAME__[^。！？!?\n]{0,8}__CUE__|(?:问|告诉)\s*__NAME__)"
).replace("__CUE__", _CROSS_LINE_SPEECH_CUE)


def _line_addresses_npc(line: str, literal_aliases: dict[str, str]) -> bool:
    """本行是否呈现「某人对 NPC 说话」：此时 NPC 是被称呼对象而非说话人。"""
    masked = _mask_quoted_spans(line)
    for name in sorted(literal_aliases, key=len, reverse=True):
        if re.search(_NPC_ADDRESSED.replace("__NAME__", re.escape(name)), masked):
            return True
    return False


def _mask_quoted_spans(line: str) -> str:
    """把成对引语区间替换为等长空格：说话人扫描只看叙述散文，不看台词内容。"""
    chars = list(line)
    for quote in _paired_quoted_speech(line):
        for index in range(quote.start, quote.end):
            chars[index] = " "
    return "".join(chars)


# 行内玩家言语线索：“你低声重复了一遍”“你接着问”——引语是玩家在说。
_PLAYER_INLINE_CUE = re.compile(
    r"你[^。！？!?\n]{0,12}(?:说|道|问|答|重复|复述|追问|反问|反驳|喊|叫|解释|补充)"
)
# 玩家主导散文：同一行叙述分句以「你/您」开头（“你掏出笔记本，‘……’”）。
# 此时引语是玩家台词的写实，按跨行/唯一对话者兜底会把玩家的话塞进 NPC
# 嘴里——宁可少一个 NPC 气泡，也不能错归。
_PLAYER_LED_PROSE = re.compile(r"(?:^|[。！？!?；;，,」』”\n])\s*[你您]")
# 选项块（“你可以—— / 1. … / [自由行动]”）不参与说话人统计与归属。
_OPTION_LINE = re.compile(r"^\s*(?:\d+\s*[.、)]|\[自由行动\]|\*\*你可以)")


def _line_named_speaker(line: str, literal_aliases: dict[str, str]) -> str | None:
    """一行内「姓名 + 言语线索」指向的唯一 NPC id；零个或多个都返回 None。

    姓名必须出现在分句开头（行首或句读之后），否则像「黄千陆对法伦说」
    里的法伦只是被搭话对象。姓名与线索之间不能再出现其他 NPC 名，
    且线索所在分句不能以「你」开头（那是玩家在说话）。
    """
    masked = _mask_quoted_spans(line)
    owners: set[str] = set()
    for name in sorted(literal_aliases, key=len, reverse=True):
        npc_id = literal_aliases[name]
        for match in re.finditer(r"(?:^|[。！？!?；;，,」』”])" + re.escape(name), masked):
            rest = masked[match.end() :]
            cue = re.search(_CROSS_LINE_SPEECH_CUE, rest)
            if not cue:
                continue
            before_cue = rest[: cue.start()]
            # 姓名与线索之间出现其他 NPC 名，则线索属于别人。
            others = [other for other in literal_aliases if other != name and other in before_cue]
            if others:
                continue
            # 线索所在分句以「你」开头 → 是玩家在说话。
            clause_start = max(before_cue.rfind(mark) for mark in "。！？!?")
            clause = before_cue[clause_start + 1 :].strip()
            if clause.startswith(("你", "您")):
                continue
            owners.add(npc_id)
            break
    return next(iter(owners)) if len(owners) == 1 else None


def _infer_novel_dialogue_line(
    line: str,
    aliases: dict[str, str],
    fallback_owner: Callable[[str], str | None] | None = None,
) -> list[Segment] | None:
    """Recognize common Chinese novel dialogue with an explicit known speaker.

    Supported forms include ``“台词。”法伦答道……`` and
    ``法伦望向窗外，低声说：“台词。”``.  The known NPC must be the
    same-line grammatical speaker, not merely a name mentioned nearby;
    unattributed quotations remain keeper narration unless the caller's
    ``fallback_owner`` (跨行/唯一对话者归属) can name a speaker.
    """
    quotes = [
        quote
        for quote in _paired_quoted_speech(line)
        if len(quote.body) >= 18 or re.search(r"[，。！？!?；…—]", quote.body)
    ]
    if not quotes:
        return None
    result: list[Segment] = []
    cursor = 0
    for index, quote in enumerate(quotes):
        # Only immediately adjacent, same-line prose counts as attribution.
        # Never carry a prior line's active NPC into an untagged quote.
        before = line[cursor : quote.start]
        next_start = quotes[index + 1].start if index + 1 < len(quotes) else len(line)
        after = line[quote.end : next_start]
        owner_id = _explicit_npc_owner_before_quote(before, aliases)
        # ``“前一句。”法伦说道：“后一句。”`` attributes the *next* quote.
        # Do not let its leading speaker phrase relabel the preceding quote.
        after_introduces_next_quote = index + 1 < len(quotes) and after.rstrip().endswith(
            (":", "：")
        )
        if owner_id is None and not after_introduces_next_quote:
            owner_id = _explicit_npc_owner_after_quote(after, aliases)
        if (
            owner_id is None
            and fallback_owner is not None
            and len(quote.body) >= _FALLBACK_MIN_BODY_CHARS
            # “你低声重复了一遍”这类行内玩家言语线索：引语是玩家在说。
            and not _PLAYER_INLINE_CUE.search(before + after)
            # “你掏出笔记本，‘……’”这类玩家主导散文：引语同样是玩家在说，
            # 兜底给在场 NPC 会把玩家台词塞进 NPC 嘴里。
            and not _PLAYER_LED_PROSE.search(_mask_quoted_spans(before + after))
        ):
            owner_id = fallback_owner(quote.body)
        if owner_id is None:
            continue
        before = line[cursor : quote.start].strip()
        if before:
            result.append(Segment(kind="narration", text=before))
        result.append(Segment(kind="speech", text=quote.text, npc_id=owner_id))
        cursor = quote.end
    if not result:
        return None
    tail = line[cursor:].strip()
    if tail:
        result.append(Segment(kind="narration", text=tail))
    return result


def _make_fallback_owner(
    line_index: int,
    *,
    lines: list[str],
    line_speakers: list[str | None],
    interlocutor: str | None,
    literal_aliases: dict[str, str],
    player_text: str | None,
) -> Callable[[str], str | None]:
    """构造单条引语的兜底归属器：跨行姓名线索 → 唯一对话者，玩家台词除外。"""

    def resolve(body: str) -> str | None:
        if _is_player_speech(body, literal_aliases, player_text):
            return None
        # 本行在「对 NPC 说话」：引语属于搭话的人，不是 NPC。
        if _line_addresses_npc(lines[line_index], literal_aliases):
            return None
        for back in (1, 2):
            neighbor = line_index - back
            if neighbor >= 0 and line_speakers[neighbor]:
                return line_speakers[neighbor]
        ahead = line_index + 1
        if ahead < len(lines) and line_speakers[ahead]:
            return line_speakers[ahead]
        return interlocutor

    return resolve


def infer_named_speech(
    segments: list[Segment],
    speaker_aliases: dict[str, str],
    player_text: str | None = None,
    present_npc_ids: Iterable[str] | None = None,
) -> list[Segment]:
    """Recover explicit ``姓名：台词`` lines when the model omitted NPC tags.

    Only server-provided, known public names are accepted. Arbitrary labels are kept as
    narration, so headings such as ``线索：`` cannot invent a speaker identity.

    行内规则之外再做两级段级兜底（模型几乎从不使用发言标签，气泡全靠这里）：
    跨行「姓名 + 言语线索」归属，以及整段唯一具名对话者归属。
    玩家台词（重述输入/呼语开头）有专门守卫，绝不归给 NPC。
    """
    normalized = {
        re.sub(r"\s+", "", name).casefold(): npc_id
        for name, npc_id in speaker_aliases.items()
        if name and npc_id
    }
    literal_aliases = {
        name.strip(): npc_id for name, npc_id in speaker_aliases.items() if name.strip() and npc_id
    }
    recovered: list[Segment] = []
    for segment in segments:
        if segment.kind != "narration":
            recovered.append(segment)
            continue
        narration_lines: list[str] = []
        segment_lines = segment.text.splitlines()
        # 选项块（“你可以—— / 1. … / [自由行动]”）不参与说话人统计与归属。
        content_lines = [line if not _OPTION_LINE.match(line) else "" for line in segment_lines]
        line_speakers = [_line_named_speaker(line, literal_aliases) for line in content_lines]
        cue_owners = {owner for owner in line_speakers if owner}
        interlocutor: str | None = None
        if len(cue_owners) == 1:
            interlocutor = next(iter(cue_owners))
        else:
            named: set[str] = set()
            for line in content_lines:
                masked = _mask_quoted_spans(line)
                for name, npc_id in literal_aliases.items():
                    if name in masked:
                        named.add(npc_id)
            if len(named) == 1:
                interlocutor = next(iter(named))
            elif present_npc_ids is not None:
                # 多个 NPC 被提及但不在场者只是话题（如“法伦的便签”）：
                # 在场的唯一具名 NPC 才是对话者。
                present_named = named & set(present_npc_ids)
                if len(present_named) == 1:
                    interlocutor = next(iter(present_named))

        def flush_narration(lines: list[str] = narration_lines) -> None:
            text = "\n".join(lines).strip("\n")
            lines.clear()
            if text.strip():
                recovered.append(Segment(kind="narration", text=text))

        for line_index, line in enumerate(segment_lines):
            # 选项行里的引语是假设性对话，绝不归属。
            if _OPTION_LINE.match(line):
                narration_lines.append(line)
                continue
            match = _NAMED_LINE.match(line)
            key = re.sub(r"\s+", "", match.group("name")).casefold() if match else ""
            npc_id = normalized.get(key)
            if match and npc_id:
                flush_narration()
                recovered.append(
                    Segment(kind="speech", text=match.group("text").strip(), npc_id=npc_id)
                )
                continue

            fallback_owner = _make_fallback_owner(
                line_index,
                lines=segment_lines,
                line_speakers=line_speakers,
                interlocutor=interlocutor,
                literal_aliases=literal_aliases,
                player_text=player_text,
            )

            novel_result = _infer_novel_dialogue_line(
                line, literal_aliases, fallback_owner=fallback_owner
            )
            if novel_result:
                flush_narration()
                recovered.extend(novel_result)
                continue
            narration_lines.append(line)
        flush_narration()

    merged: list[Segment] = []
    for segment in recovered:
        if merged and merged[-1].kind == segment.kind and merged[-1].npc_id == segment.npc_id:
            merged[-1].text = merged[-1].text.rstrip() + "\n\n" + segment.text.lstrip()
        else:
            merged.append(segment)
    return merged
