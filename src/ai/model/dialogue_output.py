"""Validate model-authored dialogue; never infer a speaker from prose."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class DialogueValidationError(ValueError):
    """结构化对话校验失败；category 供修复路径与玩家提示区分失败类型。"""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        speaker_id: str = "",
        departure: Mapping[str, object] | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.speaker_id = speaker_id
        self.departure = dict(departure) if departure else None


class DialogueFormatError(DialogueValidationError):
    """JSON 损坏或结构缺失（schema 校验未过）：修复时只应修格式。"""

    def __init__(self, message: str):
        super().__init__(message, category="invalid_json")


class SpeakerAbsentError(DialogueValidationError):
    """发言者/介绍人物不在当前场景，且不在已离场记录中。"""

    def __init__(self, message: str, *, speaker_id: str = ""):
        super().__init__(message, category="absent_speaker", speaker_id=speaker_id)


class SpeakerDepartedError(DialogueValidationError):
    """发言者已在当前场景离场（事实冲突），携带离场原因与离场回合。"""

    def __init__(
        self,
        speaker_id: str,
        departure: Mapping[str, object] | None = None,
        *,
        message: str | None = None,
    ):
        departure = dict(departure) if departure else {}
        if message is None:
            detail = []
            if departure.get("reason"):
                detail.append(f"离场原因：{departure['reason']}")
            if departure.get("turn_id"):
                detail.append(f"离场回合：{departure['turn_id']}")
            suffix = f"（{'；'.join(detail)}）" if detail else ""
            message = f"发言者已离场：{speaker_id}{suffix}"
        super().__init__(
            message, category="departed_speaker", speaker_id=speaker_id, departure=departure
        )


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=20000)


class NarrationBlock(_Block):
    kind: Literal["narration"]


class SpeechBlock(_Block):
    kind: Literal["speech"]
    speaker_id: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_-]+$")


class GuestSpeakerBlock(BaseModel):
    """本轮新声明的临时展示身份（伙计/路人等未登记 NPC）。

    只承载玩家可见称呼，不携带秘密、技能、位置等权威信息；落账与生命周期
    见 src/gameplay/guest_speakers.py。
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    speaker_id: str = Field(pattern=r"^guest_[A-Za-z0-9_-]{1,40}$")
    display_name: str = Field(min_length=1, max_length=24)


class ChoiceSkillHint(BaseModel):
    """模型对某个行动选项的“可能检定”提议：只有技能键，可用性与数值由平台解析。

    注解仅作展示，不承诺该行动一定触发检定；索引越界、未知技能等由平台
    按条丢弃（choice_skill_hint_map / resolve_skill_hints），不影响整轮。
    """

    model_config = ConfigDict(extra="forbid")
    choice_index: int
    skill_id: str = Field(min_length=1, max_length=48)


class ChoiceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    choice_index: int = Field(ge=1, le=8)
    kind: Literal["npc", "scene", "goal", "clue", "review"]
    target_id: str = Field(min_length=1, max_length=128)
    evidence_quote: str = Field(default="", max_length=300)


class DialogueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    segments: list[Annotated[NarrationBlock | SpeechBlock, Field(discriminator="kind")]] = Field(
        min_length=1, max_length=100
    )
    introduced_npc_ids: list[str] = Field(default_factory=list, max_length=30)
    choices: list[str] = Field(default_factory=list, max_length=8)
    guest_speakers: list[GuestSpeakerBlock] = Field(default_factory=list, max_length=4)
    choice_skill_hints: list[ChoiceSkillHint] = Field(default_factory=list, max_length=8)
    choice_targets: list[ChoiceTarget] = Field(default_factory=list, max_length=8)

    @field_validator("choice_targets", mode="before")
    @classmethod
    def _drop_malformed_targets(cls, value):
        if not isinstance(value, list):
            return []
        targets = []
        for item in value[:64]:
            try:
                targets.append(ChoiceTarget.model_validate(item))
            except ValidationError:
                continue
            if len(targets) == 8:
                break
        return targets

    @field_validator("choice_skill_hints", mode="before")
    @classmethod
    def _drop_malformed_hints(cls, value):
        """结构损坏的提示按条丢弃；绝不让一条坏提示拖垮整轮校验。"""
        if not isinstance(value, list):
            return []
        hints = []
        for item in value[:64]:
            if not isinstance(item, dict) or type(item.get("choice_index")) is not int:
                continue
            try:
                hint = ChoiceSkillHint.model_validate(item)
            except ValidationError:
                continue
            if hint.skill_id.strip():
                hints.append(hint)
            if len(hints) == 8:
                break
        return hints


