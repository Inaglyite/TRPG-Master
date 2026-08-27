import copy
import random
import unittest

from src.gameplay.combat import (
    CombatError,
    assign_combat_actor,
    combat_action,
    combat_decide,
    combat_status,
    preview_player_escalation,
    start_combat,
)
from src.gameplay.investigators import project_active_investigator


class FixedRandom:
    def __init__(self, values):
        self.values = iter(values)

    def randint(self, _low, _high):
        return next(self.values)


def make_world() -> dict:
    return {
        "pc": {
            "name": "调查员",
            "hp": 12,
            "max_hp": 12,
            "attributes": {"DEX": 60},
            "skills": {"fighting_brawl": 55, "dodge": 40, "firearms_handgun": 45},
            "inventory": [".38口径左轮手枪（6发）", "手电筒"],
            "conditions": [],
        },
        "npcs": [
            {
                "id": "cultist",
                "name": "教徒",
                "hp": 9,
                "max_hp": 9,
                "attributes": {"DEX": 70},
                "skills": {"fighting_brawl": 65, "dodge": 35},
                "disposition": "hostile",
                "conditions": [],
            }
        ],
    }


def make_multiplayer_world(*, active_id: str = "inv-alice") -> dict:
    world = make_world()
    alice = copy.deepcopy(world["pc"])
    alice.update(
        {
            "name": "Alice",
            "investigator_id": "inv-alice",
            "controller_user_id": "user-alice",
            "attributes": {"DEX": 80},
            "inventory": [".38口径左轮手枪（6发）"],
        }
    )
    bob = copy.deepcopy(world["pc"])
    bob.update(
        {
            "name": "Bob",
            "investigator_id": "inv-bob",
            "controller_user_id": "user-bob",
            "attributes": {"DEX": 70},
            "inventory": [".38口径左轮手枪（6发）"],
        }
    )
    world["investigators"] = {
        "inv-alice": alice,
        "inv-bob": bob,
    }
    world["investigator_controllers"] = {
        "user-alice": "inv-alice",
        "user-bob": "inv-bob",
    }
    world["active_investigator_id"] = active_id
    world["pc"] = copy.deepcopy(world["investigators"][active_id])
    return world


