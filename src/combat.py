"""Deterministic combat state and resolution helpers.

The LLM chooses intent and NPC tactics. This module owns turn order, pending
player decisions, dice comparison, damage, and round advancement.
"""

from __future__ import annotations

import copy
import random
import re
import uuid
from typing import Any

from .inventory import InventoryError, check_firearm_ammo, consume_firearm_ammo
from .personality import investigator_roleplay_profile


COMBAT_KEY = "combat_state"
_DAMAGE_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.IGNORECASE)
_SKILL_ALIASES = {
    "fighting_brawl": ("fighting_brawl", "斗殴", "格斗", "近战"),
    "dodge": ("dodge", "闪避"),
    "firearms_handgun": ("firearms_handgun", "手枪", "射击", "火器"),
}
_DEFAULT_SKILLS = {
    "fighting_brawl": 40,
    "dodge": 20,
    "firearms_handgun": 20,
}
_DISPOSITION_LABELS = {
    "cooperative": "合作",
    "nervous": "紧张但未敌对",
    "guarded": "戒备",
    "guilty": "内疚",
    "jealous": "嫉妒",
    "manipulative": "试图操纵",
    "obsessive": "偏执",
    "grieving": "悲痛",
    "unstable": "不稳定",
    "unknown": "尚不明确",
}
_PREFLIGHT_NEGATIONS = (
    "不开枪", "不要开枪", "别开枪", "不射击", "别射击", "放下枪", "收起枪",
    "不攻击", "不要攻击", "停止攻击", "只是问", "假如", "假设", "如果",
    "会怎样", "会怎么样",
)
_PREFLIGHT_NON_ACTION_MARKERS = (
    "能够", "可以", "可能", "是否", "是不是", "会不会", "能不能",
    "为什么", "为何", "怎么会", "据说", "据称", "传闻", "例如",
)
_PREFLIGHT_REPORTING_MARKERS = (
    "告诉", "询问", "问", "听说", "听到", "看到", "看见", "目睹",
    "认为", "觉得", "怀疑", "解释", "提到", "谈到", "讨论", "描述",
    "调查", "得知", "发现", "证明", "推测", "想象", "回忆", "想知道",
    "阻止", "避免", "防止", "命令", "要求", "不让",
)
_PLAYER_ACTION_PREFIX_RE = re.compile(
    r"(?:^|[，,:：])\s*(?:我|我们|调查员)"
    r"(?:现在|立刻|直接|就|要|想要|准备|决定|打算|试图|尝试|先|随后|然后|接着|二话不说)?"
    r"[^，,:：]{0,18}$"
)
_IMPLICIT_ACTION_PREFIX_RE = re.compile(
    r"^(?:(?:立刻|直接|现在|随后|然后|接着|二话不说|转身|上前|冲过去)\s*)?"
    r"(?:(?:掏出?|拔出?|拿出?|举起?|端起?|抬起?).{0,10})?$"
)
_FIREARM_ATTACK_PATTERNS = (
    re.compile(r"(?:朝着?|向|对着?).{0,20}(?:开枪|射击|扣动扳机)"),
    re.compile(r"(?:用|拿|举|持|拔).{0,8}(?:枪|手枪|左轮).{0,16}(?:打|攻击|射|杀)"),
    re.compile(r"(?:开枪|射击|扣动扳机|枪杀)"),
)
_MELEE_ATTACK_PATTERNS = (
    re.compile(r"(?:用|拿|举|持|拔).{0,8}(?:刀|剑|斧|棍|锤).{0,16}(?:砍|刺|捅|打|杀)"),
    re.compile(r"(?:杀死|杀掉|砍死|刺死|捅死|勒死|掐死|殴打|袭击)"),
)
_WEAPON_THREAT_PATTERNS = (
    re.compile(r"(?:用|拿|举|持|拔).{0,8}(?:枪|手枪|左轮|刀|剑).{0,20}(?:指着|指向|对准|瞄准|威胁|架在|抵住)"),
    re.compile(r"(?:枪口|刀尖|刀刃).{0,20}(?:指着|指向|对准|抵住)"),
    re.compile(r"(?:持枪|持刀|拔枪|拔刀).{0,16}(?:威胁|逼问|胁迫)"),
)


class CombatError(ValueError):
    pass


def _number(value: Any, default: int) -> int:
    try:
        return max(0, min(999, int(value)))
    except (TypeError, ValueError):
        return default


def _entity_for(world: dict, entity_id: str) -> tuple[dict, str, str]:
    if entity_id == "pc":
        pc = world.get("pc")
        if isinstance(pc, dict):
            return pc, "pc", "pc"
        raise CombatError("world_state.pc 不存在")

    for index, npc in enumerate(world.get("npcs", [])):
        if isinstance(npc, dict) and npc.get("id") == entity_id:
            return npc, "npc", f"npcs.{index}"
    raise CombatError(f"找不到参战者: {entity_id}")


def _read_skill(entity: dict, skill_id: str) -> int | None:
    skills = entity.get("skills", {})
    if not isinstance(skills, dict):
        return None
    aliases = _SKILL_ALIASES.get(skill_id, (skill_id,))
    for alias in aliases:
        value = skills.get(alias)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, dict):
            for key in ("value", "total", "score"):
                if isinstance(value.get(key), (int, float)):
                    return int(value[key])
    return None


