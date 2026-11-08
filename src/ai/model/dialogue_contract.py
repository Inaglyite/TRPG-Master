"""Strict live-response checks, separate from tolerant historical decoding."""
from __future__ import annotations

import re

from src.ai.model.dialogue_output import DialogueOutput, DialogueValidationError

# Match an action-menu heading even when its list is absent, split into another
# segment, followed by more prose, or uses a Markdown/Unicode spelling variant.
_MENU_HEADING = re.compile(
    r"(?:^|\n)\s*(?:[>#＃]+\s*)?[*_`]*"
    r"(?:你可以(?:选择)?|接下来你可以|可选行动|请选择)"
    r"\s*[*_`]*(?:[—–－─━―\-:：]+|(?=\s*(?:\n|$)))"
)
_NUMBERED_LINE = re.compile(r"^\s*(?:[-*+]\s+)?(?:\d+[.．、)）]|[①②③④⑤⑥⑦⑧])\s*(.+)$")


def _label(text: str) -> str:
    return re.sub(r"[\s*_`。.!！?？]+", "", text)


def validate_live_dialogue(output: DialogueOutput) -> None:
    """Never silently repair a new response that puts choices inside segments."""
    labels = {_label(choice) for choice in output.choices if choice.strip()}
    for index, block in enumerate(output.segments):
        repeated = {
            _label(match.group(1))
            for line in block.text.splitlines()
            if (match := _NUMBERED_LINE.match(line)) and _label(match.group(1)) in labels
        }
        if _MENU_HEADING.search(block.text) or len(repeated) >= 2:
            raise DialogueValidationError(
                f"segments[{index}].text 包含行动菜单。正文只写叙述或台词；"
                "将行动选项仅放在顶层 choices 数组，每项为不带编号的文本。"
                "删除正文中的菜单标题和列表，保留剧情事实；系统会统一生成菜单。",
                category="embedded_choice_menu",
            )
    for index, choice in enumerate(output.choices):
        if not choice.strip() or any(char in choice for char in "\n\r") or _MENU_HEADING.search(choice) or _NUMBERED_LINE.match(choice):
            raise DialogueValidationError(
                f"choices[{index}] 必须是非空、单行、不带编号或菜单标题的行动文本。",
                category="invalid_choice_format",
            )
