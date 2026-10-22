"""对话归属复核：零/部分 speech 的可疑叙事由模型复核，平台校验后落账。

与格式修复的区别：格式修复处理 JSON 语法/非法 id；归属复核处理“直接台词被
写进 narration”的语义错误。两者预算互相独立——修复名额是否用尽不影响归属
校验；复核确认纯旁白才放行，复核无法给出合法修正则 fail-closed（不播出、
不提交），由调用方进入可恢复的失败状态。

复核结果的硬性校验（平台侧，不信模型自述）：
- verdict=false → 语义确认纯旁白，原样放行；
- verdict=true → 必须给出至少一个 speech 块；原有 speech（id+文本）按序原样
  保留；全部块文本归一化拼接与原文逐字一致（兼容引号标点差异）；
  choices 原样照抄（choice_skill_hints 由平台随之原样保留，索引不串选项）；
  speaker_id 只能来自在场人物或已声明/登记的临时身份。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.ai.model.dialogue_output import (
    DialogueOutput,
    GuestSpeakerBlock,
    SpeechBlock,
    decode_dialogue_output,
)
from src.app.logger import game_event as log_game
from src.app.logger import model_call as log_model_call


class AttributionReviewError(RuntimeError):
    """归属复核未能给出合法修正：调用方必须 fail-closed。"""


class AttributionVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contains_direct_speech: bool
    segments: list[dict] = Field(default_factory=list)
    guest_speakers: list[GuestSpeakerBlock] = Field(default_factory=list)
    choices: list[str] | None = None


_REVIEW_INSTRUCTION = """你是对话归属复核员。给出的叙事 JSON 里，narration 块可能混入了人物正在直接发言的语句。
判断标准：直接台词是人物当场说出的话（可以没有引号）；动作神态、纸条/文件上的文字、
回忆与转述的引文、玩家自己说的话都不是 NPC 台词。
逐段检查所有人物的直接发言，不要找到一段 speech 就停止；原文没有引号、没有“说道”或人物未登记，都不是把当场台词保留为 narration 的理由。
例如“门房摇头。\n\n已经关门了，请明天再来。”中，前句是动作旁白，后句是门房当场说的话，应拆成带引号的 speech；不能因门房不在 allowed_speakers 中就漏掉这句台词。
- 若不存在直接台词：返回 {"contains_direct_speech": false}。
- 若存在：返回 {"contains_direct_speech": true, "segments": [...], "guest_speakers": [...]}，
  把原文按顺序重新拆成 narration/speech 块：
  · 不得增删改写台词与旁白的文字；所有块的 text 依序拼接后必须与原文逐字一致；保留已有引号，仅可为新识别的直接台词补齐成对中文双引号“……”，不得删去引号；
  · 已有 speech 块原样保留（speaker_id 与文本均不得改变）；
  · 新拆出的 speech 块的 text 必须带成对中文双引号；已有 speech 块原样保留，不能因为旧块缺少引号而删去其他块的引号；
  · 已登记人物的 speaker_id 使用 allowed_speakers 中的 id；未登记人物先在 guest_speakers 中声明
    {"speaker_id": "guest_…", "display_name": "玩家可见称呼"}，同一结果的 speech 即可使用该 id；不得用 guest 冒充正式 NPC，也不得因没有图片或正式角色表条目而漏标台词；
  · choices 原样照抄，不得改动。