def _read_attribute(entity: dict, attribute_id: str) -> int | None:
    aliases = {
        "DEX": ("DEX", "dex", "敏捷"),
        "CON": ("CON", "con", "体质"),
    }.get(attribute_id, (attribute_id, attribute_id.lower()))
    attributes = entity.get("attributes", {})
    if isinstance(attributes, dict):
        for key in aliases:
            if isinstance(attributes.get(key), (int, float)):
                return int(attributes[key])
    for key in aliases:
        if isinstance(entity.get(key), (int, float)):
            return int(entity[key])
    return None


def _participant(world: dict, spec: dict) -> dict:
    entity_id = str(spec.get("id", "")).strip()
    if not entity_id:
        raise CombatError("参战者缺少 id")
    entity, kind, path = _entity_for(world, entity_id)
    assumed: list[str] = []

    dex = None if kind == "pc" else spec.get("dex")
    if dex is None:
        dex = _read_attribute(entity, "DEX")
    if dex is None:
        dex = 50
        assumed.append("dex=50")
    dex = _number(dex, 50)

    con = None if kind == "pc" else spec.get("con")
    if con is None:
        con = _read_attribute(entity, "CON")
    if con is None:
        con = 50
        assumed.append("con=50")
    con = _number(con, 50)

    normalized_skills: dict[str, int] = {}
    for skill_id, default in _DEFAULT_SKILLS.items():
        value = None if kind == "pc" else spec.get(skill_id)
        if value is None:
            value = _read_skill(entity, skill_id)
        if value is None:
            if skill_id == "dodge" and kind == "pc":
                value = dex // 2
            else:
                value = default
            assumed.append(f"{skill_id}={value}")
        normalized_skills[skill_id] = _number(value, default)

    hp = _number(entity.get("hp"), 10)
    max_hp = _number(entity.get("max_hp"), hp or 10)
    if max_hp <= 0:
        max_hp = max(hp, 1)

    ready_firearm = bool(spec.get("ready_firearm", False))
    return {
        "id": entity_id,
        "name": str(entity.get("name") or entity_id),
        "kind": kind,
        "path": path,
        "dex": dex,
        "con": con,
        "initiative": dex + (50 if ready_firearm else 0),
        "ready_firearm": ready_firearm,
        "skills": normalized_skills,
        "damage_spec": str(spec.get("damage_spec") or "1d3"),
        "hp": hp,
        "max_hp": max_hp,
        "conditions": list(entity.get("conditions", [])) if isinstance(entity.get("conditions", []), list) else [],
        "disposition": str(entity.get("disposition") or "unknown"),
        "hostile_to_pc": bool(entity.get("hostile_to_pc", False) or entity.get("disposition") == "hostile"),
        "assumed_fields": assumed,
    }


def start_combat(
    world: dict,
    participants: list[dict],
    reason: str = "",
    initial_action: dict | None = None,
) -> dict:
    current = world.get(COMBAT_KEY)
    if isinstance(current, dict) and current.get("active"):
        raise CombatError("已有进行中的战斗，请继续当前战斗或先调用 combat_end")

    specs = [dict(item) for item in participants if isinstance(item, dict)]
    if not any(item.get("id") == "pc" for item in specs):
        specs.insert(0, {"id": "pc"})

    resolved: list[dict] = []
    seen: set[str] = set()
    for spec in specs:
        participant = _participant(world, spec)
        if participant["id"] in seen:
            continue
        seen.add(participant["id"])
        resolved.append(participant)

    if len(resolved) < 2:
        raise CombatError("战斗至少需要两名参战者")

    resolved.sort(key=lambda item: (-item["initiative"], item["id"]))
    combat = {
        "active": True,
        "encounter_id": uuid.uuid4().hex[:12],
        "reason": reason,
        "round": 1,
        "phase": "awaiting_action",
        "participants": resolved,
        "turn_order": [item["id"] for item in resolved],
        "turn_index": 0,
        "current_actor": resolved[0]["id"],
        "pending_decision": None,
        "defense_counts": {},
        "outcome": None,
        "log": [],
    }
    _append_log(combat, f"战斗开始：{reason or '敌对行动发生'}")
    world[COMBAT_KEY] = combat
    if isinstance(initial_action, dict):
        params = {
            key: value
            for key, value in initial_action.items()
            if key in {
                "actor_id", "target_id", "action_type", "description", "skill",
                "weapon", "damage_spec", "damage_mode", "defender_choice",
                "bonus_dice", "penalty_dice",
            }
        }
        params.setdefault("actor_id", "pc")
        return combat_action(world, **params, started_combat=True)
    return _public_result(combat, event="combat_started")


def combat_status(world: dict) -> dict:
    combat = world.get(COMBAT_KEY)
    if not isinstance(combat, dict):
        return {"ok": True, "active": False, "event": "no_combat"}
    return _public_result(combat, event="combat_status")


