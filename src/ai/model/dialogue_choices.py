"""One policy for choices in primary, repaired and rewritten dialogue."""

from __future__ import annotations

from src.ai.model.dialogue_output import DialogueOutput, NarrationBlock, _strip_trailing_menu
from src.gameplay.choice_policy import filter_choice_targets


def prepare_dialogue_choices(output: DialogueOutput, world: dict, *, history_text: str = "",
                             extra_present: set[str] | None = None) -> tuple[DialogueOutput, list[dict]]:
    segments, menu = [], []
    for block in output.segments:
        if isinstance(block, NarrationBlock):
            text, extracted = _strip_trailing_menu(block.text)
            menu = extracted or menu
            if text.strip():
                segments.append(block.model_copy(update={"text": text}))
        else:
            segments.append(block)
    choices = list(output.choices) or menu
    # Menus are separate data; they cannot act as their own public evidence.
    public_text = history_text + "\n" + "\n".join(block.text for block in segments)
    kept, dropped = filter_choice_targets(choices, [item.model_dump() for item in output.choice_targets],
                                          world, public_text, extra_present=extra_present)
    indices = {old: new for new, old in enumerate(kept, 1)}
    return output.model_copy(update={
        "segments": segments,
        "choices": [choices[index - 1] for index in kept],
        "choice_skill_hints": [hint.model_copy(update={"choice_index": indices[hint.choice_index]})
                               for hint in output.choice_skill_hints if hint.choice_index in indices],
        "choice_targets": [target.model_copy(update={"choice_index": indices[target.choice_index]})
                           for target in output.choice_targets if target.choice_index in indices],
    }), dropped
