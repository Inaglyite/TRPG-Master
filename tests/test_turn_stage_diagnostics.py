import json
from types import SimpleNamespace as NS

import pytest

from src.gameplay.turn_diagnostics import failure_diagnostics, record_stage
from src.storage.database_turn_journal import DatabaseTurnJournal
from src.storage.turn_journal import TurnJournal, TurnJournalError
from tests.test_database_persistence import seed_world, sqlite_url


@pytest.mark.parametrize("database", [False, True])
def test_failed_turn_retains_safe_stage_sources_without_becoming_history(tmp_path, database, monkeypatch):
    if database:
        url = sqlite_url(tmp_path)
        seed_world(url)
        monkeypatch.setenv("TRPG_DATABASE_URL", url)
        journal = DatabaseTurnJournal(tmp_path / "worlds" / "world-a", world_id="world-a", module_name="module-a")
    else:
        journal = TurnJournal(tmp_path / "world-a", world_id="world-a", module_name="module-a")
    turn_id = journal.begin(kind="action", player_input="继续研究")
    engine = NS(context=NS(world_id="world-a"), active_turn_id=turn_id,
                _resolution_session=NS(id="res-1"), _turn_request_id="req-1", _turn_diagnostics=[])
    record_stage(engine, "dialogue_validation", "rejected", code="speaker_departed", speaker_id="wang", source_turn_id="depart-turn", raw_prompt="PRIVATE_KEY")
    record_stage(engine, "turn_commit", "not_committed", code="failed")
    journal.finish_incomplete(turn_id, status="failed", diagnostics=failure_diagnostics(engine))
    report = journal.diagnostic_report(turn_id)
    assert report["commit_status"] == "not_committed"
    assert report["stages"][0]["speaker_id"] == "wang"
    assert report["stages"][0]["source_turn_id"] == "depart-turn"
    assert report["stages"][0]["request_id"] == "req-1"
    assert "PRIVATE_KEY" not in json.dumps(report)
    assert journal.list_completed() == []
    with pytest.raises(TurnJournalError):
        journal.load_artifacts(turn_id)


def test_stage_trace_is_bounded_and_preserves_chinese_world_identity():
    engine = NS(context=NS(world_id="local-猩红文档"), active_turn_id="turn-1")
    for _ in range(100):
        record_stage(engine, "outcome", "staged", code="safe", receipt_id="private\nsecret")
    assert len(engine._turn_stage_trace) == 64
    assert engine._turn_stage_trace[-1]["world_id"] == "local-猩红文档"
    assert "receipt_id" not in engine._turn_stage_trace[-1]