def preview_player_escalation(world: dict, content: str) -> dict | None:
    """Build a side-effect-free confirmation before any GM narration begins."""
    intent = _detect_preflight_intent(content)
    if intent is None:
        return None
    kind, action_type, action_segment = intent
    target = _preflight_target(world, action_segment)
    if target is not None and bool(
        target.get("hostile_to_pc") or target.get("disposition") == "hostile"
    ):
        return None
    if target is None:
        target = {
            "id": None,
            "name": "眼前尚未敌对的人",
            "disposition": "unknown",
        }

    action = {
        "actor_id": "pc",
        "target_id": target.get("id"),
        "action_type": action_type,
        "description": content.strip(),
    }
    if kind == "coercive_threat":
        pending = _build_threat_decision(world, action, target)
        confirm_option = "confirm_threat"
        prompt_suffix = (
            "[系统确认：玩家已确认实施武力威胁，但没有确认实际攻击。"
            "必须按原意使用 threat，绝不能升级为开枪或造成伤害。]"
        )
    else:
        pending = _build_violence_decision(world, action, target)
        confirm_option = "confirm_violence"
        prompt_suffix = "[系统确认：玩家已在叙事开始前确认执行这次攻击，不要再次询问。]"

    return {
        "decision": {
            key: copy.deepcopy(value)
            for key, value in pending.items()
            if key != "action"
        },
        "authorization": {
            "kind": kind,
            "target_id": target.get("id"),
            "confirm_option": confirm_option,
        },
        "prompt_suffix": prompt_suffix,
    }


def end_combat(world: dict, reason: str = "") -> dict:
    combat = _require_combat(world)
    combat["active"] = False
    combat["phase"] = "ended"
    combat["pending_decision"] = None
    combat["outcome"] = reason or "ended_by_keeper"
    _append_log(combat, f"战斗结束：{combat['outcome']}")
    return _public_result(combat, event="combat_ended")


def combat_action(
    world: dict,
    *,
    actor_id: str,
    target_id: str | None = None,
    action_type: str,
    description: str = "",
    skill: str | None = None,
    weapon: str | None = None,
    damage_spec: str | None = None,
    damage_mode: str = "normal",
    defender_choice: str | None = None,
    bonus_dice: int = 0,
    penalty_dice: int = 0,
    rng: random.Random | None = None,
    started_combat: bool = False,
) -> dict:
    combat = _require_combat(world)
    if combat.get("pending_decision"):
        raise CombatError("仍有玩家决定尚未处理")
    if actor_id != combat.get("current_actor"):
        raise CombatError(f"当前应由 {combat.get('current_actor')} 行动，而不是 {actor_id}")

    action_type = action_type.lower().strip()
    if action_type not in {"melee", "firearm", "threat", "move", "other"}:
        raise CombatError(f"不支持的战斗动作: {action_type}")
    actor = _find_participant(combat, actor_id)
    if not _can_act(actor):
        raise CombatError(f"{actor['name']} 已无法行动")

    action = {
        "actor_id": actor_id,
        "target_id": target_id,
        "action_type": action_type,
        "description": description,
        "skill": skill,
        "weapon": weapon,
        "damage_spec": damage_spec,
        "damage_mode": damage_mode,
        "defender_choice": defender_choice,
        "bonus_dice": max(0, min(2, int(bonus_dice or 0))),
        "penalty_dice": max(0, min(2, int(penalty_dice or 0))),
        "started_combat": bool(started_combat),
    }

    if action_type in {"move", "other"}:
        summary = description or ("移动" if action_type == "move" else "执行其他动作")
        _append_log(combat, f"{actor['name']}：{summary}")
        result = {"ok": True, "event": "action_resolved", "outcome": "completed", "description": summary}
        _advance_turn(combat)
        return _with_state(result, combat)

    if not target_id:
        raise CombatError("攻击或威胁动作必须指定 target_id")
    target = _find_participant(combat, target_id)
    if target.get("hp", 0) <= 0:
        raise CombatError(f"{target['name']} 已失去战斗能力")

    if action_type == "threat":
        if actor["kind"] == "pc" and target["kind"] == "npc" and not target.get("hostile_to_pc"):
            return _request_threat_confirmation(world, combat, action, actor, target)
        return _resolve_threat(world, combat, action, actor, target)

    if actor["kind"] == "pc" and target["kind"] == "npc" and not target.get("hostile_to_pc"):
        return _request_violence_confirmation(world, combat, action, actor, target)

    if target["kind"] == "pc" and actor["kind"] == "npc":
        _mark_hostile_to_pc(world, actor, action.get("description", ""))
        return _request_player_defense(combat, action, actor, target)

    if not defender_choice and action_type == "melee":
        defender_choice = "fight_back" if target.get("disposition") == "hostile" else "dodge"
        action["defender_choice"] = defender_choice
    return _resolve_action(world, combat, action, rng or random.Random())


