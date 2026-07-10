import random
import unittest

from src.combat import CombatError, combat_action, combat_decide, start_combat


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


class CombatStateMachineTests(unittest.TestCase):
    def test_start_uses_dex_and_persists_authoritative_state(self):
        world = make_world()

        result = start_combat(world, [{"id": "cultist", "damage_spec": "1d3"}], "伏击")

        self.assertTrue(result["ok"])
        self.assertEqual(world["combat_state"]["turn_order"], ["cultist", "pc"])
        self.assertEqual(world["combat_state"]["current_actor"], "cultist")
        self.assertEqual(world["combat_state"]["round"], 1)

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

        start_combat(world, [
            {"id": "pc", "dex": 999, "fighting_brawl": 999},
            {"id": "cultist"},
        ], "伏击")

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

        with self.assertRaisesRegex(CombatError, "没有子弹"):
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


if __name__ == "__main__":
    unittest.main()
