import unittest

from src.choices import extract_action_choices


class ChoiceProtocolTests(unittest.TestCase):
    def test_extracts_only_numbered_menu_after_explicit_marker(self):
        narrative = """证据编号如下：
1. 旧钥匙
2. 焦黑纸片

**你可以——**
1. **检查书桌**
2. 询问法伦
3. [自由行动] 你决定做什么？
"""

        choices = extract_action_choices(narrative)

        self.assertEqual(
            [choice["label"] for choice in choices],
            ["检查书桌", "询问法伦", "[自由行动] 你决定做什么？"],
        )
        self.assertFalse(choices[0]["isFree"])
        self.assertTrue(choices[-1]["isFree"])

    def test_does_not_treat_unmarked_numbered_prose_as_actions(self):
        narrative = "已确认三项事实：\n1. 门被锁住\n2. 窗户完好"

        self.assertEqual(extract_action_choices(narrative), [])


if __name__ == "__main__":
    unittest.main()