def combat_decide(
    world: dict,
    decision_id: str,
    option_id: str,
    *,
    rng: random.Random | None = None,
) -> dict:
    combat = _require_combat(world)
    pending = combat.get("pending_decision")
    if not isinstance(pending, dict) or pending.get("id") != decision_id:
        raise CombatError("待确认的战斗决定不存在或已经失效")

    valid = {item["id"] for item in pending.get("options", [])}
    if option_id not in valid:
        raise CombatError(f"无效的决定: {option_id}")
    action = dict(pending["action"])
    combat["pending_decision"] = None

    if pending.get("kind") == "irreversible_violence":
        roleplay_context = copy.deepcopy(pending.get("roleplay_context", {}))
        if option_id == "cancel_violence":
            combat["phase"] = "awaiting_action"
            result = _with_state({
                "ok": True,
                "event": "action_cancelled",
                "outcome": "cancelled",
                "action_consumed": False,
                "description": "玩家取消了对非敌对人物的不可逆攻击",
                "violence_confirmation": {
                    "confirmed": False,
                    "target_was_non_hostile": True,
                    "roleplay_context": roleplay_context,
                },
            }, combat)
        else:
            target = _find_participant(combat, action["target_id"])
            _mark_hostile_to_pc(world, target, action.get("description", ""))
            _record_violence_event(world, action, target)
            combat["phase"] = "resolving"
            result = _resolve_action(world, combat, action, rng or random.Random())
            damage = result.get("damage")
            result["violence_confirmation"] = {
                "confirmed": True,
                "target_was_non_hostile": True,
                "consequences_required": True,
                "consider_sanity": isinstance(damage, dict) and damage.get("amount", 0) > 0,
                "roleplay_context": roleplay_context,
            }
    elif pending.get("kind") == "coercive_threat":
        roleplay_context = copy.deepcopy(pending.get("roleplay_context", {}))
        if option_id == "cancel_threat":
            if action.get("started_combat"):
                combat["active"] = False
                combat["phase"] = "ended"
                combat["outcome"] = "player_backed_down_before_escalation"
                _append_log(combat, "玩家在实施武力威胁前收起了武器")
            else:
                combat["phase"] = "awaiting_action"
            result = _with_state({
                "ok": True,
                "event": "action_cancelled",
                "outcome": "cancelled",
                "action_consumed": False,
                "description": "玩家取消了对非敌对人物的武力威胁",
                "threat_confirmation": {
                    "confirmed": False,
                    "target_was_non_hostile": True,
                    "roleplay_context": roleplay_context,
                },
            }, combat)
        else:
            actor = _find_participant(combat, action["actor_id"])
            target = _find_participant(combat, action["target_id"])
            result = _resolve_threat(world, combat, action, actor, target)
            result["threat_confirmation"] = {
                "confirmed": True,
                "target_was_non_hostile": True,
                "consequences_required": True,
                "roleplay_context": roleplay_context,
            }
    else:
        action["defender_choice"] = option_id
        combat["phase"] = "resolving"
        result = _resolve_action(world, combat, action, rng or random.Random())
    result["decision"] = {"id": decision_id, "selected": option_id}
    return result


def _request_violence_confirmation(
    world: dict,
    combat: dict,
    action: dict,
    actor: dict,
    target: dict,
) -> dict:
    pending = _build_violence_decision(world, action, target)
    combat["pending_decision"] = pending
    combat["phase"] = "awaiting_decision"
    return {
        "ok": True,
        "event": "decision_required",
        "requires_decision": True,
        "decision": {key: copy.deepcopy(value) for key, value in pending.items() if key != "action"},
        "combat": _public_state(combat),
    }


def _build_violence_decision(world: dict, action: dict, target: dict) -> dict:
    scene = world.get("current_scene", {})
    scene_name = scene.get("name") if isinstance(scene, dict) else ""
    profile = investigator_roleplay_profile(world.get("pc", {}))
    disposition = target.get("disposition", "unknown")
    disposition_label = _DISPOSITION_LABELS.get(disposition, disposition)
    context = f"{target['name']}目前并未主动敌对，并且与你的关系是“{disposition_label}”"
    if scene_name:
        context += f"；这里是{scene_name}"
    consequences = "攻击将使对方敌对，并可能引来报警、法律追究、声望损失、案件中断或理智后果。"
    roleplay_note, cancel_label = _violence_roleplay_note(profile)

    decision_id = uuid.uuid4().hex[:12]
    pending = {
        "id": decision_id,
        "kind": "irreversible_violence",
        "target_id": target.get("id"),
        "title": f"你真的要攻击{target['name']}吗？",
        "description": f"{context}。{roleplay_note}{consequences}",
        "options": [
            {"id": "cancel_violence", "label": cancel_label, "description": "保留行动与当前资源，重新选择做法。"},
            {"id": "confirm_violence", "label": "仍然攻击", "description": "接受人物、法律与案件后果并进行结算。"},
        ],
        "default_option": "cancel_violence",
        "roleplay_context": profile,
        "action": action,
    }
    return pending


def _request_threat_confirmation(
    world: dict,
    combat: dict,
    action: dict,
    actor: dict,
    target: dict,
) -> dict:
    pending = _build_threat_decision(world, action, target)
    combat["pending_decision"] = pending
    combat["phase"] = "awaiting_decision"
    return {
        "ok": True,
        "event": "decision_required",
        "requires_decision": True,
        "decision": {key: copy.deepcopy(value) for key, value in pending.items() if key != "action"},
        "combat": _public_state(combat),
    }


def _build_threat_decision(world: dict, action: dict, target: dict) -> dict:
    scene = world.get("current_scene", {})
    scene_name = scene.get("name") if isinstance(scene, dict) else ""
    profile = investigator_roleplay_profile(world.get("pc", {}))
    disposition = target.get("disposition", "unknown")
    disposition_label = _DISPOSITION_LABELS.get(disposition, disposition)
    context = f"{target['name']}目前并未主动敌对，并且与你的关系是“{disposition_label}”"
    if scene_name:
        context += f"；这里是{scene_name}"
    roleplay_note, cancel_label = _threat_roleplay_note(profile)
    consequences = "即使不开枪，这也可能破坏关系、引来报警或改变案件走向。"

    decision_id = uuid.uuid4().hex[:12]
    pending = {
        "id": decision_id,
        "kind": "coercive_threat",
        "target_id": target.get("id"),
        "title": f"你真的要用武力威胁{target['name']}吗？",
        "description": f"{context}。{roleplay_note}{consequences}",
        "options": [
            {"id": "cancel_threat", "label": cancel_label, "description": "收起武器，不消耗行动或弹药。"},
            {"id": "confirm_threat", "label": "继续威胁", "description": "接受关系、法律与案件后果。"},
        ],
        "default_option": "cancel_threat",
        "roleplay_context": profile,
        "action": action,
    }
    return pending


