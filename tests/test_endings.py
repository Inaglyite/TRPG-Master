import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.ai.tools.registry import execute_function
from src.gameplay.endings import validate_ending
from src.storage.world_store import WorldStore


def ending_world(*, recovered: bool = False, defeated: bool = False) -> dict:
    return {
        "pc": {"name": "调查员"},
        "flags": {
            "documents_recovered": recovered,
            "monster_defeated": defeated,
        },
        "endings": [{
            "id": "truth_and_seal",
            "title": "真相大白，怪物被制伏",
            "ending_type": "good",
            "description": "墨中怪物被重新封印。",
            "required_flags": {
                "documents_recovered": True,
                "monster_defeated": True,
            },
        }],
    }


class EndingValidationTests(unittest.TestCase):
    def test_configured_ending_rejects_missing_flags(self):
        result = validate_ending(
            ending_world(recovered=True, defeated=False),
            {"ending_id": "truth_and_seal", "ending_type": "good"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["missing_flags"], {"monster_defeated": True})

    def test_configured_ending_uses_authoritative_definition(self):
        result = validate_ending(
            ending_world(recovered=True, defeated=True),
            {
                "ending_id": "truth_and_seal",
                "ending_type": "bad",
                "title": "伪造标题",
                "summary": "",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ending_type"], "good")
        self.assertEqual(result["title"], "真相大白，怪物被制伏")

    def test_end_game_tool_does_not_write_rejected_ending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(ending_world(recovered=True, defeated=False))
            context = SimpleNamespace(world_store=store)

            output = execute_function(
                "end_game",
                {
                    "ending_id": "truth_and_seal",
                    "ending_type": "good",
                    "title": "真相大白，怪物被制伏",
                    "summary": "故事结束。",
                },
                context=context,
            )

            result = json.loads(output)
            self.assertFalse(result["game_over"])
            self.assertNotIn("game_over", store.load())


if __name__ == "__main__":
    unittest.main()


def test_legacy_ending_is_atomic_source_bound_and_idempotent(tmp_path):
    from src.gameplay.endings import ending_readiness

    store = WorldStore(tmp_path / "world")
    store.initialize(ending_world(recovered=True, defeated=True))
    context = SimpleNamespace(world_store=store)
    source = ending_readiness(store.load())["source_token"]
    store.update(lambda state: state.setdefault("flags", {}).update(extra=True))
    stale = json.loads(execute_function("end_game", {"ending_id": "truth_and_seal", "source_token": source}, context=context))
    assert stale["error"] == "ending_source_changed"
    assert "game_over" not in store.load()
    store.update(lambda state: state.update(pending_departure={"destination_scene_id": "hall"}))
    blocked = json.loads(execute_function("end_game", {"ending_id": "truth_and_seal"}, context=context))
    assert blocked["error"] == "ending_work_unresolved"
    store.update(lambda state: state.pop("pending_departure"))
    first = json.loads(execute_function("end_game", {"ending_id": "truth_and_seal"}, context=context))
    before = store.load()
    second = json.loads(execute_function("end_game", {"ending_id": "truth_and_seal"}, context=context))
    assert first["game_over"] and not first["already_ended"]
    assert second["already_ended"]
    assert store.load() == before
    assert before["game_over"]["source_token"]


def test_failed_turn_never_publishes_ending_or_rewards(tmp_path, monkeypatch):
    import pytest

    from src.app.agent_graph import _handle_end_game
    from tests.test_turn_journal import TurnJournalTests

    engine = TurnJournalTests().make_engine(tmp_path)
    ended = []
    engine.cb.on_game_over = lambda *args: ended.append(args)

    def story(*args, **kwargs):
        engine.context.world_store.update(lambda state: state.update(game_over={"id": "end"}))
        _handle_end_game(engine, json.dumps({"game_over": True, "ending_type": "good", "title": "结束", "summary": "结束。"}))
        return "调查结束，所有人离开了。", []

    engine._stream_llm = story
    original = engine.turn_journal.complete
    monkeypatch.setattr(engine.turn_journal, "complete", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        engine.handle_action("留在原地观察")
    assert not ended
    assert not engine.context.world_store.load().get("game_over")
    assert not getattr(engine, "_pending_game_over", None)
    monkeypatch.setattr(engine.turn_journal, "complete", original)
    engine.handle_action("留在原地观察")
    assert len(ended) == 1
