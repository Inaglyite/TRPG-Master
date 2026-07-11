import unittest

from src.personality import investigator_roleplay_profile, normalize_violence_stance
from tools.character import create_character


class PersonalityTests(unittest.TestCase):
    def test_old_character_defaults_to_conditional(self):
        profile = investigator_roleplay_profile({"backstory": {"traits": "谨慎"}})

        self.assertEqual(profile["violence_stance"], "conditional")
        self.assertEqual(profile["traits"], ["谨慎"])

    def test_profile_merges_background_and_acquired_traits(self):
        profile = investigator_roleplay_profile({
            "backstory": {
                "beliefs": "必要时不惜动手",
                "traits": "冷静且善于操纵他人",
                "violence_stance": "unrestrained",
            },
            "psychological_profile": {
                "traits": ["冷静且善于操纵他人", "享受掌控局面"],
            },
        })

        self.assertEqual(profile["violence_stance"], "unrestrained")
        self.assertEqual(profile["beliefs"], "必要时不惜动手")
        self.assertEqual(profile["traits"], ["冷静且善于操纵他人", "享受掌控局面"])

    def test_chinese_alias_and_unknown_value_are_safe(self):
        self.assertEqual(normalize_violence_stance("非暴力"), "avoidant")
        self.assertEqual(normalize_violence_stance("something-new"), "conditional")

    def test_character_creation_persists_explicit_stance(self):
        character = create_character(
            "汉尼拔",
            "医生",
            quick=True,
            violence_stance="unrestrained",
        )

        self.assertEqual(character["backstory"]["violence_stance"], "unrestrained")


if __name__ == "__main__":
    unittest.main()
