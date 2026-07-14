"""Conservative pre-narrative skill-check inference.

Only explicit, mechanically meaningful player actions are matched here. Routine
observation remains narration-only so core clues are not hidden behind an
accidental roll. Explicit travel to a known scene is resolved as deterministic
state, not delegated to another model pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionCheck:
    skill: str
    reason: str


_CHECK_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "listen",
        "玩家明确进行倾听",
        (
            r"(?:侧耳|贴(?:在|到)?.{0,8}(?:门|墙|地板|管道).{0,5}听)",
            r"(?:仔细|专心|屏息)(?:地)?(?:倾听|听)",
            r"倾听.{0,18}(?:动静|声音|谈话)",
        ),
    ),
    (
        "track",
        "玩家明确追踪痕迹",
        (
            r"(?:追踪|循着|沿着).{0,24}(?:足迹|脚印|血迹|车辙|踪迹|痕迹)",
            r"(?:足迹|脚印|血迹|车辙|踪迹).{0,16}(?:追|跟|找)",
        ),
    ),
    (
        "library_use",
        "玩家明确检索档案或资料",
        (
            r"(?:查阅|检索|查找|翻查|翻阅).{0,24}(?:档案|卷宗|资料|报纸|目录|记录|文献)",
            r"(?:图书馆|档案馆).{0,18}(?:调查|研究|查|找)",
        ),
    ),
    (
        "psychology",
        "玩家明确判断他人的心理或谎言",
        (
            r"(?:观察|留意|判断|分析).{0,20}(?:反应|表情|神态|情绪|是否撒谎|有没有说谎)",
            r"(?:看穿|识破).{0,12}(?:谎言|伪装|心思)",
            r"察言观色",
        ),
    ),
    (
        "locksmith",
        "玩家明确尝试开锁",
        (r"(?:撬锁|开锁|解锁|撬开).{0,16}(?:锁|门|柜|箱|抽屉)",),
    ),
    (
        "stealth",
        "玩家明确尝试隐蔽移动",
        (
            r"(?:潜行|蹑手蹑脚|悄无声息|不出声地).{0,24}(?:靠近|进入|离开|跟踪|移动|走)",
            r"(?:偷偷|悄悄)(?:地)?(?:潜入|溜进|绕到|跟上)",
        ),
    ),
    (
        "first_aid",
        "玩家明确实施急救",
        (r"(?:急救|包扎|止血|处理).{0,20}(?:伤口|伤势|创口|出血)",),
    ),
    (
        "spot_hidden",
        "玩家明确搜寻隐藏细节",
        (
            r"(?:搜查|搜索|翻找|搜寻).{0,32}(?:房间|办公室|书桌|抽屉|柜子|现场|尸体|遗体|角落|墙面|地面|物品)?",
            r"(?:寻找|检查|查看).{0,24}(?:暗格|夹层|机关|隐藏|痕迹|指纹|脚印|血迹|异常|可疑之处)",
            r"(?:仔细|彻底)(?:地)?(?:检查|查看|观察|搜查).{0,32}",
            r"(?:完整|全面|仔细|彻底)?(?:地)?(?:检查|检视|查看|观察)"
            r".{0,24}(?:尸体|遗体|眼睛|眼球|躯干|伤口|死者)",
        ),
    ),
)

_NEGATED_ACTION = re.compile(
    r"(?:不|别|不要|没有打算|并不想|拒绝).{0,5}"
    r"(?:侧耳|倾听|追踪|查阅|检索|观察|判断|撬锁|开锁|潜行|急救|搜查|搜索|翻找|检查)"
)
_DISCUSSED_ACTION = re.compile(
    r"(?:问|询问|请问|想知道|追问|请教).{0,40}"
    r"(?:倾听|追踪|查阅|检索|观察|判断|撬锁|开锁|潜行|急救|搜查|搜索|翻找|检查)"
)

_MOVE_ACTION = re.compile(
    r"(?:^|[，。；！？\s])(?:我)?(?:立刻|马上|直接|先|现在)?"
    r"(?:前往|去往|去|来到|进入|走进|赶到|返回|回到)"
)
_NEGATED_MOVE = re.compile(
    r"(?:不|别|不要|拒绝|暂时不).{0,6}(?:前往|去往|去|进入|走进|返回|回到)"
)
_DISCUSSED_MOVE = re.compile(
    r"(?:问|询问|请问|想知道).{0,24}(?:怎么|如何|能否|能不能|可不可以|是否可以)"
    r".{0,16}(?:前往|去往|去|进入|走进|返回|回到)"
)
_LOCATION_NOUNS = (
    "停尸房",
    "医学院",
    "办公室",
    "小屋",
    "酒馆",
    "古董店",
    "宅邸",
    "公寓",
    "疗养院",
    "大学",
)


def infer_action_check(content: str, world: dict) -> ActionCheck | None:
    """Infer one authoritative check from an explicit player action.

    The character must actually have the inferred skill. This avoids silently
    applying the CLI helper's compatibility default to malformed character data.
    """
    text = " ".join(str(content).strip().split())
    if not text or _NEGATED_ACTION.search(text) or _DISCUSSED_ACTION.search(text):
        return None

    skills = world.get("pc", {}).get("skills", {})
    if not isinstance(skills, dict):
        return None

    for skill, reason, patterns in _CHECK_PATTERNS:
        if skill not in skills:
            continue
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return ActionCheck(skill=skill, reason=reason)
    return None


def _scene_aliases(scene: dict) -> set[str]:
    name = str(scene.get("name") or "").strip()
    description = str(scene.get("description") or "")
    aliases = {name, name.replace("的", "")}
    for noun in _LOCATION_NOUNS:
        if noun in name or noun in description:
            aliases.add(noun)
    return {alias for alias in aliases if len(alias) >= 2}


def infer_scene_transition(content: str, world: dict) -> str | None:
    """Return one unambiguous known destination from an explicit move action."""
    text = " ".join(str(content).strip().split())
    if not text or _NEGATED_MOVE.search(text) or _DISCUSSED_MOVE.search(text):
        return None
    move = _MOVE_ACTION.search(text)
    if move is None:
        return None
    destination_text = text[move.end():]
    scenes = world.get("scene_catalog", {})
    if not isinstance(scenes, dict):
        return None

    matches: list[tuple[int, str]] = []
    for scene_id, scene in scenes.items():
        if not isinstance(scene, dict):
            continue
        matched_aliases = [
            alias for alias in _scene_aliases(scene) if alias in destination_text
        ]
        if matched_aliases:
            matches.append((max(map(len, matched_aliases)), str(scene_id)))
    if not matches:
        return None
    best_length = max(length for length, _scene_id in matches)
    best_ids = {
        scene_id for length, scene_id in matches if length == best_length
    }
    if len(best_ids) != 1:
        return None
    scene_id = best_ids.pop()
    if scene_id == str(world.get("current_scene", {}).get("id") or ""):
        return None
    return scene_id
