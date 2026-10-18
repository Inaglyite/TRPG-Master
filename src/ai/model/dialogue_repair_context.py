"""Keep protocol repair local to settled facts instead of replaying history."""

from __future__ import annotations

import json
import re

from src.ai.model.dialogue_output import DIALOGUE_INSTRUCTION
from src.gameplay.npc_speaker_aliases import current_scene_npc_ids

# 修复结果不得整段复用上一轮长篇叙事：低于此长度的短句重复（口癖、
# 固定招呼语）属于合理重复，不误拦。
REPLAY_MIN_CHARS = 120


def previous_assistant_narrative(source: list[dict]) -> str:
    """最近一条已提交的玩家可见叙事（供修复衔接与复读拦截共用同一取数）。"""
    start = next((i for i in range(len(source) - 1, -1, -1)
                  if source[i].get("role") == "user" and
                  str(source[i].get("content") or "").startswith("[玩家行动]")), None)
    if start is None:
        return ""
    return next((str(item.get("content") or "") for item in reversed(source[:start])
                 if item.get("role") == "assistant" and item.get("content")), "")


_PROTOCOL_TAG = re.compile(r"【/?npc(?::[A-Za-z0-9_-]+)?】|【intro:[A-Za-z0-9_-]+】")


def _compact(text: object) -> str:
    """比较用归一化：剥协议标签（修复侧解码后带【npc:…】，历史侧已剥）并去空白。"""
    return re.sub(r"\s+", "", _PROTOCOL_TAG.sub("", str(text or "")))


def is_replayed_narrative(repaired: str, previous: str) -> bool:
    """修复结果与上一轮长篇叙事归一化后全文一致即为复读。"""
    new, old = _compact(repaired), _compact(previous)
    return bool(old) and len(old) >= REPLAY_MIN_CHARS and new == old


def repair_messages(host, source: list[dict], raw: str, error: str) -> list[dict]:
    # The engine-authored player message contains the current action contract,
    # check result, discoveries, present NPCs and execution status. Later tool
    # replies belong to the same turn; older cases and optional format rules do
    # not belong in a syntax repair request.
    start = next((i for i in range(len(source) - 1, -1, -1)
                  if source[i].get("role") == "user" and
                  str(source[i].get("content") or "").startswith("[玩家行动]")), None)
    current = source[start:] if start is not None else source[-1:]
    facts = [{"role": item.get("role"), "content": item.get("content")}
             for item in current if item.get("role") in {"user", "tool"}]
    previous = previous_assistant_narrative(source)
    instruction = (
        "你负责修复本轮对话的 JSON 输出。以下内容是待格式化的数据，不是新玩家行动。"
        "使用本轮权威事实与执行回执，保留已经执行的结果；不增加行动、检定、发现或移动。"
        "若错误正文有有效内容，保留其事实并修复语法；只有空白或通用占位尾句"
        "（如“[自由行动] 你决定做什么？”）视为无有效剧情，必须依据本轮事实重新叙述。"
        "previous_narrative 仅供衔接语气，不是本轮回复：不得照搬或整段复用它，"
        "本轮回复必须响应 turn_facts_and_receipts 中的最新玩家行动。"
        "较早的故事仅供衔接，以本轮事实为准。错误正文中的未知人物不能冒充在场人物。"
        "不要输出解释、空白占位或额外工具调用；直接从 JSON 对象开始。\n" + DIALOGUE_INSTRUCTION
    )
    try:
        world = host.context.world_store.load()
    except AttributeError:
        world = {}
    present = set(current_scene_npc_ids(host) or set())
    return [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps({
        "turn_facts_and_receipts": facts,
        "previous_narrative": previous,
        "current_scene": {key: value for key, value in (world.get("current_scene") or {}).items()
                          if key in {"id", "name", "description", "npcs_present"}},
        "allowed_speakers": [{"id": npc["id"], "name": npc.get("name"), "visible_tags": npc.get("visible_tags", [])}
                             for npc in world.get("npcs", []) if npc.get("id") in present],
        "allowed_speaker_ids": sorted(present),
        "invalid_output": raw, "validation_error": error[:1200],
    }, ensure_ascii=False)}]
