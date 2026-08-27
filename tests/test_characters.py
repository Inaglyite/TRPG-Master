import json
import tempfile
import unittest
from pathlib import Path

from src.app.config import PROJECT_ROOT
from src.app.engine import GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.characters import list_character_options


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
            self.assertEqual(character["source_label"], "默认调查员")

    def test_shared_room_character_list_does_not_expose_host_personal_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.local(
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            context.custom_characters_dir.mkdir(parents=True, exist_ok=True)
            (context.custom_characters_dir / "secret.json").write_text(
                json.dumps({"name": "主机私有角色", "occupation": "记者"})
            )
            context.profiles_dir.mkdir(parents=True, exist_ok=True)
            context.player_profile_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "characters": {
                            "private": {
                                "name": "另一账号的长期角色",
                                "character": {
                                    "name": "另一账号的长期角色",
                                    "occupation": "医生",
                                },
                            }
                        },
                    }
                )
            )

            options = list_character_options(
                context=context,
                include_personal=False,
            )
            groups = {group["id"]: group["characters"] for group in options["groups"]}
            self.assertEqual([], groups["profile"])
            self.assertEqual([], groups["custom"])
            self.assertTrue(groups["default"])
            # 模组角色目录可能只由本地安装的模组包提供；这个隐私测试不应
            # 依赖开发机上被 .gitignore 忽略的用户角色文件。
            self.assertIsInstance(groups["module"], list)

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
            self.assertIn(
                "module_opening 是开场演出脚本",
                engine.messages[-1]["content"],
            )
            self.assertIn(
                "以“**你可以——**”开头",
                engine.messages[-1]["content"],
            )

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
