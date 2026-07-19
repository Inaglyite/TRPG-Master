import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.npc_conversations import commit_npc_conversations
from src.world_store import WorldStore


class NarrativeMemoryTests(unittest.TestCase):
    def test_speech_is_persisted_and_deduplicated_without_granting_clue(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldStore(Path(tmp))
            store.initialize({"clues_found": {"npc": []}})
            engine = SimpleNamespace(context=SimpleNamespace(world_store=store))
            segments = [{
                "kind": "speech",
                "npc_id": "bryce_fallon",
                "text": "莱特教授在死前几周表现得十分反常。",
            }]

            self.assertEqual(commit_npc_conversations(engine, segments), 1)
            self.assertEqual(commit_npc_conversations(engine, segments), 0)

            state = store.load()
            self.assertEqual(len(state["npc_conversations"]["bryce_fallon"]), 1)
            self.assertEqual(state["clues_found"]["npc"], [])


if __name__ == "__main__":
    unittest.main()