class CombatStateMachineTests(unittest.TestCase):
    def test_preflight_detects_explicit_attack_before_combat_exists(self):
        world = make_world()
        world["pc"]["backstory"] = {"violence_stance": "avoidant"}
        world["npcs"][0]["id"] = "bryce_fallon"
        world["npcs"][0]["name"] = "布莱斯·法伦"
        world["npcs"][0]["disposition"] = "cooperative"

        preview = preview_player_escalation(world, "朝着法伦开枪")

        self.assertIsNotNone(preview)
        self.assertEqual(preview["decision"]["kind"], "irreversible_violence")
        self.assertEqual(preview["decision"]["target_id"], "bryce_fallon")
        self.assertEqual(preview["decision"]["default_option"], "cancel_violence")
        self.assertEqual(preview["decision"]["presentation"], "chat")
        self.assertEqual(preview["authorization"]["confirm_option"], "confirm_violence")
        self.assertNotIn("combat_state", world)

    def test_preflight_distinguishes_weapon_threat_and_ignores_hypotheticals(self):
        world = make_world()
        world["npcs"][0]["id"] = "bryce_fallon"
        world["npcs"][0]["name"] = "布莱斯·法伦"
        world["npcs"][0]["disposition"] = "cooperative"

        threat = preview_player_escalation(world, "用枪指着法伦逼问真相")

        self.assertEqual(threat["decision"]["kind"], "coercive_threat")
        self.assertEqual(threat["authorization"]["confirm_option"], "confirm_threat")
        self.assertIsNone(preview_player_escalation(world, "如果朝法伦开枪会怎么样？"))
        self.assertIsNone(preview_player_escalation(world, "我收起枪，不朝法伦开枪"))

    def test_preflight_ignores_reported_deaths_and_conversational_questions(self):
        world = make_world()
        world["npcs"][0]["id"] = "bryce_fallon"
        world["npcs"][0]["name"] = "布莱斯·法伦"
        world["npcs"][0]["disposition"] = "cooperative"

        dialogue = (
            "你是说，莱特教授的死很有可能和巫术有关？法伦先生，我来自遥远的东方，"
            "也从来没有听说过这样神奇的巫术。能够通过一个文档将人杀死。"
        )

        self.assertIsNone(preview_player_escalation(world, dialogue))
        self.assertIsNone(preview_player_escalation(world, "我问法伦：莱特教授是被人杀死的吗？"))
        self.assertIsNotNone(preview_player_escalation(world, "我决定杀死法伦"))

    def test_preflight_does_not_interrupt_attack_on_hostile_target(self):
        world = make_world()

        self.assertIsNone(preview_player_escalation(world, "朝教徒开枪"))
        start_combat(world, [{"id": "cultist"}], "遭到袭击")
        self.assertIsNone(preview_player_escalation(world, "扣动扳机"))

    def test_start_uses_dex_and_persists_authoritative_state(self):
        world = make_world()

        result = start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")

        self.assertTrue(result["ok"])
        self.assertEqual(world["combat_state"]["turn_order"], ["cultist", "pc"])
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")
        self.assertEqual(world["combat_state"]["round"], 1)

    def test_multiplayer_combat_uses_stable_ids_and_damage_never_follows_projection(self):
        world = make_multiplayer_world(active_id="inv-alice")
        world["npcs"][0]["attributes"]["DEX"] = 90
        start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")
        self.assertEqual(
            world["combat_state"]["turn_order"],
            ["cultist", "inv-alice", "inv-bob"],
        )

        # Simulate the legacy projection being switched while Alice remains the
        # authoritative target stored in combat_state.
        world["active_investigator_id"] = "inv-bob"
        world["pc"] = copy.deepcopy(world["investigators"]["inv-bob"])
        pending = combat_action(
            world,
            actor_id="cultist",
            target_id="inv-alice",
            action_type="melee",
            description="教徒挥拳攻击 Alice",
        )
        self.assertEqual(
            pending["decision"]["responding_investigator_id"],
            "inv-alice",
        )
        self.assertEqual(
            pending["decision"]["target_investigator_id"],
            "inv-alice",
        )

        resolved = combat_decide(
            world,
            pending["decision"]["id"],
            "no_defense",
            rng=FixedRandom([1, 1, 2]),
        )
        self.assertEqual(resolved["damage"]["target"], "inv-alice")
        self.assertEqual(world["investigators"]["inv-alice"]["hp"], 10)
        self.assertEqual(world["pc"]["hp"], 12)
        self.assertEqual(world["investigators"]["inv-bob"]["hp"], 12)

    def test_multiplayer_firearm_ammo_is_charged_to_the_stable_actor(self):
        world = make_multiplayer_world(active_id="inv-bob")
        world["investigators"]["inv-bob"]["attributes"]["DEX"] = 95
        world["pc"]["attributes"]["DEX"] = 95
        world["npcs"][0]["attributes"]["DEX"] = 60
        start_combat(world, [{"id": "cultist"}], "枪战")

        result = combat_action(
            world,
            actor_id="inv-bob",
            target_id="cultist",
            action_type="firearm",
            description="Bob 向教徒开枪",
            weapon="左轮手枪",
            damage_spec="1d8",
            rng=FixedRandom([9, 9]),
        )
        self.assertEqual(result["ammo"]["investigator_id"], "inv-bob")
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（5发）")
        self.assertEqual(
            world["investigators"]["inv-alice"]["inventory"][0],
            ".38口径左轮手枪（6发）",
        )
        project_active_investigator(world)
        self.assertEqual(
            world["investigators"]["inv-bob"]["inventory"][0],
            ".38口径左轮手枪（5发）",
        )

    def test_multiplayer_violence_confirmation_uses_the_acting_investigator_profile(self):
        world = make_multiplayer_world(active_id="inv-bob")
        world["investigators"]["inv-alice"]["backstory"] = {"violence_stance": "avoidant"}
        world["investigators"]["inv-bob"]["backstory"] = {"violence_stance": "unrestrained"}
        world["pc"] = copy.deepcopy(world["investigators"]["inv-bob"])
        world["pc"]["attributes"]["DEX"] = 95
        world["npcs"][0]["attributes"]["DEX"] = 60
        world["npcs"][0]["disposition"] = "cooperative"
        start_combat(world, [{"id": "cultist"}], "突然冲突")

        pending = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="melee",
            description="Bob 挥拳攻击",
        )

        self.assertEqual(
            pending["decision"]["responding_investigator_id"],
            "inv-bob",
        )
        self.assertEqual(
            pending["decision"]["roleplay_context"]["violence_stance"],
            "unrestrained",
        )
        self.assertIn("并不冲突", pending["decision"]["description"])

    def test_owner_skip_updates_authoritative_combat_turn_before_next_player_action(self):
        world = make_multiplayer_world(active_id="inv-alice")
        world["npcs"][0]["attributes"]["DEX"] = 60
        start_combat(world, [{"id": "cultist"}], "调查员遭到围攻")
        self.assertEqual(
            world["combat_state"]["turn_order"],
            ["inv-alice", "inv-bob", "cultist"],
        )

        assignment = assign_combat_actor(world, "inv-bob")
        self.assertEqual(assignment["actor_id"], "inv-bob")
        self.assertEqual(assignment["skipped_actor_ids"], ["inv-alice"])
        self.assertEqual(world["combat_state"]["turn_index"], 1)
        self.assertEqual(world["combat_state"]["current_actor"], "inv-bob")
        self.assertIn("跳过 inv-alice", world["combat_state"]["log"][-1]["text"])

        result = combat_action(
            world,
            actor_id="inv-bob",
            action_type="move",
            description="Bob 寻找掩体",
        )
        self.assertEqual(result["event"], "action_resolved")
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")

    def test_uncontrolled_defender_uses_safe_default_without_waiting_for_callback(self):
        world = make_multiplayer_world(active_id="inv-alice")
        world["npcs"][0]["attributes"]["DEX"] = 90
        start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")
        world["investigator_controllers"].pop("user-bob")
        world["investigators"]["inv-bob"]["controller_user_id"] = None

        result = combat_action(
            world,
            actor_id="cultist",
            target_id="inv-bob",
            action_type="melee",
            description="教徒攻击已经失去控制者的 Bob",
            rng=FixedRandom([1, 1, 9, 9, 2]),
        )

        self.assertFalse(result.get("requires_decision", False))
        self.assertTrue(result["decision"]["automatic"])
        self.assertEqual(
            result["decision"]["reason"],
            "target_investigator_uncontrolled",
        )
        self.assertEqual(result["decision"]["selected"], "dodge")
        self.assertIsNone(world["combat_state"]["pending_decision"])
        self.assertEqual(world["investigators"]["inv-bob"]["hp"], 10)

    def test_saved_legacy_pc_combat_is_normalized_without_breaking_singleplayer(self):
        world = make_multiplayer_world(active_id="inv-alice")
        world["npcs"][0]["attributes"]["DEX"] = 90
        start_combat(world, [{"id": "cultist"}], "旧存档")
        combat = world["combat_state"]
        participant = next(item for item in combat["participants"] if item["id"] == "inv-alice")
        participant["id"] = "pc"
        combat["turn_order"] = [
            "pc" if value == "inv-alice" else value for value in combat["turn_order"]
        ]
        combat["pending_decision"] = {
            "id": "legacy-decision",
            "kind": "combat_defense",
            "action": {"actor_id": "cultist", "target_id": "pc"},
            "options": [{"id": "dodge"}],
        }

        status = combat_status(copy.deepcopy(world))
        normalized = status["combat"]
        self.assertIn("inv-alice", normalized["turn_order"])
        self.assertNotIn("pc", normalized["turn_order"])
        self.assertEqual(
            normalized["pending_decision"]["responding_investigator_id"],
            "inv-alice",
        )

        legacy = make_world()
        start_combat(legacy, [{"id": "cultist"}], "单机")
        self.assertIn("pc", legacy["combat_state"]["turn_order"])

    def test_npc_attack_waits_for_player_decision_before_advancing(self):
        world = make_world()
        start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")

        pending = combat_action(
            world,
            actor_id="cultist",
            target_id="pc",
            action_type="melee",
            description="教徒挥拳扑来",
            defender_choice="fight_back",
        )

        # Even if the model supplied a choice, PC defense remains player-owned.
        self.assertTrue(pending["requires_decision"])
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")
        self.assertEqual(world["combat_state"]["phase"], "awaiting_decision")

        resolved = combat_decide(
            world,
            pending["decision"]["id"],
            "dodge",
            rng=random.Random(7),
        )
        self.assertEqual(resolved["attack_roll"]["skill_value"], 65)
        self.assertEqual(resolved["defense_roll"]["skill_value"], 40)
        self.assertEqual(world["combat_state"]["current_actor"], "pc")
        self.assertEqual(world["combat_state"]["phase"], "awaiting_action")

    def test_pc_attack_uses_npc_defense_and_round_wraps(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "正面冲突")

        result = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="melee",
            description="挥拳攻击",
            defender_choice="dodge",
            damage_spec="1d3",
            rng=random.Random(11),
        )

        self.assertEqual(result["attack_roll"]["skill_value"], 55)
        self.assertEqual(result["defense_roll"]["skill_value"], 35)
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")

        combat_action(
            world,
            actor_id="cultist",
            action_type="move",
            description="退到桌后",
        )
        self.assertEqual(world["combat_state"]["round"], 2)
        self.assertEqual(world["combat_state"]["current_actor"], "pc")

    def test_rejects_out_of_turn_action(self):
        world = make_world()
        start_combat(world, [{"id": "cultist"}], "伏击")

        with self.assertRaises(CombatError):
            combat_action(
                world,
                actor_id="pc",
                target_id="cultist",
                action_type="melee",
                description="抢先攻击",
            )

    def test_pc_stats_cannot_be_overridden_by_model(self):
        world = make_world()

        start_combat(
            world,
            [
                {"id": "pc", "dex": 999, "fighting_brawl": 999},
                {"id": "cultist"},
            ],
            "伏击",
        )

        pc = next(item for item in world["combat_state"]["participants"] if item["id"] == "pc")
        self.assertEqual(pc["dex"], 60)
        self.assertEqual(pc["skills"]["fighting_brawl"], 55)

    def test_major_wound_runs_con_check_and_updates_conditions(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        start_combat(world, [{"id": "cultist"}], "正面冲突")

        result = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="melee",
            description="重击",
            defender_choice="no_defense",
            damage_spec="1d6",
            rng=FixedRandom([1, 0, 6, 9, 9]),
        )

        self.assertTrue(result["damage"]["major_wound"])
        self.assertEqual(result["damage"]["major_wound_check"]["roll"], 99)
        self.assertIn("major_wound", world["npcs"][0]["conditions"])
        self.assertIn("unconscious", world["npcs"][0]["conditions"])

    def test_firearm_action_consumes_one_round_even_on_miss(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        start_combat(world, [{"id": "cultist"}], "枪战")

        result = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="firearm",
            description="向教徒开枪",
            weapon=".38口径左轮手枪",
            damage_spec="1d8",
            damage_mode="impaling",
            rng=FixedRandom([9, 9]),
        )

        self.assertEqual(result["outcome"], "miss")
        self.assertEqual(result["ammo"]["before"], 6)
        self.assertEqual(result["ammo"]["after"], 5)
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（5发）")

    def test_empty_firearm_does_not_roll_or_advance_turn(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        world["pc"]["inventory"][0] = ".38口径左轮手枪（0发）"
        start_combat(world, [{"id": "cultist"}], "枪战")

        with self.assertRaisesRegex(CombatError, "弹药不足"):
            combat_action(
                world,
                actor_id="pc",
                target_id="cultist",
                action_type="firearm",
                description="向教徒开枪",
                weapon="左轮手枪",
                damage_spec="1d8",
                rng=FixedRandom([]),
            )

        self.assertEqual(world["combat_state"]["current_actor"], "pc")
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（0发）")

    def test_non_hostile_attack_requires_confirmation_and_cancel_preserves_turn(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        world["pc"]["backstory"] = {
            "beliefs": "以头脑而非暴力追查真相",
            "traits": "克制而审慎",
            "violence_stance": "avoidant",
        }
        world["npcs"][0]["disposition"] = "cooperative"
        start_combat(world, [{"id": "cultist"}], "突然拔枪")

        pending = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="firearm",
            description="近距离朝对方开枪",
            weapon="左轮手枪",
            damage_spec="1d8",
        )

        self.assertTrue(pending["requires_decision"])
        self.assertEqual(pending["decision"]["kind"], "irreversible_violence")
        self.assertEqual(pending["decision"]["default_option"], "cancel_violence")
        self.assertEqual(pending["decision"]["options"][0]["label"], "克制冲动")
        self.assertEqual(pending["decision"]["roleplay_context"]["violence_stance"], "avoidant")
        self.assertIn("明显违背", pending["decision"]["description"])
        self.assertIn("以头脑而非暴力", pending["decision"]["description"])
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（6发）")

        cancelled = combat_decide(world, pending["decision"]["id"], "cancel_violence")
        self.assertEqual(cancelled["event"], "action_cancelled")
        self.assertFalse(cancelled["action_consumed"])
        self.assertFalse(cancelled["violence_confirmation"]["confirmed"])
        self.assertEqual(world["combat_state"]["current_actor"], "pc")
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（6发）")
        self.assertNotIn("violence_log", world)

    def test_declared_opening_attack_immediately_requires_confirmation(self):
        world = make_world()
        world["pc"]["backstory"] = {"violence_stance": "avoidant"}
        world["npcs"][0]["disposition"] = "cooperative"

        pending = start_combat(
            world,
            [{"id": "pc", "ready_firearm": True}, {"id": "cultist"}],
            "调查员突然拔枪射击",
            {
                "actor_id": "pc",
                "target_id": "cultist",
                "action_type": "firearm",
                "description": "近距离朝对方开枪",
                "weapon": "左轮手枪",
                "damage_spec": "1d8",
            },
        )

        self.assertTrue(pending["requires_decision"])
        self.assertEqual(pending["decision"]["kind"], "irreversible_violence")
        self.assertEqual(world["combat_state"]["phase"], "awaiting_decision")
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（6发）")

    def test_initial_weapon_threat_can_be_cancelled_without_escalation(self):
        world = make_world()
        world["pc"]["backstory"] = {
            "beliefs": "以头脑而非武力解决问题",
            "violence_stance": "avoidant",
        }
        world["npcs"][0]["disposition"] = "cooperative"
        world["case_clocks"] = {"human_pressure": 0}

        pending = start_combat(
            world,
            [{"id": "pc", "ready_firearm": True}, {"id": "cultist"}],
            "调查员拔枪指向对方",
            {
                "actor_id": "pc",
                "target_id": "cultist",
                "action_type": "threat",
                "description": "用左轮手枪指着对方逼问真相",
                "weapon": "左轮手枪",
            },
        )

        decision = pending["decision"]
        self.assertEqual(decision["kind"], "coercive_threat")
        self.assertEqual(decision["default_option"], "cancel_threat")
        self.assertEqual(decision["options"][0]["label"], "收起武器")
        self.assertIn("明显违背", decision["description"])

        cancelled = combat_decide(world, decision["id"], "cancel_threat")

        self.assertEqual(cancelled["event"], "action_cancelled")
        self.assertFalse(cancelled["action_consumed"])
        self.assertFalse(cancelled["combat"]["active"])
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（6发）")
        self.assertEqual(world["case_clocks"]["human_pressure"], 0)
        self.assertNotIn("threat_log", world)
        self.assertNotIn("threatened_by_pc", world["npcs"][0])

    def test_confirmed_weapon_threat_records_consequences_but_not_ammo(self):
        world = make_world()
        world["pc"]["backstory"] = {"violence_stance": "conditional"}
        world["npcs"][0]["disposition"] = "cooperative"
        world["case_clocks"] = {"human_pressure": 0}
        pending = start_combat(
            world,
            [{"id": "pc", "ready_firearm": True}, {"id": "cultist"}],
            "调查员拔枪指向对方",
            {
                "actor_id": "pc",
                "target_id": "cultist",
                "action_type": "threat",
                "description": "用左轮手枪指着对方逼问真相",
                "weapon": "左轮手枪",
            },
        )

        resolved = combat_decide(world, pending["decision"]["id"], "confirm_threat")

        self.assertEqual(resolved["outcome"], "threat_established")
        self.assertFalse(resolved["resource_consumed"])
        self.assertTrue(resolved["threat_confirmation"]["confirmed"])
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（6发）")
        self.assertTrue(world["npcs"][0]["threatened_by_pc"])
        self.assertEqual(world["npcs"][0]["disposition"], "guarded")
        self.assertEqual(world["threat_log"][-1]["target"], "cultist")
        self.assertEqual(world["case_clocks"]["human_pressure"], 1)
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")

        combat_action(world, actor_id="cultist", action_type="move", description="退到桌后")
        attack = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="firearm",
            description="扣动扳机",
            weapon="左轮手枪",
            damage_spec="1d8",
        )
        self.assertEqual(attack["decision"]["kind"], "irreversible_violence")

    def test_unrestrained_character_still_confirms_without_moralizing(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        world["pc"]["backstory"] = {
            "traits": "冷静、善于操纵他人",
            "violence_stance": "unrestrained",
        }
        world["pc"]["psychological_profile"] = {"traits": ["享受掌控局面"]}
        world["npcs"][0]["disposition"] = "cooperative"
        start_combat(world, [{"id": "cultist"}], "突然拔枪")

        pending = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="melee",
            description="毫无征兆地袭击对方",
            damage_spec="1d3",
        )

        decision = pending["decision"]
        self.assertTrue(pending["requires_decision"])
        self.assertEqual(decision["default_option"], "cancel_violence")
        self.assertEqual(decision["options"][0]["label"], "改换做法")
        self.assertIn("并不冲突", decision["description"])
        self.assertNotIn("明显违背", decision["description"])
        self.assertEqual(
            decision["roleplay_context"]["traits"],
            ["冷静、善于操纵他人", "享受掌控局面"],
        )

    def test_confirmed_non_hostile_attack_records_consequences_and_resolves(self):
        world = make_world()
        world["pc"]["attributes"]["DEX"] = 80
        world["npcs"][0]["disposition"] = "cooperative"
        world["current_scene"] = {"id": "office", "name": "大学办公室"}
        world["case_clocks"] = {"human_pressure": 0}
        start_combat(world, [{"id": "cultist"}], "突然拔枪")
        pending = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="firearm",
            description="近距离朝对方开枪",
            weapon="左轮手枪",
            damage_spec="1d8",
        )

        resolved = combat_decide(
            world,
            pending["decision"]["id"],
            "confirm_violence",
            rng=FixedRandom([9, 9]),
        )

        self.assertEqual(resolved["event"], "action_resolved")
        self.assertTrue(resolved["violence_confirmation"]["confirmed"])
        self.assertTrue(resolved["violence_confirmation"]["consequences_required"])
        self.assertEqual(resolved["ammo"]["after"], 5)
        self.assertTrue(world["npcs"][0]["hostile_to_pc"])
        self.assertTrue(world["combat_state"]["participants"][1]["hostile_to_pc"])
        self.assertEqual(world["case_clocks"]["human_pressure"], 1)
        self.assertEqual(world["violence_log"][-1]["target"], "cultist")
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")


