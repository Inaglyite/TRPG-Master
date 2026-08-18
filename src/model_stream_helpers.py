"""Pure helpers shared by model streaming and final narrative reconciliation."""

from __future__ import annotations

import re

from .tool_protocol import strip_tool_protocol

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
