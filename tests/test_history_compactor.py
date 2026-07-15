import unittest

from src.history_compactor import build_summary_input, parse_summary_json


class HistoryCompactorTests(unittest.TestCase):
    def test_summary_input_omits_system_and_bounds_tool_content(self):
        text = build_summary_input([
            {"role": "system", "content": "secret"},
            {"role": "tool", "content": "x" * 300},
            {"role": "assistant", "content": "结果"},
        ])

        self.assertNotIn("secret", text)
        self.assertIn("x" * 200 + "...", text)
        self.assertIn("结果", text)

    def test_parser_accepts_fenced_and_repairs_truncated_json(self):
        self.assertEqual('{"ok": true}', parse_summary_json('```json\n{"ok": true}\n```'))
        self.assertEqual(
            {"items": [1, 2]},
            __import__("json").loads(parse_summary_json('{"items": [1, 2')),
        )


if __name__ == "__main__":
    unittest.main()
