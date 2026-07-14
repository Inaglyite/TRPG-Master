import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.endings import validate_ending
from src.tools import execute_function
from src.world_store import WorldStore


def ending_world(*, recovered: bool = False, defeated: bool = False) -> dict:
    return {
        "pc": {"name": "调查员"},
        "flags": {
            "documents_recovered": recovered,
            "monster_defeated": defeated,
        },
        "endings": [{
            "id": "truth_and_seal",
            "title": "真相大白，怪物被制伏",
            "ending_type": "good",
            "description": "墨中怪物被重新封印。",
            "required_flags": {
                "documents_recovered": True,
                "monster_defeated": True,
            },
        }],
    }


class EndingValidationTests(unittest.TestCase):
    def test_configured_ending_rejects_missing_flags(self):
        result = validate_ending(
            ending_world(recovered=True, defeated=False),
            {"ending_id": "truth_and_seal", "ending_type": "good"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["missing_flags"], {"monster_defeated": True})

    def test_configured_ending_uses_authoritative_definition(self):
        result = validate_ending(
            ending_world(recovered=True, defeated=True),
            {
                "ending_id": "truth_and_seal",
                "ending_type": "bad",
                "title": "伪造标题",
                "summary": "",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ending_type"], "good")
        self.assertEqual(result["title"], "真相大白，怪物被制伏")

    def test_end_game_tool_does_not_write_rejected_ending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(ending_world(recovered=True, defeated=False))
            context = SimpleNamespace(world_store=store)

            output = execute_function(
                "end_game",
                {
                    "ending_id": "truth_and_seal",
                    "ending_type": "good",
                    "title": "真相大白，怪物被制伏",
                    "summary": "故事结束。",
                },
                context=context,
            )

            result = json.loads(output)
            self.assertFalse(result["game_over"])
            self.assertNotIn("game_over", store.load())


if __name__ == "__main__":
    unittest.main()