def choice_skill_hint_map(output: DialogueOutput) -> tuple[dict[int, str], list[dict]]:
    """按选项序号（1 起，对应 choices 顺序）归并提示；返回（有效映射, 丢弃诊断）。

    choices 为空（如菜单走 narration 兜底提取）时序号无从对应，全部丢弃；
    越界与重复标注只丢该条。诊断不含模型原文，可安全进入回合记录。
    """
    hints: dict[int, str] = {}
    dropped: list[dict] = []
    count = len(output.choices)
    for hint in output.choice_skill_hints:
        index = hint.choice_index
        if count == 0 or not 1 <= index <= count:
            dropped.append({"choice_index": index, "reason": "index_out_of_range"})
            continue
        if index in hints:
            dropped.append({"choice_index": index, "reason": "duplicate_hint"})
            continue
        hints[index] = hint.skill_id.strip()
    return hints, dropped


DIALOGUE_INSTRUCTION = """本请求正文必须是 JSON 对象，不输出小说体纯文本、Markdown 代码围栏或发言标签。
格式：{"segments":[{"kind":"narration","text":"他放下茶杯。"},{"kind":"speech","speaker_id":"在场 NPC 的稳定 id","text":"“这件事，请您另问别人。”"}],"introduced_npc_ids":[],"choices":[],"guest_speakers":[],"choice_skill_hints":[]}
旁白块禁止 speaker_id；台词块必须给出 speaker_id，只能来自当前场景在场人物或已声明的临时人物。
人物说出的直接台词一律放进 speech 块；只有转述、纸条/文件文字、回忆引文、玩家自己的话才留在 narration。
每次换说话人必须新建 speech 块；姓名、头像和资源由平台按 id 匹配，不要自行输出资源。
所有当场说出的直接台词都必须保留成对中文双引号“……”（标点属于 text 字符串内容），即使已有 speech 身份和人物气泡也不能省略。
每个 speech 块只放该人物带引号的台词，动作神态另放 narration 块；换人或穿插动作后重新分块，台词引号在各块内闭合。
引号用于保证玩家阅读原文时仍能区分台词与旁白；speaker_id 用于人物气泡，两者必须同时提供，不能互相替代。
修复、重新分段和重新叙述时保留已有台词引号；旧历史缺少引号不代表本轮可以省略，不能把直接台词改成无引号旁白或转述来规避人物归属。
introduced_npc_ids 只填本轮实际首次介绍的在场人物；只是谈及的人物不填。
choices 是供玩家选择的后续行动文本数组，或空数组；不得在 narration 中另写选项菜单。
choice_skill_hints 可选，形如 [{"choice_index":1,"skill_id":"history"}]：choice_index 从 1 对应 choices 顺序，
只在该行动很可能需要技能检定时标注最相关的一个规则技能键（如 spot_hidden、history、listen）；
它只表示“可能检定”，不承诺一定检定，也不预示成败、难度或隐藏信息；日常行动与自由行动不标注。
choice_targets 可选：建议与人物、地点、目标或线索有关时提供
{"choice_index":1,"kind":"npc|scene|goal|clue|review","target_id":"稳定 id","evidence_quote":"最终正文或玩家已知材料中的原句"}。
npc 表示现场交谈，scene 表示可达出口，goal 表示继续未完成目标，clue 表示寻找尚未取得但已有公开依据的线索，review 表示查看已获线索。
离场者不列为现场交谈对象，已经完成的目标或取得的物品不再列为待寻找；不得把未公开身份、线索真相或结局条件写进选项。
正文被修复或重写后同步核对选项及其依据，不足三项也可以；自由行动保留，不需要 target。
guest_speakers 只用于本轮首次开口的未登记人物（伙计、路人等未报姓名者）：
声明 {"speaker_id":"guest_…","display_name":"玩家可见称呼"} 后本回合起可用该 id 发言；
display_name 只能用玩家可见称呼，不得写真实姓名或秘密身份；已声明过的 id 直接复用，不要重复声明；
场景中的正式 NPC 一律使用既有 id，不得声明为 guest。
需要游戏工具时先完成工具调用，工具结果返回后再输出最终 JSON；有工具调用的响应不夹带正文。
怀疑场景或在场人物状态不一致时，先 inspect_turn_state；有冲突再 repair_turn_state 并按执行回执写正文，无冲突则修正叙事。
所有正文字符串禁止嵌入 【npc:…】、【/npc】、【intro:…】 等协议标签。
可恢复的历史叙事已由平台规范为同一 JSON 结构；标明“发言归属未知”的旧历史和会话摘要只供回忆，不是格式示例。
本轮回复必须遵守本契约，不能模仿旧历史缺失的引号、身份或分段。
出发前待决回合只出现原场景旁白与人物；抵达回合用旁白简短交代出行，由目的地在场人物发言，
出发地人物可以给一句简短送别台词（不提尚待玩家回答的新问题），此外只有目的地在场人物能说话，
不在同一回合追加尚待玩家回答的出发前选择。
"""

