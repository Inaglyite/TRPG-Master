"""Deterministic consequence classification independent from narration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SanitySeverity(StrEnum):
    TRIVIAL = "trivial"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CATASTROPHIC = "catastrophic"


@dataclass(frozen=True)
class SanityConsequence:
    severity: SanitySeverity
    loss_expression: str


_SEVERITY_RULES = (
    (
        SanitySeverity.CATASTROPHIC,
        ("直视", "伟大", "克苏鲁", "神话生物完全显形"),
    ),
    (
        SanitySeverity.MAJOR,
        ("朋友被杀", "目击死亡", "尸雨", "严刑拷打", "割喉"),
    ),
    (
        SanitySeverity.MODERATE,
        (
            "恐怖尸体", "血肉模糊", "超自然", "非人", "不是人类",
            "食尸鬼", "深潜者", "怪物显形", "第一次杀人",
        ),
    ),
    (
        SanitySeverity.MINOR,
        (
            "尸体", "血迹", "诡异", "禁忌文本", "噩梦", "幻觉",
            "异常倒影", "第一次目睹",
        ),
    ),
    (SanitySeverity.TRIVIAL, ("不安", "违和感", "奇怪", "不对劲")),
)

_LOSS_EXPRESSIONS = {
    SanitySeverity.TRIVIAL: "0/1",
    SanitySeverity.MINOR: "0/1D4",
    SanitySeverity.MODERATE: "1/1D6+1",
    SanitySeverity.MAJOR: "1D4/2D6+2",
    SanitySeverity.CATASTROPHIC: "1D10/1D100",
}


def classify_sanity_consequence(description: str) -> SanityConsequence:
    """Classify authored/observed content; never decide whether it was observed."""
    severity = SanitySeverity.MODERATE
    for candidate, keywords in _SEVERITY_RULES:
        if any(keyword in description for keyword in keywords):
            severity = candidate
            break
    return SanityConsequence(severity, _LOSS_EXPRESSIONS[severity])