def _detect_preflight_intent(content: str) -> tuple[str, str, str] | None:
    text = str(content or "").strip()
    if not text:
        return None

    pattern_groups = (
        ("irreversible_violence", "firearm", _FIREARM_ATTACK_PATTERNS),
        ("irreversible_violence", "melee", _MELEE_ATTACK_PATTERNS),
        ("coercive_threat", "threat", _WEAPON_THREAT_PATTERNS),
    )
    for raw_segment in re.split(r"(?<=[。！？!?；;\n])", text):
        segment = raw_segment.strip()
        if not segment or any(phrase in segment for phrase in _PREFLIGHT_NEGATIONS):
            continue
        for kind, action_type, patterns in pattern_groups:
            for pattern in patterns:
                match = pattern.search(segment)
                if match and _is_explicit_player_action(segment, match):
                    return kind, action_type, segment
    return None


def _is_explicit_player_action(segment: str, match: re.Match[str]) -> bool:
    """Reject reports, questions and hypotheticals while keeping direct commands."""
    if segment.rstrip().endswith(("?", "？")):
        return False
    if any(marker in segment for marker in _PREFLIGHT_NON_ACTION_MARKERS):
        return False

    prefix = segment[:match.start()].strip(" \t\"'“”‘’")
    if any(marker in prefix for marker in _PREFLIGHT_REPORTING_MARKERS):
        return False
    if "被" in prefix or "让" in prefix:
        return False
    return bool(
        _PLAYER_ACTION_PREFIX_RE.search(prefix)
        or _IMPLICIT_ACTION_PREFIX_RE.fullmatch(prefix)
    )


def _preflight_target(world: dict, content: str) -> dict | None:
    scene = world.get("current_scene")
    present_ids = set(scene.get("npcs_present", [])) if isinstance(scene, dict) else set()
    candidates: list[tuple[tuple[int, int], dict]] = []
    for npc in world.get("npcs", []):
        if not isinstance(npc, dict) or not npc.get("id"):
            continue
        aliases = _npc_aliases(npc)
        matches = [alias for alias in aliases if alias in content]
        if not matches:
            continue
        candidates.append(((int(npc["id"] in present_ids), max(map(len, matches))), npc))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]

    combat = world.get(COMBAT_KEY)
    if isinstance(combat, dict) and combat.get("active"):
        combat_npc_ids = [
            participant.get("id")
            for participant in combat.get("participants", [])
            if isinstance(participant, dict) and participant.get("kind") == "npc"
        ]
        if len(combat_npc_ids) == 1:
            for npc in world.get("npcs", []):
                if isinstance(npc, dict) and npc.get("id") == combat_npc_ids[0]:
                    return npc

    pronouns = ("他", "她", "你", "对方", "这个人", "那个人")
    if not any(pronoun in content for pronoun in pronouns):
        return None
    present = [
        npc for npc in world.get("npcs", [])
        if isinstance(npc, dict) and npc.get("id") in present_ids
    ]
    return present[0] if len(present) == 1 else None


def _npc_aliases(npc: dict) -> set[str]:
    name = str(npc.get("name") or "").strip()
    aliases = {name, str(npc.get("id") or "").strip()}
    aliases.update(
        part.strip()
        for part in re.split(r"[·•・\s]+", name)
        if len(part.strip()) >= 2
    )
    return {alias for alias in aliases if len(alias) >= 2}


def _violence_roleplay_note(profile: dict) -> tuple[str, str]:
    stance = profile["violence_stance"]
    beliefs = profile.get("beliefs", "")
    traits = "；".join(profile.get("traits", [])[:3])

    if stance == "avoidant":
        note = "这明显违背了调查员避免主动暴力的行为倾向。"
        cancel_label = "克制冲动"
    elif stance == "unrestrained":
        note = "这种做法与调查员不排斥主动暴力的行为倾向并不冲突。"
        cancel_label = "改换做法"
    else:
        note = "调查员通常只在认为必要时使用暴力，这一次是否必要仍由你决定。"
        cancel_label = "暂不攻击"

    note += _roleplay_anchors(beliefs, traits)
    return note, cancel_label


def _threat_roleplay_note(profile: dict) -> tuple[str, str]:
    stance = profile["violence_stance"]
    beliefs = profile.get("beliefs", "")
    traits = "；".join(profile.get("traits", [])[:3])

    if stance == "avoidant":
        note = "用武器胁迫他人明显违背了调查员避免主动暴力的行为倾向。"
        cancel_label = "收起武器"
    elif stance == "unrestrained":
        note = "这种胁迫与调查员不排斥主动暴力的行为倾向并不冲突。"
        cancel_label = "改用别的手段"
    else:
        note = "调查员通常只在认为必要时诉诸武力，这一次是否必要仍由你决定。"
        cancel_label = "暂不威胁"

    note += _roleplay_anchors(beliefs, traits)
    return note, cancel_label


def _roleplay_anchors(beliefs: str, traits: str) -> str:
    anchors: list[str] = []
    if beliefs:
        anchors.append(f"信念：“{beliefs[:60]}”")
    if traits:
        anchors.append(f"特质：“{traits[:70]}”")
    return f" 人物记录为{'；'.join(anchors)}。" if anchors else ""