_SPEECH_TAG = re.compile(r"【npc:([A-Za-z0-9_-]{1,48})】(.*?)【/npc】", re.S)
_INTRO_TAG = re.compile(r"【intro:([A-Za-z0-9_-]{1,48})】")
_CHOICES_MENU = re.compile(r"\n?\*\*你可以——\*\*\s*\n((?:\d+\.[^\n]*(?:\n|$))+)\s*$")


def narrative_history_to_dialogue_json(text: str) -> str:
    """把历史上以内部小说体存放的 assistant 叙事规范为 dialogue_v1 JSON 线格式。

    只重组结构、不改事实：带【npc:id】标签的台词保留 speaker_id（标签在落账前
    已校验，这里不用正则猜测 NPC）；无标签的旧存档正文整体作为 narration；末尾
    “你可以——”编号菜单移入 choices；【intro:id】点标记移入 introduced_npc_ids。
    已是 JSON 或空内容原样返回。存档不改写，规范化只发生在请求副本上。
    """
    stripped = text.strip()
    if not stripped or stripped.startswith("{"):
        return text
    choices: list[str] = []
    body = stripped
    menu = _CHOICES_MENU.search(stripped)
    if menu:
        choices = [
            re.sub(r"^\d+\.\s*", "", line).strip()
            for line in menu.group(1).splitlines()
            if line.strip()
        ]
        body = stripped[: menu.start()].rstrip()
    introduced: list[str] = []
    body = _INTRO_TAG.sub(lambda match: introduced.append(match.group(1)) or "", body)
    segments: list[dict] = []
    cursor = 0
    for match in _SPEECH_TAG.finditer(body):
        narration = body[cursor : match.start()].strip()
        if narration:
            segments.append({"kind": "narration", "text": narration})
        speech = match.group(2).strip()
        if speech:
            segments.append({"kind": "speech", "speaker_id": match.group(1), "text": speech})
        cursor = match.end()
    tail = body[cursor:].strip()
    if tail:
        segments.append({"kind": "narration", "text": tail})
    if not segments:
        return text
    return json.dumps(
        {
            "segments": segments,
            "introduced_npc_ids": list(dict.fromkeys(introduced)),
            "choices": choices,
        },
        ensure_ascii=False,
    )


_RESERVED_TAG = re.compile(r"(?:【|\[)(?:/?npc|intro)(?::|[】\]])")


def _strip_trailing_menu(text: str) -> tuple[str, list[str]]:
    """剥掉叙述块末尾的“你可以——”编号菜单；返回（干净文本, 提取到的选项）。

    契约要求选项只走 choices 数组，但模型常把菜单又写进 narration。平台菜单由
    choices 统一生成，叙述里的重复菜单必须移除，否则正文会出现两遍。
    """
    extracted: list[str] = []
    cleaned = text
    while True:
        menu = _CHOICES_MENU.search(cleaned)
        if not menu:
            break
        extracted = [
            re.sub(r"^\d+\.\s*", "", line).strip()
            for line in menu.group(1).splitlines()
            if line.strip()
        ]
        cleaned = cleaned[: menu.start()].rstrip()
    return cleaned, extracted


def parse_dialogue_output(raw: str) -> DialogueOutput:
    """Parse and schema-validate one model reply without any speaker checks."""
    return DialogueOutput.model_validate_json(raw)


def finalized_history_content(tagged_text: str, clean_text: str, *, structured: bool) -> str:
    """定稿后写入引擎消息历史的正本。

    结构化回合保存带【npc:id】标签的内部形式（定稿前已校验 id），下一轮
    请求由 narrative_history_to_dialogue_json 无损还原为 speech 结构；
    干净正文只用于玩家可见记录。旧模式正文不含可验证标签，维持干净文本，
    读取时不臆造归属。
    """
    return tagged_text if structured else clean_text


