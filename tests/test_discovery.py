import json
import tempfile
import unittest
from pathlib import Path

from src.action_resolution import ActionPhase, plan_player_action
from src.agent_graph import _prepare_turn
from src.config import PROJECT_ROOT
from src.discovery import (
    match_discovery_rules,
    preferred_check_skill,
    preferred_luck_difficulty,
)
from src.encounters import resolve_scene_encounters
from src.engine import EngineCallbacks, GameEngine
from src.runtime import RuntimeContext


def discovery_world() -> dict:
    return {
        "pc": {"skills": {"spot_hidden": 70}},
        "current_scene": {"id": "morgue"},
        "clues_found": {"investigation": []},
        "clue_catalog": {
            "body": {
                "id": "body",
                "source": "morgue",
                "related_scenes": ["morgue"],
                "discovery_rules": [{
                    "intent": "examine",
                    "targets": ["教授遗体", "尸体"],
                    "skill": "spot_hidden",
                    "requires_success": True,
                }],
            },
        },
    }


class DiscoveryMatchingTests(unittest.TestCase):
    def test_declarative_encounter_supports_luck_and_absence(self):
        world = {
            "flags": {"campus_open": True},
            "npcs": [{
                "id": "professor",
                "current_location": "office",
            }],
            "scene_catalog": {
                "office": {
                    "encounters": [{
                        "id": "professor_after_hours",
                        "npc_id": "professor",
                        "availability": "luck",
                        "required_flags": {"campus_open": True},
                        "on_present_text": "教授恰好还在办公室。",
                        "on_absent_text": "办公室已经空了。",
                    }],
                },
            },
        }

        absent = resolve_scene_encounters(
            "office", world, luck_check=lambda _difficulty: {
                "success": False, "d100_roll": 88, "skill_value": 60,
            }
        )
        self.assertEqual(absent.present_npc_ids, ())
        self.assertEqual(absent.narrative_text, "办公室已经空了。")

        present = resolve_scene_encounters(
            "office", world, luck_check=lambda _difficulty: {
                "success": True, "d100_roll": 12, "skill_value": 60,
            }
        )
        self.assertEqual(present.present_npc_ids, ("professor",))
        self.assertEqual(present.narrative_text, "教授恰好还在办公室。")

        world["encounter_history"] = {
            "office": {
                "professor_after_hours": {
                    "present": False,
                    "check_result": {"d100_roll": 88, "skill_value": 60},
                },
            },
        }
        rerolls = 0

        def unexpected_roll(_difficulty: str) -> dict:
            nonlocal rerolls
            rerolls += 1
            return {"d100_roll": 1, "skill_value": 60, "success": True}

        cached = resolve_scene_encounters(
            "office", world, luck_check=unexpected_roll
        )
        self.assertEqual(cached.present_npc_ids, ())
        self.assertTrue(cached.outcomes[0].cached)
        self.assertEqual(rerolls, 0)

    def test_scarlet_opening_does_not_pregrant_document_contacts(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json")
            .read_text(encoding="utf-8")
        )
        initial_text = " ".join(
            str(clue.get("text") or "")
            for clues in world["clues_found"].values()
            for clue in clues
        )
        self.assertNotIn("哈兰德·洛奇", initial_text)
        self.assertNotIn("艾米莉亚·考特", initial_text)

        matches = match_discovery_rules(
            "我问法伦：还有谁参与文档评估？",
            world,
        )
        self.assertEqual(
            [match.clue_id for match in matches],
            ["fallon_document_contacts"],
        )

    def test_scarlet_history_department_is_an_authoritative_scene(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json")
            .read_text(encoding="utf-8")
        )
        world["current_scene"] = world["scene_catalog"]["miskatonic_medical"]

        action = plan_player_action("立刻前往历史系找艾米莉亚·考特。", world)

        self.assertEqual(action.phase, ActionPhase.ARRIVAL)
        self.assertEqual(action.destination_scene_id, "miskatonic_history")
        self.assertEqual(
            world["scene_catalog"]["miskatonic_history"]["npcs_present"],
            ["emilia_court"],
        )

        by_name = plan_player_action("我去找考特谈谈。", world)
        self.assertEqual(by_name.destination_scene_id, "miskatonic_history")

        lodge = plan_player_action("接下来去找哈兰德·洛奇。", world)
        self.assertEqual(lodge.destination_scene_id, "miskatonic_lodge_office")

        # Old saves may retain a broad university location.  Authored scene
        # presence is the stronger navigation authority.
        next(npc for npc in world["npcs"] if npc["id"] == "harland_lodge")[
            "current_location"
        ] = "miskatonic_university"
        old_save_lodge = plan_player_action("去东翼二层找哈兰德·洛奇。", world)
        self.assertEqual(
            old_save_lodge.destination_scene_id,
            "miskatonic_lodge_office",
        )

    def test_scarlet_scene_graph_covers_authored_npc_locations_and_returns(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json")
            .read_text(encoding="utf-8")
        )
        scenes = world["scene_catalog"]
        for scene_id, scene in scenes.items():
            for exit_id in scene["exits"]:
                self.assertIn(exit_id, scenes, f"{scene_id} has unknown exit")
            for npc_id in scene["npcs_present"]:
                npc = next(npc for npc in world["npcs"] if npc["id"] == npc_id)
                self.assertEqual(npc["current_location"], scene_id)

        for npc in world["npcs"]:
            location = npc.get("current_location")
            if location == "unknown":
                continue
            self.assertIn(location, scenes, npc["id"])
            self.assertIn(npc["id"], scenes[location]["npcs_present"])

        hub = "miskatonic_university"
        for scene_id, scene in scenes.items():
            if scene_id != hub and hub in scene["exits"]:
                self.assertIn(scene_id, scenes[hub]["exits"])

    def test_action_resolution_separates_arrival_from_contact(self):
        world = discovery_world()
        world["current_scene"] = {"id": "campus"}
        world["scene_catalog"] = {
            "campus": {"id": "campus", "name": "大学"},
            "morgue": {"id": "morgue", "name": "停尸房"},
        }

        arrival = plan_player_action("我去停尸房检查教授遗体。", world)
        self.assertEqual(arrival.phase, ActionPhase.ARRIVAL)
        self.assertEqual(arrival.destination_scene_id, "morgue")
        self.assertFalse(arrival.permits_discovery_effects)
        self.assertEqual(arrival.discovery_matches, ())

        world["current_scene"] = {"id": "morgue"}
        contact = plan_player_action("我仔细检查教授遗体。", world)
        self.assertEqual(contact.phase, ActionPhase.CONTACT)
        self.assertTrue(contact.permits_discovery_effects)
        self.assertEqual([match.clue_id for match in contact.discovery_matches], ["body"])

    def test_matches_explicit_action_and_declared_skill(self):
        world = discovery_world()

        matches = match_discovery_rules("我仔细检查教授遗体的眼睛。", world)

        self.assertEqual([match.clue_id for match in matches], ["body"])
        self.assertEqual(preferred_check_skill(matches, world), "spot_hidden")

    def test_discovery_can_declare_luck_instead_of_a_skill(self):
        world = discovery_world()
        rule = world["clue_catalog"]["body"]["discovery_rules"][0]
        rule.pop("skill")
        rule["check_type"] = "luck"
        rule["difficulty"] = "hard"

        matches = match_discovery_rules("我仔细检查教授遗体。", world)

        self.assertIsNone(preferred_check_skill(matches, world))
        self.assertEqual(preferred_luck_difficulty(matches), "hard")

    def test_rejects_negated_or_discussed_action(self):
        world = discovery_world()

        self.assertEqual(
            match_discovery_rules("我暂时不检查教授遗体。", world),
            [],
        )
        self.assertEqual(
            match_discovery_rules("请问我能不能检查教授遗体？", world),
            [],
        )
        self.assertEqual(
            match_discovery_rules("我问医生：你检查过教授遗体吗？", world),
            [],
        )
        self.assertEqual(
            match_discovery_rules("我让医生检查教授遗体。", world),
            [],
        )

    def test_does_not_rediscover_known_clue(self):
        world = discovery_world()
        world["clues_found"]["investigation"].append({"catalog_id": "body"})

        self.assertEqual(
            match_discovery_rules("我检查教授遗体。", world),
            [],
        )


