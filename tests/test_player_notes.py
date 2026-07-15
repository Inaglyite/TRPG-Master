import tempfile
import unittest
from pathlib import Path

from src.player_notes import (
    MAX_PLAYER_NOTES_CHARS,
    PlayerNotesConflict,
    PlayerNotesStore,
)


class PlayerNotesTests(unittest.TestCase):
    def test_notes_use_optimistic_revision_and_atomic_file(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PlayerNotesStore(Path(temp) / "world")
            self.assertEqual(0, store.load()["revision"])

            first = store.save("法伦知道钥匙的位置。", expected_revision=0)
            self.assertEqual(1, first["revision"])
            self.assertEqual("法伦知道钥匙的位置。", store.load()["text"])

            with self.assertRaises(PlayerNotesConflict):
                store.save("覆盖", expected_revision=0)
            self.assertEqual("法伦知道钥匙的位置。", store.load()["text"])

    def test_notes_reject_oversized_content(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PlayerNotesStore(Path(temp) / "world")
            with self.assertRaisesRegex(ValueError, "不能超过"):
                store.save("x" * (MAX_PLAYER_NOTES_CHARS + 1))


if __name__ == "__main__":
    unittest.main()
