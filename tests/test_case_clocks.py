import unittest

from src.case_clocks import clock_status


class ClockStatusTests(unittest.TestCase):
    def test_passthrough_without_definitions(self):
        self.assertEqual(clock_status({"danger": 2}, {}), {"danger": 2})
        self.assertEqual(clock_status(None, {"danger": {}}), {})

    def test_enriches_with_next_level_and_advance_when(self):
        definitions = {
            "monster_manifestation": {
                "max": 6,
                "levels": {"1": "寒意、电灯闪烁", "2": "红色鬼火"},
                "advance_when": ["拖延", "夜间独处"],
            }
        }
        status = clock_status({"monster_manifestation": 1}, definitions)
        entry = status["monster_manifestation"]
        self.assertEqual(entry["value"], 1)
        self.assertEqual(entry["next_level"], "红色鬼火")
        self.assertEqual(entry["max"], 6)
        self.assertEqual(entry["advance_when"], ["拖延", "夜间独处"])

    def test_top_level_clock_has_no_next_level(self):
        definitions = {"monster_manifestation": {"max": 6, "levels": {"6": "完全显形"}}}
        status = clock_status({"monster_manifestation": 6}, definitions)
        entry = status["monster_manifestation"]
        self.assertNotIn("next_level", entry)
        self.assertEqual(entry["max"], 6)

    def test_clock_without_definition_stays_scalar(self):
        status = clock_status(
            {"a": 1, "b": 0},
            {"a": {"levels": {"2": "下一级"}}},
        )
        self.assertEqual(status["a"], {"value": 1, "next_level": "下一级"})
        self.assertEqual(status["b"], 0)


if __name__ == "__main__":
    unittest.main()
