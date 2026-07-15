import unittest

from src.game_application import (
    ApplicationUseCaseError,
    GameApplication,
    SaveNotFoundError,
)


class _Engine:
    def __init__(self):
        self.calls = []
        self.load_result = 12
        self.saves = []

    def reset(self, character_ref=None):
        self.calls.append(("reset", character_ref))

    def load(self, slot_id=None):
        self.calls.append(("load", slot_id))
        return self.load_result

    def append_control_instruction(self, content):
        self.calls.append(("instruction", content))

    def handle_action(self, user_content=None):
        self.calls.append(("action", user_content))

    def rewrite_turn(self, turn_id):
        self.calls.append(("rewrite", turn_id))
        return {"turn_id": turn_id, "narrative": "rewritten"}

    def save(self, slot_id=None):
        self.calls.append(("save", slot_id))
        return slot_id or "manual-slot"

    def list_saves(self):
        return self.saves


class GameApplicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = _Engine()
        self.app = GameApplication.for_engine(self.engine)

    def test_start_resets_before_returning_opening_intent(self):
        intent = self.app.start_game.execute({"id": "investigator"})

        self.assertEqual("opening", intent.kind)
        self.assertEqual([("reset", {"id": "investigator"})], self.engine.calls)

    def test_resume_loads_and_appends_control_instruction(self):
        intent = self.app.resume_game.execute("slot_002")

        self.assertEqual("resume", intent.kind)
        self.assertEqual(12, intent.loaded_message_count)
        self.assertEqual("slot_002", intent.slot_id)
        self.assertEqual("load", self.engine.calls[0][0])
        self.assertEqual("instruction", self.engine.calls[1][0])

    def test_resume_missing_save_has_typed_error_and_no_instruction(self):
        self.engine.load_result = None

        with self.assertRaises(SaveNotFoundError) as raised:
            self.app.resume_game.execute("missing")

        self.assertEqual("missing", raised.exception.slot_id)
        self.assertEqual([("load", "missing")], self.engine.calls)

    def test_action_is_normalized_and_empty_action_rejected(self):
        intent = self.app.perform_action.execute("  查看书桌  ")
        self.assertEqual("查看书桌", intent.engine_input)
        self.assertEqual("查看书桌", intent.player_input)

        with self.assertRaises(ApplicationUseCaseError):
            self.app.perform_action.execute("   ")

    def test_rewrite_requires_id_and_delegates_exactly_once(self):
        result = self.app.rewrite_turn.execute(" turn-7 ")

        self.assertEqual("turn-7", result["turn_id"])
        self.assertEqual(("rewrite", "turn-7"), self.engine.calls[-1])
        with self.assertRaises(ApplicationUseCaseError):
            self.app.rewrite_turn.execute("")

    def test_save_slot_allocation_fills_first_gap_and_skips_auto_slot(self):
        self.engine.saves = [
            {"id": "slot_000"},
            {"id": "slot_001"},
            {"id": "slot_003"},
            {"id": "named-save"},
        ]

        slot_id = self.app.manage_saves.create_slot()

        self.assertEqual("slot_002", slot_id)
        self.assertEqual(("save", "slot_002"), self.engine.calls[-1])


if __name__ == "__main__":
    unittest.main()
