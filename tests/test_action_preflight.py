import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.engine import GameEngine
from src.world_store import WorldStore


def make_world() -> dict:
    return {
        "pc": {
            "name": "黄千陆",
            "backstory": {
                "beliefs": "以头脑解决问题",
                "violence_stance": "avoidant",
            },
        },
        "current_scene": {
            "id": "office",
            "name": "法伦主任办公室",
            "npcs_present": ["bryce_fallon"],
        },
        "npcs": [{
            "id": "bryce_fallon",
            "name": "布莱斯·法伦",
            "disposition": "cooperative",
        }],
    }


class FakeGraph:
    def __init__(self, events: list[str]):
        self.events = events
        self.inputs = []

    def invoke(self, state, config=None):
        self.events.append("graph")
        self.inputs.append((state, config))


class ActionPreflightTests(unittest.TestCase):
    def _engine(
        self,
        selected: str,
        events: list[str],
        store: WorldStore | None = None,
    ) -> GameEngine:
        engine = GameEngine.__new__(GameEngine)
        if store is not None:
            engine.context = SimpleNamespace(world_store=store)

        def on_decision(_decision):
            events.append("decision")
            return selected

        engine.cb = SimpleNamespace(
            on_decision=on_decision,
            on_done=lambda: events.append("done"),
        )
        engine.messages = []
        engine._preconfirmed_escalation = None
        engine._resume_pending_combat_decision = lambda: None
        engine._turn_graph = FakeGraph(events)
        engine.save = lambda _slot: events.append("save")
        return engine

    def test_confirmation_happens_before_graph_and_first_model_token(self):
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(make_world())
            engine = self._engine("confirm_violence", events, store)
            engine.handle_action("朝着法伦开枪")

        self.assertEqual(events, ["decision", "graph"])
        submitted = engine._turn_graph.inputs[0][0]["user_content"]
        self.assertIn("玩家已在叙事开始前确认", submitted)

    def test_cancelling_preflight_never_starts_gm_graph(self):
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(make_world())
            engine = self._engine("cancel_violence", events, store)
            engine.handle_action("朝着法伦开枪")

        self.assertEqual(events, ["decision", "save", "done"])
        self.assertEqual(engine._turn_graph.inputs, [])
        self.assertIn("行动发生前取消", engine.messages[-1]["content"])

    def test_matching_tool_confirmation_consumes_one_time_authorization(self):
        engine = self._engine("confirm_violence", [])
        engine._preconfirmed_escalation = {
            "kind": "irreversible_violence",
            "target_id": "bryce_fallon",
            "confirm_option": "confirm_violence",
        }

        selected = engine._preconfirmed_option({
            "kind": "irreversible_violence",
            "target_id": "bryce_fallon",
        })

        self.assertEqual(selected, "confirm_violence")
        self.assertIsNone(engine._preconfirmed_escalation)


if __name__ == "__main__":
    unittest.main()
