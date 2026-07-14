import tempfile
import unittest
from pathlib import Path

from src.characters import list_character_options
from src.config import PROJECT_ROOT
from src.engine import GameEngine
from src.runtime import RuntimeContext


class CharacterListTests(unittest.TestCase):
    def test_character_summary_contains_start_screen_dossier(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.local(
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )

            options = list_character_options(context=context)
            default_group = next(
                group for group in options["groups"] if group["id"] == "default"
            )
            character = next(
                item for item in default_group["characters"] if item["name"] == "黄千陆"
            )

            self.assertEqual(character["attributes"]["INT"], 65)
            self.assertEqual(character["derived"]["LUCK"], 55)
            self.assertIn("笔记本与钢笔", character["inventory"])
            self.assertIn("行动是最好的回击", character["backstory"]["beliefs"])
            self.assertTrue(character["top_skills"])

    def test_new_game_applies_selected_character_before_opening_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.local(
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            options = list_character_options(context=context)
            character = next(
                item
                for group in options["groups"]
                for item in group["characters"]
                if item["name"] == "黄千陆"
            )

            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            selected = engine.reset(character["ref"])

            self.assertEqual(selected["name"], "黄千陆")
            self.assertEqual(context.world_store.load()["pc"]["name"], "黄千陆")
            self.assertIn('"name": "黄千陆"', engine.messages[-1]["content"])

    def test_module_starting_items_are_merged_with_selected_character(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.local(
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            options = list_character_options(context=context)
            character = next(
                item
                for group in options["groups"]
                for item in group["characters"]
                if item["id"] == "default:黄千陆"
            )
            engine = GameEngine.__new__(GameEngine)
            engine.context = context

            engine.reset(character["ref"])

            inventory = context.world_store.load()["pc"]["inventory"]
            self.assertIn("手电筒", inventory)
            self.assertIn("莱特办公室的黄铜钥匙", inventory)
            self.assertIn("莱特小屋的黄铜钥匙", inventory)


if __name__ == "__main__":
    unittest.main()
