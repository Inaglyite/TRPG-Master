import unittest

from src.history_compactor import build_summary_input, parse_summary_json


class HistoryCompactorTests(unittest.TestCase):
    def test_summary_input_omits_system_and_raw_tool_content_but_keeps_pairing(self):
        text = build_summary_input(
            [
                {"role": "system", "content": "secret"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "dice_roll", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "x" * 300},
                {"role": "assistant", "content": "结果"},
            ]
        )

        self.assertNotIn("secret", text)
        self.assertNotIn("x" * 20, text)
        self.assertIn("assistant_tool_call]: dice_roll", text)
        self.assertIn("tool_result]: dice_roll", text)
        self.assertIn("结果", text)

    def test_parser_accepts_fenced_and_repairs_truncated_json(self):
        self.assertEqual('{"ok": true}', parse_summary_json('```json\n{"ok": true}\n```'))
        self.assertEqual(
            {"items": [1, 2]},
            __import__("json").loads(parse_summary_json('{"items": [1, 2')),
        )


if __name__ == "__main__":
    unittest.main()
