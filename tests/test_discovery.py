import tempfile
import unittest
from pathlib import Path

from src.config import PROJECT_ROOT
from src.discovery import match_discovery_rules, preferred_check_skill
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
    def test_matches_explicit_action_and_declared_skill(self):
        world = discovery_world()

        matches = match_discovery_rules("我仔细检查教授遗体的眼睛。", world)

        self.assertEqual([match.clue_id for match in matches], ["body"])
        self.assertEqual(preferred_check_skill(matches, world), "spot_hidden")

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
            world = context.world_store.load()
            self.assertTrue(world["flags"]["body_examined"])
            found_ids = {
                clue.get("catalog_id") or clue.get("id")
                for clues in world["clues_found"].values()
                for clue in clues
            }
            self.assertIn("wright_body_evidence", found_ids)


if __name__ == "__main__":
    unittest.main()
