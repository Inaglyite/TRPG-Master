"""Validate model-authored dialogue; never infer a speaker from prose."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=20000)


class NarrationBlock(_Block):
    kind: Literal["narration"]


class SpeechBlock(_Block):
    kind: Literal["speech"]
    speaker_id: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_-]+$")


class DialogueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    segments: list[Annotated[NarrationBlock | SpeechBlock, Field(discriminator="kind")]] = Field(min_length=1, max_length=100)
    introduced_npc_ids: list[str] = Field(default_factory=list, max_length=30)
    choices: list[str] = Field(default_factory=list, max_length=8)


DIALOGUE_INSTRUCTION = """本请求正文必须是 JSON 对象，不输出小说体纯文本、Markdown 代码围栏或发言标签。
格式：{"segments":[{"kind":"narration","text":"旁白"},{"kind":"speech","speaker_id":"在场 NPC 的稳定 id","text":"该人物的直接台词"}],"introduced_npc_ids":[],"choices":[]}
旁白块禁止 speaker_id；台词块必须给出 speaker_id，只能来自当前场景在场人物。
每次换说话人必须新建 speech 块；姓名、头像和资源由平台按 id 匹配，不要自行输出资源。
台词正文不需要外围引号；动作神态放 narration 块，不能塞入别人的 speech 块。
introduced_npc_ids 只填本轮实际首次介绍的在场人物；只是谈及的人物不填。
choices 是供玩家选择的后续行动文本数组，或空数组；不得在 narration 中另写选项菜单。
需要游戏工具时先完成工具调用，工具结果返回后再输出最终 JSON；有工具调用的响应不夹带正文。
怀疑场景或在场人物状态不一致时，先 inspect_turn_state；有冲突再 repair_turn_state 并按执行回执写正文，无冲突则修正叙事。
所有正文字符串禁止嵌入 【npc:…】、【/npc】、【intro:…】 等协议标签。
较早历史消息中的叙事已由平台规范为同一 JSON 结构；本轮回复同样必须遵守本契约。
出发前待决回合只出现原场景旁白与人物；抵达回合只出现已实际抵达场景的在场人物台词，
用旁白简短交代出行，不在同一回合追加尚待玩家回答的出发前选择。
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
            segments.append(
                {"kind": "speech", "speaker_id": match.group(1), "text": speech}
            )
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


def decode_dialogue_output(raw: str, *, present_npc_ids: set[str]) -> str:
    """Translate validated blocks to the existing internal segment protocol.

    Validation is atomic: unknown speakers, incomplete JSON and tag injection
    fail before any text is sent to the client.
    """
    output = DialogueOutput.model_validate_json(raw)
    parts: list[str] = []
    for block in output.segments:
        if not block.text.strip() or _RESERVED_TAG.search(block.text):
            raise ValueError("对话正文为空或包含保留的发言协议标签")
        if isinstance(block, SpeechBlock):
            if block.speaker_id not in present_npc_ids:
                raise ValueError(f"发言者不在当前场景：{block.speaker_id}")
            parts.extend(f"【npc:{block.speaker_id}】{paragraph}【/npc】" for paragraph in block.text.split("\n\n") if paragraph.strip())
        else:
            parts.append(block.text)
    for npc_id in dict.fromkeys(output.introduced_npc_ids):
        if npc_id not in present_npc_ids:
            raise ValueError(f"介绍人物不在当前场景：{npc_id}")
        parts.append(f"【intro:{npc_id}】")
    if output.choices:
        if any(not choice.strip() or _RESERVED_TAG.search(choice) for choice in output.choices):
            raise ValueError("行动选项为空或包含保留协议标签")
        choices = [re.sub(r"\s+", " ", choice).strip() for choice in output.choices]
        parts.append("**你可以——**\n" + "\n".join(f"{index}. {choice}" for index, choice in enumerate(choices, 1)))
    return "\n\n".join(parts)
