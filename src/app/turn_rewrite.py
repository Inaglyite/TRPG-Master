"""rewrite_turn 的载荷构造与归属校验（从 engine.py 抽出，保持引擎文件体量）。"""

from __future__ import annotations

import re
from typing import Any

_PROTOCOL_TAGS = re.compile(r"【/?npc(?::[A-Za-z0-9_-]+)?】|【intro:[A-Za-z0-9_-]+】")


def normalize_narrative_for_match(text: str) -> str:
    """改写定位会话历史：发言标签是结构正本的一部分，比较时统一剥除。"""
    return re.sub(r"\s+", "", _PROTOCOL_TAGS.sub("", text))


def clip(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def build_rewrite_payload(
    record: dict,
    stored_messages: list[dict],
    original_body: str,
) -> dict:
    """改写模型输入：本轮事实 + 原文干净正文 + 原文发言结构（original_segments）。"""
    recent_context = []
    for message in stored_messages[:-1]:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        recent_context.append({"role": role, "content": clip(message.get("content"), 1200)})
    return {
        "player_input": record.get("player_input"),
        "recent_context": recent_context[-6:],
        "fixed_outcomes": [
            {"tool": item.get("name"), "result": clip(item.get("output"), 1400)}
            for item in record.get("executed_tools", [])
            if isinstance(item, dict) and item.get("name")
        ],
        "original_narrative": clip(original_body, 18000),
        "original_segments": [
            {
                "kind": str(segment.get("kind") or ""),
                "npc_id": str(segment.get("npc_id") or "") or None,
                "text": clip(segment.get("text"), 4000),
            }
            for segment in (record.get("narrative_segments") or [])
            if isinstance(segment, dict) and str(segment.get("text") or "").strip()
        ],
        "fixed_choices_not_to_output": [
            item.get("label")
            for item in record.get("choices", [])
            if isinstance(item, dict) and item.get("label")
        ],
    }


def assert_same_speaker_sequence(record: dict, rewrite_segments: list[Any]) -> None:
    """改写不得丢失/串换人物归属：发言者 id 序列必须与已提交回合一致。"""
    original = [
        str(segment.get("npc_id") or "")
        for segment in (record.get("narrative_segments") or [])
        if isinstance(segment, dict) and segment.get("kind") == "speech"
    ]
    rewritten = [
        str(segment.npc_id or "") for segment in rewrite_segments if segment.kind == "speech"
    ]
    if rewritten != original:
        raise RuntimeError("改写结果改变了人物发言归属，未保存任何内容")


_STRUCTURED_CONTRACT_ADDENDUM = (
    "\n\n原文的发言者结构见 original_segments：每个 speech 段的 speaker_id 归属"
    "与先后顺序必须原样保留，只调整措辞；输出遵循本请求末尾的 JSON 输出契约，"
    "上一段“不要输出 JSON”的约束以该契约为准。"
)


def rewrite_contract(base: str, *, structured: bool) -> str:
    """结构化模式以 JSON 输出契约为准，并声明 original_segments 的归属冻结。"""
    return base + _STRUCTURED_CONTRACT_ADDENDUM if structured else base
