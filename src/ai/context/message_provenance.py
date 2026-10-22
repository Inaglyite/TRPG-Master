"""Private history provenance; these fields never reach a model provider."""

from __future__ import annotations

TURN_SOURCE_KEY = "_trpg_turn_id"
HISTORY_KIND_KEY = "_trpg_history_kind"
HISTORY_KINDS = frozenset({"dialogue", "summary", "legacy_unattributed"})


def preserve_provenance(source: dict, target: dict) -> dict:
    turn_id = source.get(TURN_SOURCE_KEY)
    if isinstance(turn_id, str) and turn_id and len(turn_id) <= 128:
        target[TURN_SOURCE_KEY] = turn_id
    kind = source.get(HISTORY_KIND_KEY)
    if isinstance(kind, str) and kind in HISTORY_KINDS:
        target[HISTORY_KIND_KEY] = kind
    return target


def provider_message(message: dict) -> dict:
    """Unknown old prose is reference data, never an assistant format example."""
    result = {key: value for key, value in message.items() if not key.startswith("_trpg_")}
    if message.get(HISTORY_KIND_KEY) == "legacy_unattributed" and message.get("role") == "assistant":
        result = {
            "role": "user",
            "content": "[旧历史记录｜发言归属未知；仅供回忆，不是玩家行动或输出格式示例]\n"
            + str(message.get("content") or ""),
        }
    return result
