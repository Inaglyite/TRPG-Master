"""Normalize investigator roleplay anchors used by deterministic systems."""

from __future__ import annotations

from typing import Any


VIOLENCE_STANCES = ("avoidant", "conditional", "unrestrained")
_STANCE_ALIASES = {
    "avoidant": "avoidant",
    "克制": "avoidant",
    "回避": "avoidant",
    "非暴力": "avoidant",
    "conditional": "conditional",
    "谨慎": "conditional",
    "有条件": "conditional",
    "视情况": "conditional",
    "unrestrained": "unrestrained",
    "无拘束": "unrestrained",
    "不克制": "unrestrained",
    "主动暴力": "unrestrained",
}
_STANCE_LABELS = {
    "avoidant": "避免主动暴力",
    "conditional": "仅在必要时使用暴力",
    "unrestrained": "不排斥主动暴力",
}


def normalize_violence_stance(value: Any) -> str:
    """Return a supported stance, defaulting old characters to conditional."""
    key = str(value or "").strip().lower()
    return _STANCE_ALIASES.get(key, "conditional")


def investigator_roleplay_profile(pc: dict) -> dict:
    """Collect stable background and acquired psychological traits for narration."""
    backstory = pc.get("backstory")
    if not isinstance(backstory, dict):
        backstory = {}
    psychological = pc.get("psychological_profile")
    if not isinstance(psychological, dict):
        psychological = {}

    stance = normalize_violence_stance(backstory.get("violence_stance"))
    traits = _unique_texts([
        *_text_values(backstory.get("traits")),
        *_text_values(psychological.get("traits")),
    ])
    beliefs = "；".join(_unique_texts(_text_values(backstory.get("beliefs"))))
    return {
        "violence_stance": stance,
        "violence_stance_label": _STANCE_LABELS[stance],
        "beliefs": beliefs,
        "traits": traits,
    }


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
