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


COMBAT_KEY = "combat_state"
_DAMAGE_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.IGNORECASE)
_AMMO_RE = re.compile(r"(?P<open>[（(])(?P<before>\s*)(?P<count>\d+)(?P<after>\s*发\s*)(?P<close>[）)])")
_FIREARM_WORDS = ("枪", "左轮", "手枪", "步枪", "霰弹", "冲锋枪")
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
        "assumed_fields": assumed,
    }


def start_combat(
    world: dict,
    participants: list[dict],
    reason: str = "",
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
    return _public_result(combat, event="combat_started")


def combat_status(world: dict) -> dict:
    combat = world.get(COMBAT_KEY)
    if not isinstance(combat, dict):
        return {"ok": True, "active": False, "event": "no_combat"}
    return _public_result(combat, event="combat_status")


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
) -> dict:
    combat = _require_combat(world)
    if combat.get("pending_decision"):
        raise CombatError("仍有玩家决定尚未处理")
    if actor_id != combat.get("current_actor"):
        raise CombatError(f"当前应由 {combat.get('current_actor')} 行动，而不是 {actor_id}")

    action_type = action_type.lower().strip()
    if action_type not in {"melee", "firearm", "move", "other"}:
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
    }

    if action_type in {"move", "other"}:
        summary = description or ("移动" if action_type == "move" else "执行其他动作")
        _append_log(combat, f"{actor['name']}：{summary}")
        result = {"ok": True, "event": "action_resolved", "outcome": "completed", "description": summary}
        _advance_turn(combat)
        return _with_state(result, combat)

    if not target_id:
        raise CombatError("攻击动作必须指定 target_id")
    target = _find_participant(combat, target_id)
    if target.get("hp", 0) <= 0:
        raise CombatError(f"{target['name']} 已失去战斗能力")

    if target["kind"] == "pc" and actor["kind"] == "npc":
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
    action["defender_choice"] = option_id
    combat["pending_decision"] = None
    combat["phase"] = "resolving"
    result = _resolve_action(world, combat, action, rng or random.Random())
    result["decision"] = {"id": decision_id, "selected": option_id}
    return result


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
    ammo_tracker = _find_ammo_tracker(world, actor, action.get("weapon"))
    if ammo_tracker and ammo_tracker["count"] <= 0:
        raise CombatError(f"{ammo_tracker['item']} 已经没有子弹，必须先装填或更换武器")

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
    ammo = _consume_ammo(world, ammo_tracker) if ammo_tracker else {
        "tracked": False,
        "spent": 1,
        "warning": "未找到带“(N发)”或“（N发）”标记的枪械物品，未自动扣减",
    }

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


def _find_ammo_tracker(world: dict, actor: dict, weapon_hint: str | None) -> dict | None:
    if actor.get("kind") != "pc":
        return None
    inventory = world.get("pc", {}).get("inventory", [])
    if not isinstance(inventory, list):
        return None

    hint = (weapon_hint or "").strip().lower()
    candidates: list[tuple[tuple[int, int, int, int], dict]] = []
    for index, item in enumerate(inventory):
        if not isinstance(item, str):
            continue
        match = _AMMO_RE.search(item)
        if not match:
            continue
        count = int(match.group("count"))
        hint_match = int(bool(hint and hint in item.lower()))
        firearm_match = int(any(word in item for word in _FIREARM_WORDS))
        score = (hint_match, int(count > 0), firearm_match, -index)
        candidates.append((score, {
            "index": index,
            "item": item,
            "count": count,
            "count_start": match.start("count"),
            "count_end": match.end("count"),
        }))

    if not candidates:
        return None
    if hint:
        matching = [candidate for candidate in candidates if candidate[0][0] == 1]
        if matching:
            candidates = matching
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _consume_ammo(world: dict, tracker: dict) -> dict:
    inventory = world["pc"]["inventory"]
    before = tracker["count"]
    after = before - 1
    item = tracker["item"]
    updated = f"{item[:tracker['count_start']]}{after}{item[tracker['count_end'] :]}"
    inventory[tracker["index"]] = updated
    return {
        "tracked": True,
        "weapon": updated,
        "before": before,
        "after": after,
        "spent": 1,
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