class HostileSpecOverrideTests(unittest.TestCase):
    def test_spec_hostile_override_skips_violence_confirmation(self):
        """危机战斗参战者可经 spec 声明 hostile_to_pc——否则暴力确认门把危机
        伏击当成「攻击非敌对者」，真机里 12 回合射击全被 action_cancelled。"""
        world = make_world()
        world["npcs"][0]["disposition"] = "submissive"
        world["pc"]["attributes"]["DEX"] = 90  # PC 先动

        # 无覆盖：攻击非敌对 NPC → 暴力确认门
        start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")
        pending = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="firearm",
            description="朝教徒开枪",
            weapon="左轮手枪",
            damage_spec="1d8",
        )
        self.assertTrue(pending.get("requires_decision"))

        # spec 声明 hostile_to_pc → 直接结算，无确认门
        world = make_world()
        world["npcs"][0]["disposition"] = "submissive"
        world["pc"]["attributes"]["DEX"] = 90  # PC 先动
        start_combat(
            world,
            [{"id": "cultist", "damage_spec": "1d3", "hostile_to_pc": True}],
            "伏击",
        )
        result = combat_action(
            world,
            actor_id="pc",
            target_id="cultist",
            action_type="firearm",
            description="朝教徒开枪",
            weapon="左轮手枪",
            damage_spec="1d8",
        )
        self.assertFalse(result.get("requires_decision", False))
        self.assertNotEqual(result.get("event"), "decision_required")


if __name__ == "__main__":
    unittest.main()