def _resolve_threat(world: dict, combat: dict, action: dict, actor: dict, target: dict) -> dict:
    description = action.get("description") or f"{actor['name']}以武力威胁{target['name']}"
    _append_log(combat, f"{actor['name']}威胁{target['name']}：{description}")
    if actor.get("kind") == "pc" and target.get("kind") == "npc":
        _mark_threatened_by_pc(world, target, description)
        _record_threat_event(world, action, target)
    result = {
        "ok": True,
        "event": "action_resolved",
        "outcome": "threat_established",
        "description": description,
        "target": target.get("id"),
        "resource_consumed": False,
    }
    _advance_turn(combat)
    return _with_state(result, combat)


def _request_player_defense(combat: dict, action: dict, actor: dict, target: dict) -> dict:
    if action["action_type"] == "melee":
        options = [
            {"id": "dodge", "label": "闪避", "description": "只求避开这次攻击。"},
            {"id": "fight_back", "label": "反击", "description": "与对方正面对抗，胜出时可造成伤害。"},
            {"id": "no_defense", "label": "不防御", "description": "不进行对抗，让攻击方正常检定。"},
        ]
        default_option = "dodge"
    else:
        options = [
            {"id": "take_cover", "label": "寻找掩体", "description": "进行闪避检定，成功后令射击获得惩罚骰。"},
            {"id": "no_defense", "label": "不找掩体", "description": "让攻击方正常进行射击检定。"},
        ]
        default_option = "take_cover"

    decision_id = uuid.uuid4().hex[:12]
    pending = {
        "id": decision_id,
        "kind": "combat_defense",
        "title": f"{actor['name']} 正在攻击你",
        "description": action.get("description") or f"{actor['name']} 对 {target['name']} 发动攻击。",
        "options": options,
        "default_option": default_option,
        "action": action,
    }
    combat["pending_decision"] = pending
    combat["phase"] = "awaiting_decision"
    return {
        "ok": True,
        "event": "decision_required",
        "requires_decision": True,
        "decision": {key: copy.deepcopy(value) for key, value in pending.items() if key != "action"},
        "combat": _public_state(combat),
    }


def _resolve_action(world: dict, combat: dict, action: dict, rng: random.Random) -> dict:
    actor = _find_participant(combat, action["actor_id"])
    target = _find_participant(combat, action["target_id"])
    if action["action_type"] == "melee":
        result = _resolve_melee(world, combat, action, actor, target, rng)
    else:
        result = _resolve_firearm(world, combat, action, actor, target, rng)

    _check_combat_end(combat)
    if combat.get("active"):
        _advance_turn(combat)
    return _with_state(result, combat)


def _resolve_melee(world: dict, combat: dict, action: dict, actor: dict, target: dict, rng: random.Random) -> dict:
    attack_skill = action.get("skill") or "fighting_brawl"
    defense_choice = action.get("defender_choice") or "dodge"
    extra_bonus = min(2, int(combat.get("defense_counts", {}).get(target["id"], 0)))
    attack_roll = _skill_roll(
        actor,
        attack_skill,
        action.get("bonus_dice", 0) + extra_bonus,
        action.get("penalty_dice", 0),
        rng,
    )
    defense_roll = None
    outcome = "miss"
    damage = None

    if defense_choice == "no_defense":
        if attack_roll["rank"] >= 1:
            outcome = "attacker_hit"
    else:
        defense_skill = "fighting_brawl" if defense_choice == "fight_back" else "dodge"
        defense_roll = _skill_roll(target, defense_skill, 0, 0, rng)
        if attack_roll["rank"] > defense_roll["rank"] and attack_roll["rank"] >= 1:
            outcome = "attacker_hit"
        elif defense_choice == "fight_back" and defense_roll["rank"] > attack_roll["rank"] and defense_roll["rank"] >= 1:
            outcome = "defender_hit"
        else:
            outcome = "defended"

    if outcome == "attacker_hit":
        spec = action.get("damage_spec") or actor.get("damage_spec", "1d3")
        damage = _deal_damage(world, actor, target, spec, action.get("damage_mode", "normal"), attack_roll, rng)
    elif outcome == "defender_hit":
        spec = target.get("damage_spec", "1d3")
        damage = _deal_damage(world, target, actor, spec, "normal", defense_roll, rng)

    combat.setdefault("defense_counts", {})[target["id"]] = extra_bonus + 1
    summary = f"{actor['name']} 攻击 {target['name']}：{_outcome_label(outcome)}"
    _append_log(combat, summary)
    return {
        "ok": True,
        "event": "action_resolved",
        "action_type": "melee",
        "actor": actor["id"],
        "target": target["id"],
        "defender_choice": defense_choice,
        "attack_roll": attack_roll,
        "defense_roll": defense_roll,
        "outcome": outcome,
        "damage": damage,
        "summary": summary,
    }


