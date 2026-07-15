"""Extract the keeper's explicit action menu at the protocol boundary."""

from __future__ import annotations

import re


_CHOICE_MARKER = re.compile(
    r"(?:\*{0,2})你可以(?:\s*)[—–-]{1,2}(?:\*{0,2})\s*[:：]?",
    re.IGNORECASE,
)
_NUMBERED_CHOICE = re.compile(r"^\s*(\d{1,2})[.)、．]\s*(.+?)\s*$")
_MARKDOWN_WRAPPER = re.compile(r"^(?:\*{1,2}|_{1,2})(.*?)(?:\*{1,2}|_{1,2})$")


def extract_action_choices(narrative: str, *, limit: int = 8) -> list[dict]:
    """Return choices only from a list following an explicit ``你可以`` marker.

    The model still writes a human-readable menu, but parsing now happens once
    on the server and travels as structured protocol data.  A numbered list
    elsewhere in the prose is deliberately ignored.
    """
    matches = list(_CHOICE_MARKER.finditer(narrative or ""))
    if not matches:
        return []

    menu = narrative[matches[-1].end():]
    choices: list[dict] = []
    expected_number: int | None = None
    for raw_line in menu.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _NUMBERED_CHOICE.match(line)
        if not match:
            if choices:
                break
            continue
        number = int(match.group(1))
        if expected_number is not None and number != expected_number:
            break
        label = _clean_label(match.group(2))
        if not label:
            continue
        choices.append({
            "label": label,
            "isFree": "自由行动" in label or "你决定做什么" in label,
        })
        expected_number = number + 1
        if len(choices) >= limit:
            break
    return choices


def _clean_label(label: str) -> str:
    label = label.strip()
    wrapper = _MARKDOWN_WRAPPER.match(label)
    if wrapper:
        label = wrapper.group(1).strip()
    label = re.sub(r"[*_`]+", "", label)
    return re.sub(r"\s+", " ", label)
