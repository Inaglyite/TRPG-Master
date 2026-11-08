"""Protocol repair may remove invalid speech, but cannot give it to someone else."""
from __future__ import annotations

import re

from src.ai.model.dialogue_output import (
    DialogueOutput,
    DialogueValidationError,
    SpeechBlock,
    parse_dialogue_output,
)


def _words(text: str) -> str:
    # Ignore quotation, punctuation and whitespace in comparison copies only.
    return re.sub(r"[\W_]+", "", text)


def validate_repair_speakers(original_raw: str, repaired: DialogueOutput) -> None:
    """Reject reused substantive lines under a different ID before publication.

    This is a conservative text-preservation check, never an attribution guess
    from narration. Short greetings are deliberately excluded. Malformed drafts
    have no trustworthy segment structure and stay with the JSON repair path.
    """
    try:
        original = parse_dialogue_output(original_raw)
    except ValueError:
        return
    original_lines = [(_words(block.text), block.speaker_id)
                      for block in original.segments if isinstance(block, SpeechBlock)]
    for block in repaired.segments:
        if not isinstance(block, SpeechBlock):
            continue
        line = _words(block.text)
        owners = {speaker_id for old, speaker_id in original_lines
                  if min(len(line), len(old)) >= 12 and (line in old or old in line)}
        if owners and block.speaker_id not in owners:
            raise DialogueValidationError(
                "格式修复把原有台词转交给了另一人物；不得通过替换 speaker_id 绕过校验。"
                "保留实际说话人的独立身份；若其不在场，应移除无效发言。",
                category="speaker_reassigned", speaker_id=block.speaker_id,
            )