class DiscoveryResolutionTests(unittest.TestCase):
    def test_arrival_can_find_an_authored_npc_location_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "empty-lodge-office",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            handouts: list[dict] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.cb = EngineCallbacks(on_handout=handouts.append)
            world = context.world_store.load()
            lodge_index = next(
                index for index, npc in enumerate(world["npcs"])
                if npc["id"] == "harland_lodge"
            )
            engine._execute_tool("state_set", {
                "path": f"npcs.{lodge_index}.current_location",
                "value": '"miskatonic_student_commons"',
            })

            engine._execute_tool("state_set", {
                "path": "current_scene.id",
                "value": '"miskatonic_lodge_office"',
            })

            current = context.world_store.load()["current_scene"]
            self.assertEqual(current["id"], "miskatonic_lodge_office")
            self.assertEqual(current["npcs_present"], [])
            self.assertNotIn(
                "harland_lodge",
                [event.get("entity_id") for event in handouts],
            )

    def test_morgue_arrival_does_not_examine_body_or_trigger_effects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-timeline",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            events: list[tuple[str, str]] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = []
            engine._player_turn_count = 0
            engine.narrative_model = "story-model"
            engine.cb = EngineCallbacks(
                on_narrative=lambda text: events.append(("narrative", text)),
                on_tension=lambda _text, category: events.append(("tension", category)),
                on_dice=lambda summary, _data: events.append(("dice", summary)),
                on_handout=lambda info: events.append(("handout", info["asset_id"])),
            )
            engine._maybe_inject_tier = lambda: None
            engine._detect_content_skill_hint = lambda _content: None
            engine._retrieve_lore_context = lambda _content=None: None
            engine._resolve_action_check = lambda *_args: events.append(
                ("check", "unexpected")
            )

            result = _prepare_turn({
                "engine": engine,
                "user_content": (
                    "我前往密斯卡托尼克大学医学院，"
                    "去见惠特克罗夫特医生并查看莱特教授的遗体。"
                ),
            })

            narrative_events = [value for kind, value in events if kind == "narrative"]
            self.assertTrue(narrative_events)
            self.assertNotIn("白布掀起", narrative_events[0])
            self.assertNotIn("check", [event[0] for event in events])
            dice_events = [value for kind, value in events if kind == "dice"]
            self.assertEqual(dice_events, [])
            self.assertEqual(
                [value for kind, value in events if kind == "handout"],
                ["john_whitcroft"],
            )
            self.assertTrue(result["narrative"].startswith("你前往密斯卡托尼克大学医学院"))
            self.assertIn("本轮已向玩家展示的前置叙事", engine.messages[-1]["content"])
            self.assertIn('"arrival_only":true', engine.messages[-1]["content"])

            blocked = engine._execute_model_tool(
                "sanity_event",
                {"description": "看见莱特遗体", "severity": "minor", "clue_id": ""},
                player_action="前往停尸间查看莱特遗体",
            )
            self.assertEqual(
                __import__("json").loads(blocked)["error"],
                "arrival_turn_effect_not_authorized",
            )

    def test_declared_body_event_commits_before_story_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preflight",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            handouts: list[dict] = []
            dice: list[tuple[str, dict]] = []
            tension: list[tuple[str, str]] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.cb = EngineCallbacks(
                on_handout=handouts.append,
                on_dice=lambda summary, data: dice.append((summary, data)),
                on_tension=lambda text, category: tension.append((text, category)),
            )
            engine._execute_tool("state_set", {
                "path": "current_scene.id",
                "value": '"miskatonic_medical"',
            })

            matches, skill = engine._match_discoveries(
                "我掀开白布，检查莱特教授的遗体。"
            )
            resolved = engine._resolve_discoveries(matches, None)

            self.assertIsNone(skill)
            self.assertEqual(len(resolved), 1)
            self.assertTrue(resolved[0]["discovered"])
            self.assertEqual(resolved[0]["clue_id"], "wright_body_evidence")
            self.assertEqual(len(dice), 1)
            self.assertEqual(tension[0][1], "sanity")
            self.assertEqual(
                [event["asset_id"] for event in handouts if event.get("asset_id") == "wright_body"],
                ["wright_body"],
            )
            self.assertIn(
                "john_whitcroft",
                [event.get("asset_id") for event in handouts],
            )
            world = context.world_store.load()
            self.assertTrue(world["flags"]["body_examined"])
            self.assertIn(
                "john_whitcroft",
                world["seen_handouts"]["npcs"],
            )
            found_ids = {
                clue.get("catalog_id") or clue.get("id")
                for clues in world["clues_found"].values()
                for clue in clues
            }
            self.assertIn("wright_body_evidence", found_ids)


if __name__ == "__main__":
    unittest.main()