def _resolve_firearm(world: dict, combat: dict, action: dict, actor: dict, target: dict, rng: random.Random) -> dict:
    if actor.get("kind") == "pc":
        try:
            check_firearm_ammo(world, action.get("weapon"), 1)
        except InventoryError as exc:
            raise CombatError(str(exc)) from exc

    defense_choice = action.get("defender_choice") or "no_defense"
    cover_roll = None
    penalty = action.get("penalty_dice", 0)
    if defense_choice == "take_cover":
        cover_roll = _skill_roll(target, "dodge", 0, 0, rng)
        if cover_roll["rank"] >= 1:
            penalty += 1

    attack_roll = _skill_roll(
        actor,
        action.get("skill") or "firearms_handgun",
        action.get("bonus_dice", 0),
        penalty,
        rng,
    )
    outcome = "attacker_hit" if attack_roll["rank"] >= 1 else "miss"
    damage = None
    if outcome == "attacker_hit":
        spec = action.get("damage_spec") or actor.get("damage_spec", "1d3")
        mode = action.get("damage_mode") or "impaling"
        damage = _deal_damage(world, actor, target, spec, mode, attack_roll, rng)
    ammo = None
    if actor.get("kind") == "pc":
        try:
            ammo = consume_firearm_ammo(
                world,
                action.get("weapon"),
                amount=1,
                reason=action.get("description", ""),
            )
        except InventoryError as exc:
            raise CombatError(str(exc)) from exc

    summary = f"{actor['name']} 射击 {target['name']}：{_outcome_label(outcome)}"
    _append_log(combat, summary)
    return {
        "ok": True,
        "event": "action_resolved",
        "action_type": "firearm",
        "actor": actor["id"],
        "target": target["id"],
        "defender_choice": defense_choice,
        "cover_roll": cover_roll,
        "attack_roll": attack_roll,
        "defense_roll": cover_roll,
        "outcome": outcome,
        "damage": damage,
        "ammo": ammo,
        "summary": summary,
    }


def _skill_roll(participant: dict, skill_id: str, bonus: int, penalty: int, rng: random.Random) -> dict:
    value = _number(participant.get("skills", {}).get(skill_id), _DEFAULT_SKILLS.get(skill_id, 20))
    net = max(-2, min(2, int(bonus or 0) - int(penalty or 0)))
    units = rng.randint(0, 9)
    tens = [rng.randint(0, 9) for _ in range(abs(net) + 1)]
    candidates = [100 if ten == 0 and units == 0 else ten * 10 + units for ten in tens]
    roll = min(candidates) if net >= 0 else max(candidates)
    rank, level = _success_level(roll, value)
    return {
        "actor": participant["id"],
        "skill": skill_id,
        "skill_value": value,
        "roll": roll,
        "rank": rank,
        "level": level,
        "bonus_dice": max(net, 0),
        "penalty_dice": max(-net, 0),
        "candidates": candidates,
    }


def _attribute_roll(participant: dict, attribute_id: str, value: int, rng: random.Random) -> dict:
    units = rng.randint(0, 9)
    tens = rng.randint(0, 9)
    roll = 100 if tens == 0 and units == 0 else tens * 10 + units
    rank, level = _success_level(roll, value)
    return {
        "actor": participant["id"],
        "attribute": attribute_id,
        "attribute_value": value,
        "roll": roll,
        "rank": rank,
        "level": level,
    }


