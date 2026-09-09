"""Ground travel in player-specified places or known people, not prose guesses."""

from __future__ import annotations

import re

from src.gameplay.action_checks import _scene_aliases
from src.gameplay.action_resolution import ActionResolution


class DestinationGroundingError(ValueError):
    """A valid scene id is not sufficient evidence of the player's destination."""


def recent_dialogue(engine) -> list[dict]:
    """Only bounded conversational text; never system prompts, tools or images."""
    messages = getattr(engine, "messages", [])
    if not isinstance(messages, list):
        return []
    result = []
    remaining = 6000
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        if text.startswith("[引擎控制指令｜非玩家发言]"):
            continue
        if message["role"] == "user":
            # User-role history also contains appended keeper instructions.
            # Preserve only the player's part, not the state/lore/tool payload.
            text = re.split(r"\n(?:\n)?\[", text, maxsplit=1)[0]
        text = text[-min(remaining, 4000) :]
        result.append({"role": message["role"], "content": text})
        remaining -= len(text)
        if remaining <= 0 or len(result) >= 4:
            break
    return list(reversed(result))


def known_people(world: dict, dialogue: list[dict]) -> list[dict]:
    present = set((world.get("current_scene") or {}).get("npcs_present", []))
    spoken = "\n".join(message["content"] for message in dialogue)
    result = []
    for npc in world.get("npcs", []):
        revealed = npc.get("revealed") or {}
        name = str(npc.get("name") or "")
        if (
            npc.get("id") in present
            or revealed.get("level", 0)
            or revealed.get("entries")
            or (name and name in spoken)
        ):
            result.append({key: npc.get(key) for key in ("id", "name", "current_location")})
    return result


def _normalize(text: str) -> str:
    return re.sub(r"[\s·・的]", "", text)


def _named_scenes(content: str, world: dict) -> set[str]:
    """Use authored names/aliases, NOT generic nouns inferred from scene names."""
    moves = re.findall(r"(?:前往|去往|返回|回到|走进|进入|去)([^，。；！？,;!?]+)", content)
    if moves:
        content = moves[0]
    text = _normalize(content)
    named = {
        key
        for key, scene in (world.get("scene_catalog") or {}).items()
        if any(
            len(_normalize(name)) >= 2 and _normalize(name) in text
            for name in [str(scene.get("name") or ""), *scene.get("aliases", [])]
        )
    }
    if named:
        return named
    # Preserve unambiguous ordinary navigation ("古董店"), but never infer
    # ownership from a generic office or from a pronoun/person reference.
    if re.search(r"他|她|那里|那儿|办公室", content) or any(
        _mentions_person(content, str(npc.get("name") or "")) for npc in world.get("npcs", [])
    ):
        return set()
    aliases: dict[str, set[str]] = {}
    for key, scene in (world.get("scene_catalog") or {}).items():
        for alias in _scene_aliases(scene):
            aliases.setdefault(_normalize(alias), set()).add(key)
    return {next(iter(keys)) for alias, keys in aliases.items() if len(keys) == 1 and alias in text}


def named_scenes(content: str, world: dict) -> set[str]:
    """玩家输入明确提到的场景 ID 集合（跨场景行动边界校验的公开入口）。

    沿用移动裁决的同一套判定：只认模组声明的名称/别名，代词与通用名词
    （"他/那里/办公室"）不产生目的地。
    """
    return _named_scenes(content, world)


_ROLE_WORDS = frozenset(
    {"医生", "教授", "主任", "先生", "女士", "小姐", "警官", "警探", "护士", "管家", "神父", "店员", "店主"}
)


def _mentions_person(content: str, name: str) -> bool:
    # 姓名分段匹配：两字姓名（法伦、洛奇、亨特、维克）也要能命中，
    # 但纯身份词（医生/教授）不算"提到了某个人"。
    parts = re.split(r"[·・\s]", name)
    for part in parts:
        part = re.sub(r"(?:医生|教授|主任|先生|女士|小姐)$", "", part)
        if len(part) < 2 or part in _ROLE_WORDS:
            continue
        if part in content:
            return True
        if len(part) >= 3 and part[: max(3, len(part) - 1)] in content:
            return True
    return False


def requested_person_location(content: str, world: dict, dialogue: list[dict]) -> str | None:
    """Only explicit travel to one known person; questions/escorts are not travel."""
    if _named_scenes(content, world):
        return None
    if not re.search(r"(?:去|前往|拜访|寻找).{0,35}", content):
        return None
    if re.search(
        r"带.{0,5}(?:去|前往)|跟随|跟着|(?:不|别|不要).{0,4}去|询问|请问|能否|是否", content
    ):
        return None
    locations = {
        npc.get("current_location")
        for npc in known_people(world, dialogue)
        if _mentions_person(content, str(npc.get("name") or ""))
    }
    if len(locations) == 1:
        location = next(iter(locations))
        if location in (world.get("scene_catalog") or {}):
            return location
    return None


def validate_destination(
    destination: str,
    content: str,
    world: dict,
    fallback: ActionResolution,
    dialogue: list[dict] | None = None,
) -> None:
    """Fail closed on ungrounded travel; same-scene movement needs no new address."""
    current = str((world.get("current_scene") or {}).get("id") or "")
    if destination == current:
        return
    named = _named_scenes(content, world)
    if named:
        if destination in named:
            return
        raise DestinationGroundingError("目的地与玩家明确提到的地点不一致；不要擅自改道")
    # Module-authored routes and clue destinations already have structured evidence.
    if fallback.destination_scene_id == destination and fallback.transition_kind in {
        "authored_route",
        "discovery_target",
    }:
        return
    people = [
        npc
        for npc in known_people(world, dialogue or [])
        if _mentions_person(content, str(npc.get("name") or ""))
    ]
    # A person leading us elsewhere is not the same as seeking that person.
    seeking = re.search(r"(?:找|寻找|拜访|去见|去|前往).{0,35}", content)
    if people and seeking and not re.search(r"带.{0,5}(?:去|前往)|跟随|跟着", content):
        locations = {npc.get("current_location") for npc in people}
        if locations == {destination}:
            return
        raise DestinationGroundingError("要找的人不在该目的地；请依据已知位置或询问，不得编造迷路")
    raise DestinationGroundingError(
        "目的地缺少明确归属：‘他/那里/办公室’不能映射到任意场景；"
        "结合最近对话确认，同场景走位用 interact，无法确定则 clarify，不得擅自改道"
    )
