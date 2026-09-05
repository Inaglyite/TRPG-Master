"""Convert model combat proposals into actions backed by authored equipment."""

from __future__ import annotations

import copy

from src.gameplay.combat import CombatError, _entity_for
from src.gameplay.inventory import AMMO_RE, check_investigator_firearm_ammo


def authorize_combat_proposal(world: dict, name: str, args: dict) -> dict:
    result = copy.deepcopy(args)
    if name == "combat_start":
        participants = []
        present = set((world.get("current_scene") or {}).get("npcs_present", []))
        for spec in result.get("participants", []):
            entity, kind, _ = _entity_for(world, str(spec.get("id", "")))
            if kind == "npc" and "current_scene" in world and entity["id"] not in present:
                raise CombatError("不能让不在当前场景的 NPC 加入战斗")
            # Stats, hostility and NPC weapons are authored state, not a setup
            # shortcut for the model. Internal crisis triggers keep their own
            # explicit, trusted participant specifications.
            participant = {"id": spec["id"]}
            if spec.get("ready_firearm") and kind == "pc":
                check_investigator_firearm_ammo(world, spec["id"], None)
                participant["ready_firearm"] = True
            participants.append(participant)
        result["participants"] = participants
        if isinstance(result.get("initial_action"), dict):
            result["initial_action"] = authorize_combat_proposal(world, "combat_action", result["initial_action"])
        return result

    if name != "combat_action" or result.get("action_type") not in {"melee", "firearm"}:
        return result
    entity, kind, _ = _entity_for(world, str(result.get("actor_id", "")))
    firearm = result["action_type"] == "firearm"
    expected_skill = "firearms_handgun" if firearm else "fighting_brawl"
    damage = str(entity.get("damage_spec") or "1d3")
    mode = "normal"
    if kind == "pc":
        if firearm:
            held = check_investigator_firearm_ammo(world, result["actor_id"], result.get("weapon"))
            weapon = AMMO_RE.sub("", held["item"]).strip()
            # Existing string inventories have no weapon profiles. Preserve a
            # fixed legacy handgun profile until the author supplies one.
            damage, mode = "1d8", "impaling"
        elif result.get("weapon"):
            hint = str(result["weapon"])
            matches = [item for item in entity.get("inventory", []) if isinstance(item, str) and hint in item]
            if len(matches) != 1:
                raise CombatError("近战武器必须唯一对应物品栏中的实际物品")
            weapon = matches[0]
        else:
            weapon = "unarmed"
        profiles = world.get("weapon_profiles") or {}
        profile = profiles.get(weapon) if isinstance(profiles, dict) else None
        if isinstance(profile, dict):
            if profile.get("action_type", result["action_type"]) != result["action_type"]:
                raise CombatError("武器不支持此类动作")
            damage = str(profile.get("damage_spec") or damage)
            mode = str(profile.get("damage_mode") or mode)
        elif not firearm:
            damage = "1d3"
    else:
        combat = world.get("combat_state") or {}
        participant = next((p for p in combat.get("participants", []) if p.get("id") == result["actor_id"]), {})
        damage = str(participant.get("damage_spec") or damage)
        if firearm:
            if not entity.get("damage_spec") and not participant.get("ready_firearm"):
                raise CombatError("NPC 没有已声明的枪械攻击")
            mode = "impaling"
    supplied_skill = result.get("skill")
    if supplied_skill and supplied_skill != expected_skill:
        raise CombatError(f"此攻击需要 {expected_skill}，不能改用 {supplied_skill}")
    # Ignore deprecated model-supplied damage, exposing the canonical profile
    # in the eventual result. This avoids wasting a model round on arithmetic.
    result.update(skill=expected_skill, damage_spec=damage, damage_mode=mode)
    return result