只输出 JSON，不要解释。"""

_QUOTE_CHARS = '“”"「」『』‘’'


def _flatten_text(output: DialogueOutput) -> str:
    """仅在比较副本中剥空白与引号，兼容补齐引号；不修改实际台词。"""
    joined = "\n\n".join(block.text for block in output.segments)
    stripped = "".join(ch for ch in joined if ch not in _QUOTE_CHARS)
    return re.sub(r"\s+", "", stripped)


def review_messages(
    host: Any, output: DialogueOutput, *, allowed_speakers: list[dict]
) -> list[dict]:
    payload = {
        "segments": [
            {
                "kind": block.kind,
                **({"speaker_id": block.speaker_id} if isinstance(block, SpeechBlock) else {}),
                "text": block.text,
            }
            for block in output.segments
        ],
        "choices": list(output.choices),
        "allowed_speakers": allowed_speakers,
    }
    return [
        {"role": "system", "content": _REVIEW_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def validate_review_fix(
    original: DialogueOutput,
    verdict: AttributionVerdict,
    *,
    present_npc_ids: set[str],
    guest_speaker_ids: set[str],
) -> DialogueOutput:
    """校验复核修正稿；任何一项不符即 AttributionReviewError。"""
    if not verdict.contains_direct_speech:
        raise AttributionReviewError("内部错误：无台词结论不应进入修正校验")
    if verdict.choices is not None and verdict.choices != original.choices:
        raise AttributionReviewError("复核修正改动了 choices")
    fixed = DialogueOutput.model_validate(
        {
            "segments": verdict.segments,
            "introduced_npc_ids": list(original.introduced_npc_ids),
            "choices": list(original.choices),
            "choice_targets": [target.model_dump(mode="json") for target in original.choice_targets],
            # 选项技能提示按索引跟随 choices：choices 原样照抄，提示也原样保留。
            "choice_skill_hints": [
                hint.model_dump(mode="json") for hint in original.choice_skill_hints
            ],
            "guest_speakers": [
                *original.guest_speakers,
                *verdict.guest_speakers,
            ],
        }
    )
    if not any(isinstance(block, SpeechBlock) for block in fixed.segments):
        raise AttributionReviewError("复核声称存在直接台词但未给出任何 speech 块")
    # 文本保全：归一化后逐字一致（只允许多少引号差异）
    if _flatten_text(fixed) != _flatten_text(original):
        raise AttributionReviewError("复核修正改变了叙事文本")
    # 原有 speech 按序原样保留
    original_speech = [
        (block.speaker_id, block.text)
        for block in original.segments
        if isinstance(block, SpeechBlock)
    ]
    fixed_speech = [
        (block.speaker_id, block.text) for block in fixed.segments if isinstance(block, SpeechBlock)
    ]
    cursor = 0
    for pair in original_speech:
        try:
            cursor = fixed_speech.index(pair, cursor) + 1
        except ValueError:
            raise AttributionReviewError("复核修正改动或丢失了原有 speech 块") from None
    # 发言身份复用既有原子校验（在场 ∪ 临时身份），同时产出内部标签形式以
    # 确认可以落账；返回值只取结构，标签文本由 emit 阶段重新解码。
    allowed_guests = set(guest_speaker_ids) | {block.speaker_id for block in fixed.guest_speakers}
    decode_dialogue_output(
        json.dumps(fixed.model_dump(mode="json"), ensure_ascii=False),
        present_npc_ids=present_npc_ids,
        guest_speaker_ids=allowed_guests,
    )
    return fixed


def run_attribution_review(
    host: Any,
    model: str,
    output: DialogueOutput,
    *,
    present_npc_ids: set[str],
    guest_speaker_ids: set[str],
    allowed_speakers: list[dict],
    log_error,
) -> DialogueOutput | None:
    """一次有界复核。返回 None 表示语义确认纯旁白；DialogueOutput 为合法修正稿。

    复核调用失败、返回非法结论或修正稿不过校验：抛 AttributionReviewError，
    由调用方 fail-closed（不播出、不提交可疑草稿）。
    """
    client = getattr(host, "client", None)
    if client is None:
        raise AttributionReviewError("复核模型客户端不可用")
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=review_messages(host, output, allowed_speakers=allowed_speakers),
            temperature=0,
            max_tokens=6000,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError(f"归属复核未完整结束：{choice.finish_reason}")
        verdict = AttributionVerdict.model_validate_json(str(choice.message.content or ""))
    except AttributionReviewError:
        raise
    except Exception as exc:
        log_error(f"对话归属复核失败：{type(exc).__name__}")
        raise AttributionReviewError(f"归属复核调用失败：{type(exc).__name__}") from exc
    finally:
        log_model_call(model, "attribution_review", time.monotonic() - started, None, "chars", 0)
    if not verdict.contains_direct_speech:
        log_game("对话归属复核：确认无直接台词，按原样落账")
        return None
    fixed = validate_review_fix(
        output,
        verdict,
        present_npc_ids=present_npc_ids,
        guest_speaker_ids=guest_speaker_ids,
    )
    log_game("对话归属复核：已按发言者重新分段")
    return fixed