def _success_level(roll: int, value: int) -> tuple[int, str]:
    fumble_threshold = 96 if value < 50 else 100
    if roll == 1:
        return 4, "critical"
    if roll >= fumble_threshold:
        return -1, "fumble"
    if roll <= max(1, value // 5):
        return 3, "extreme"
    if roll <= max(1, value // 2):
        return 2, "hard"
    if roll <= value:
        return 1, "regular"
    return 0, "failure"


def _deal_damage(
    world: dict,
    source: dict,
    target: dict,
    spec: str,
    mode: str,
    hit_roll: dict,
    rng: random.Random,
) -> dict:
    rolls, modifier, maximum = _roll_damage(spec, rng)
    amount = sum(rolls) + modifier
    if hit_roll.get("level") == "extreme":
        if mode == "impaling":
            extra, extra_modifier, _ = _roll_damage(spec, rng)
            amount = maximum + sum(extra) + extra_modifier
            rolls.extend(extra)
        elif mode == "blunt":
            amount = maximum
    amount = max(0, amount)

    entity, _, _ = _entity_for(world, target["id"])
    before = _number(entity.get("hp"), target.get("hp", 0))
    max_hp = _number(entity.get("max_hp"), target.get("max_hp", before or 1))
    instant_death = amount >= max_hp and max_hp > 0
    after = 0 if instant_death else max(0, before - amount)
    entity["hp"] = after
    target["hp"] = after

    conditions = entity.setdefault("conditions", [])
    if not isinstance(conditions, list):
        conditions = []
        entity["conditions"] = conditions
    major_wound = amount * 2 >= max_hp and amount > 0
    if major_wound and "major_wound" not in conditions:
        conditions.append("major_wound")
    major_wound_check = None
    if major_wound and after > 0 and not instant_death:
        if "prone" not in conditions:
            conditions.append("prone")
        major_wound_check = _attribute_roll(target, "CON", _number(target.get("con"), 50), rng)
        if major_wound_check["rank"] < 1 and "unconscious" not in conditions:
            conditions.append("unconscious")
    if instant_death and "dead" not in conditions:
        conditions.append("dead")
    elif after == 0 and "dying" not in conditions:
        conditions.append("dying")
    target["conditions"] = list(conditions)

    return {
        "source": source["id"],
        "target": target["id"],
        "spec": spec,
        "rolls": rolls,
        "amount": amount,
        "hp_before": before,
        "hp_after": after,
        "major_wound": major_wound,
        "major_wound_check": major_wound_check,
        "instant_death": instant_death,
    }


def _roll_damage(spec: str, rng: random.Random) -> tuple[list[int], int, int]:
    match = _DAMAGE_RE.fullmatch(str(spec).replace(" ", ""))
    if not match:
        raise CombatError(f"不支持的伤害骰: {spec}")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)
    if not 1 <= count <= 10 or not 2 <= sides <= 100:
        raise CombatError(f"伤害骰超出范围: {spec}")
    return [rng.randint(1, sides) for _ in range(count)], modifier, count * sides + modifier


def _require_combat(world: dict) -> dict:
    combat = world.get(COMBAT_KEY)
    if not isinstance(combat, dict) or not combat.get("active"):
        raise CombatError("当前没有进行中的战斗")
    return combat


def _find_participant(combat: dict, participant_id: str) -> dict:
    for participant in combat.get("participants", []):
        if participant.get("id") == participant_id:
            return participant
    raise CombatError(f"参战者不在当前战斗中: {participant_id}")


def _advance_turn(combat: dict) -> None:
    order = combat.get("turn_order", [])
    if not order:
        return
    start = int(combat.get("turn_index", 0))
    for offset in range(1, len(order) + 1):
        index = (start + offset) % len(order)
        participant = _find_participant(combat, order[index])
        if _can_act(participant):
            if index <= start:
                combat["round"] = int(combat.get("round", 1)) + 1
                combat["defense_counts"] = {}
            combat["turn_index"] = index
            combat["current_actor"] = participant["id"]
            combat["phase"] = "awaiting_action"
            return


def _check_combat_end(combat: dict) -> None:
    pc_alive = any(p.get("kind") == "pc" and _can_act(p) for p in combat.get("participants", []))
    npc_alive = any(p.get("kind") == "npc" and _can_act(p) for p in combat.get("participants", []))
    if pc_alive and npc_alive:
        return
    combat["active"] = False
    combat["phase"] = "ended"
    combat["pending_decision"] = None
    combat["outcome"] = "victory" if pc_alive else "defeat"
    _append_log(combat, f"战斗结束：{combat['outcome']}")


def _mark_hostile_to_pc(world: dict, participant: dict, reason: str) -> None:
    participant["hostile_to_pc"] = True
    if participant.get("kind") != "npc":
        return
    try:
        entity, _, _ = _entity_for(world, participant["id"])
    except CombatError:
        return
    entity["hostile_to_pc"] = True
    if reason:
        entity["hostility_reason"] = reason


def _mark_threatened_by_pc(world: dict, participant: dict, reason: str) -> None:
    participant["threatened_by_pc"] = True
    if participant.get("disposition") != "hostile":
        participant["disposition"] = "guarded"
    try:
        entity, _, _ = _entity_for(world, participant["id"])
    except CombatError:
        return
    entity["threatened_by_pc"] = True
    if entity.get("disposition") != "hostile":
        entity["disposition"] = "guarded"
    if reason:
        entity["threat_reason"] = reason


def _record_violence_event(world: dict, action: dict, target: dict) -> None:
    scene = world.get("current_scene", {})
    log = world.setdefault("violence_log", [])
    if not isinstance(log, list):
        log = []
        world["violence_log"] = log
    log.append({
        "actor": action.get("actor_id"),
        "target": target.get("id"),
        "target_name": target.get("name"),
        "action_type": action.get("action_type"),
        "description": action.get("description", ""),
        "scene_id": scene.get("id", "") if isinstance(scene, dict) else "",
        "confirmed": True,
    })
    del log[:-30]

    clocks = world.get("case_clocks")
    if isinstance(clocks, dict) and isinstance(clocks.get("human_pressure"), (int, float)):
        clocks["human_pressure"] += 1


def _record_threat_event(world: dict, action: dict, target: dict) -> None:
    scene = world.get("current_scene", {})
    log = world.setdefault("threat_log", [])
    if not isinstance(log, list):
        log = []
        world["threat_log"] = log
    log.append({
        "actor": action.get("actor_id"),
        "target": target.get("id"),
        "target_name": target.get("name"),
        "description": action.get("description", ""),
        "scene_id": scene.get("id", "") if isinstance(scene, dict) else "",
        "confirmed": True,
    })
    del log[:-30]

    clocks = world.get("case_clocks")
    if isinstance(clocks, dict) and isinstance(clocks.get("human_pressure"), (int, float)):
        clocks["human_pressure"] += 1


def _can_act(participant: dict) -> bool:
    conditions = participant.get("conditions", [])
    return participant.get("hp", 0) > 0 and not any(
        condition in conditions for condition in ("dead", "dying", "unconscious")
    )


def _append_log(combat: dict, text: str) -> None:
    log = combat.setdefault("log", [])
    log.append({"round": combat.get("round", 1), "text": text})
    del log[:-30]


def _outcome_label(outcome: str) -> str:
    return {
        "attacker_hit": "攻击命中",
        "defender_hit": "反击命中",
        "defended": "防御成功",
        "miss": "攻击落空",
    }.get(outcome, outcome)


def _public_state(combat: dict) -> dict:
    state = copy.deepcopy(combat)
    pending = state.get("pending_decision")
    if isinstance(pending, dict):
        pending.pop("action", None)
    return state


def _public_result(combat: dict, *, event: str) -> dict:
    return {"ok": True, "event": event, "combat": _public_state(combat)}


def _with_state(result: dict, combat: dict) -> dict:
    result["combat"] = _public_state(combat)
    return result
