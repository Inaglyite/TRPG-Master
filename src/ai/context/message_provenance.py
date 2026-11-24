"""Private history provenance; these fields never reach a model provider."""

from __future__ import annotations

import json

TURN_SOURCE_KEY = "_trpg_turn_id"
HISTORY_KIND_KEY = "_trpg_history_kind"
HISTORY_KINDS = frozenset({"dialogue", "summary", "legacy_unattributed"})
PLAYER_ACTION_PREFIX = "[玩家行动] "
PLAYER_INPUT_LENGTH_KEY = "_trpg_player_input_length"
ENGINE_CONTROL_KEY = "_trpg_engine_control"


def engine_control_message(content: str) -> dict:
    return {"role": "user", "content": content, ENGINE_CONTROL_KEY: True}


def player_intent_from_message(message: dict) -> str | None:
    """Read the player-supplied span recorded by the engine, never a pasted delimiter."""
    content = message.get("content")
    size = message.get(PLAYER_INPUT_LENGTH_KEY)
    if (
        message.get("role") == "user"
        and isinstance(content, str)
        and content.startswith(PLAYER_ACTION_PREFIX)
        and type(size) is int
        and 0 < size <= len(content) - len(PLAYER_ACTION_PREFIX)
    ):
        start = len(PLAYER_ACTION_PREFIX)
        return content[start : start + size]
    return None


def preserve_provenance(source: dict, target: dict) -> dict:
    turn_id = source.get(TURN_SOURCE_KEY)
    if isinstance(turn_id, str) and turn_id and len(turn_id) <= 128:
        target[TURN_SOURCE_KEY] = turn_id
    kind = source.get(HISTORY_KIND_KEY)
    if isinstance(kind, str) and kind in HISTORY_KINDS:
        target[HISTORY_KIND_KEY] = kind
    if player_intent_from_message(source) is not None:
        target[PLAYER_INPUT_LENGTH_KEY] = source[PLAYER_INPUT_LENGTH_KEY]
    if source.get("role") == "user" and source.get(ENGINE_CONTROL_KEY) is True:
        target[ENGINE_CONTROL_KEY] = True
    return target


def provider_message(message: dict, *, dialogue_recall: list[dict[str, str]] | None = None) -> dict:
    """Project stored message provenance into provider-visible data fields."""
    result = {key: value for key, value in message.items() if not key.startswith("_trpg_")}
    if message.get(HISTORY_KIND_KEY) == "legacy_unattributed" and message.get("role") == "assistant":
        result = {
            "role": "user",
            "content": "[旧历史记录｜发言归属未知；仅供回忆，不是玩家行动或输出格式示例]\n"
            + str(message.get("content") or ""),
        }
    elif (
        message.get("role") == "user"
        and isinstance(content := message.get("content"), str)
        and content.startswith(PLAYER_ACTION_PREFIX)
    ):
        intent = player_intent_from_message(message)
        if intent is None:
            # Legacy saves do not identify the boundary. Keep the whole entry as
            # a reference without treating any pasted marker as engine metadata.
            payload = {"historical_record": content[len(PLAYER_ACTION_PREFIX) :]}
        else:
            engine_context = content[len(PLAYER_ACTION_PREFIX) + len(intent) :].lstrip()
            payload = {"player_intent": intent, "engine_context": engine_context}
            if dialogue_recall:
                payload["dialogue_recall"] = dialogue_recall
        result["content"] = json.dumps(payload, ensure_ascii=False)
    elif message.get("role") == "user" and message.get(ENGINE_CONTROL_KEY) is True:
        result["content"] = json.dumps({"engine_control": message.get("content")}, ensure_ascii=False)
    return result