def decode_dialogue_output(
    raw: str,
    *,
    present_npc_ids: set[str],
    guest_speaker_ids: set[str] | None = None,
    departed: Mapping[str, dict] | None = None,
) -> str:
    """Translate validated blocks to the existing internal segment protocol.

    Validation is atomic: unknown speakers, incomplete JSON and tag injection
    fail before any text is sent to the client.  发言者必须在场，或属于本轮
    声明/已注册的临时展示身份（guest_speaker_ids）。传入 departed（scene_speaker_presence
    的已离场记录）时，命中离场者的错误类型化为 SpeakerDepartedError（事实冲突），
    与 SpeakerAbsentError（未知/不在场）、DialogueFormatError（JSON 损坏）区分；
    未传 departed 时退化为“不在场”错误。
    """
    try:
        output = parse_dialogue_output(raw)
    except ValueError as exc:
        raise DialogueFormatError(f"对话 JSON 损坏或结构缺失：{exc}") from exc
    departed = departed or {}
    allowed_speakers = set(present_npc_ids) | set(guest_speaker_ids or set())
    parts: list[str] = []
    menu_choices: list[str] = []
    for block in output.segments:
        if not block.text.strip() or _RESERVED_TAG.search(block.text):
            raise DialogueValidationError("对话正文为空或包含保留的发言协议标签", category="invalid_content")
        if isinstance(block, SpeechBlock):
            if block.speaker_id not in allowed_speakers:
                if block.speaker_id in departed:
                    raise SpeakerDepartedError(block.speaker_id, departed[block.speaker_id])
                raise SpeakerAbsentError(
                    f"发言者不在当前场景：{block.speaker_id}", speaker_id=block.speaker_id
                )
            parts.extend(
                f"【npc:{block.speaker_id}】{paragraph}【/npc】"
                for paragraph in block.text.split("\n\n")
                if paragraph.strip()
            )
        else:
            # 模型常在 narration 里再写一遍选项菜单：剥掉正文菜单，
            # choices 数组缺失时以提取到的菜单补上，保证正文至多一份菜单。
            narration, extracted = _strip_trailing_menu(block.text)
            if extracted:
                menu_choices = extracted
            if narration.strip():
                parts.append(narration)
    for npc_id in dict.fromkeys(output.introduced_npc_ids):
        if npc_id not in present_npc_ids:
            if npc_id in departed:
                raise SpeakerDepartedError(npc_id, departed[npc_id])
            raise SpeakerAbsentError(f"介绍人物不在当前场景：{npc_id}", speaker_id=npc_id)
        parts.append(f"【intro:{npc_id}】")
    raw_choices = list(output.choices) or menu_choices
    if raw_choices:
        if any(not choice.strip() or _RESERVED_TAG.search(choice) for choice in raw_choices):
            raise DialogueValidationError("行动选项为空或包含保留协议标签", category="invalid_content")
        choices = [re.sub(r"\s+", " ", choice).strip() for choice in raw_choices]
        parts.append(
            "**你可以——**\n"
            + "\n".join(f"{index}. {choice}" for index, choice in enumerate(choices, 1))
        )
    return "\n\n".join(parts)


def segments_to_tagged_text(segments: list[dict]) -> str:
    """把已验证的段结构还原为内部标签正本（载入路径专用，不做任何猜测）。"""
    parts: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "")
        if not text.strip():
            continue
        npc_id = str(segment.get("npc_id") or "")
        if segment.get("kind") == "speech" and npc_id:
            parts.append(f"【npc:{npc_id}】{text}【/npc】")
        else:
            parts.append(text)
    return "\n\n".join(parts)


# 归属疑点只触发一次有界的语义复核（dialogue_attribution），规则自身永不指派
# 说话人：成对引语与无引号的第二人称直呼段落都只是“疑点”，归属由复核模型给出、
# 平台按文本保全与身份表校验。
_SUSPICION_MIN_QUOTE_CHARS = 12
_SECOND_PERSON = re.compile(r"[您你](?!们?的)")
_DIRECT_ENDING = re.compile(r"[？！]\s*$|—+\s*$")
_VOCATIVE_START = re.compile(
    r"^(?:这位|那位)?(?:先生|女士|小姐|夫人|伙计|朋友|老板|各位|诸位|两位)"
)


def _paragraph_sounds_like_direct_speech(paragraph: str) -> bool:
    """无引号直接台词疑点：独立段落里的第二人称直呼/呼语提问。

    只用于触发复核，不构成归属；纯叙述（“你推开门”“他看向你”）不命中。
    """
    text = paragraph.strip()
    if len(text) < 6:
        return False
    if _SECOND_PERSON.search(text) and _DIRECT_ENDING.search(text):
        return True
    return bool(_VOCATIVE_START.match(text) and re.search(r"[？！]", text))


class AttributionSuspicion(ValueError):
    """narration 疑似混入直接台词；携带解析结果供归属复核使用。"""

    def __init__(self, output: DialogueOutput):
        super().__init__("疑似把人物直接台词写进了 narration，需要归属复核")
        self.output = output


def narration_attribution_suspicion(output: DialogueOutput) -> bool:
    """任何 narration 块含成段引语或无引号直呼段落（即使已有别的 speech）。"""
    from src.gameplay.speaker_parser import _paired_quoted_speech

    for block in output.segments:
        if isinstance(block, SpeechBlock):
            continue
        for quote in _paired_quoted_speech(block.text):
            if len(quote.body.strip()) >= _SUSPICION_MIN_QUOTE_CHARS:
                return True
        for paragraph in re.split(r"\n\s*\n", block.text):
            if _paragraph_sounds_like_direct_speech(paragraph):
                return True
    return False
