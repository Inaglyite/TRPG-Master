import unittest

from src.consequences import SanitySeverity, classify_sanity_consequence


class ConsequenceTests(unittest.TestCase):
    def test_specific_severity_precedes_generic_corpse_keyword(self):
        consequence = classify_sanity_consequence("第一次看到恐怖尸体与血肉模糊的伤口")

        self.assertEqual(SanitySeverity.MODERATE, consequence.severity)
        self.assertEqual("1/1D6+1", consequence.loss_expression)

    def test_ambience_can_be_trivial_but_unknown_defaults_to_moderate(self):
        self.assertEqual(
            SanitySeverity.TRIVIAL,
            classify_sanity_consequence("房间里有挥之不去的不安").severity,
        )
        self.assertEqual(
            SanitySeverity.MODERATE,
            classify_sanity_consequence("无法归类的明确恐怖事件").severity,
        )


if __name__ == "__main__":
    unittest.main()
