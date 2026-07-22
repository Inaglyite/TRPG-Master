from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event

from src.database import Base, SaveSlot, Turn, World, get_engine, session_scope
from src.database_store import DatabaseWorldStore
from src.database_turn_journal import DatabaseTurnJournal
from src.runtime import RuntimeContext
from src.tools import execute_function
from src.turn_mutations import TurnMutationLedger
from src.turn_performance import TurnPerformance
from src.turn_reconciler import turn_needs_model_audit

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _store(tmp_path: Path) -> DatabaseWorldStore:
    url = f"sqlite:///{tmp_path / 'perf.db'}"
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id="perf-world", module_name="mansion_of_madness"))
    store = DatabaseWorldStore(url, "perf-world", tmp_path / "worlds/perf-world")
    store.initialize({
        "schema_version": 0,
        "revision": 0,
        "pc": {"hp": 10, "max_hp": 10, "skills": {"spot_hidden": 60}},
        "npcs": [],
        "clues_found": {},
    })
    return store


def test_turn_cache_reuses_reads_and_refreshes_after_mutation(tmp_path: Path):
    store = _store(tmp_path)
    selects = 0

    def count_select(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(get_engine(store.database_url), "before_cursor_execute", count_select)
    try:
        with store.turn_cache():
            assert store.load()["pc"]["hp"] == 10
            assert store.load()["pc"]["hp"] == 10
            first = selects
            store.update(lambda state: state["pc"].update({"hp": 8}))
            assert store.load()["pc"]["hp"] == 8
            assert selects == first + 1  # update locks once; post-update load is cached
        assert store.load()["pc"]["hp"] == 8
        assert selects == first + 2
    finally:
        event.remove(get_engine(store.database_url), "before_cursor_execute", count_select)


def test_common_tools_never_spawn_python_subprocess(tmp_path: Path):
    context = RuntimeContext.create(
        "perf-tools", "mansion_of_madness", project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    with patch.object(subprocess, "run", side_effect=AssertionError("spawned")):
        rolled = json.loads(execute_function("dice_roll", {"spec": "2d6+1"}, context=context))
        checked = json.loads(execute_function(
            "skill_check", {"skill": "spot_hidden"}, context=context
        ))
        state = json.loads(execute_function("state_get", {"path": "pc.hp"}, context=context))
    assert rolled["count"] == 2
    assert checked["skill"] == "spot_hidden"
    assert isinstance(state, int)


def test_mutation_ledger_skips_redundant_model_audit():
    ledger = TurnMutationLedger()
    ledger.record_tool("state_set", {"path": "flags.open"}, '{"ok":true}')
    assert ledger.has_authoritative_mutation
    assert not turn_needs_model_audit(
        [], narrative="调查员取得钥匙。", has_authoritative_mutation=True
    )


def test_turn_performance_records_spans_counters_and_first_visible():
    perf = TurnPerformance()
    with perf.span("prepare"):
        sum(range(100))
    perf.increment("model_call_count")
    perf.mark_first_visible()
    snapshot = perf.snapshot()
    assert snapshot["phases_ms"]["prepare"] >= 0
    assert snapshot["counters"]["model_call_count"] == 1
    assert snapshot["first_visible_ms"] is not None


def test_completed_turn_and_auto_save_share_one_snapshot(tmp_path: Path):
    store = _store(tmp_path)
    with patch.dict("os.environ", {
        "TRPG_DATABASE_URL": store.database_url,
        "TRPG_WRITE_COMPAT_EXPORTS": "0",
    }):
        journal = DatabaseTurnJournal(
            tmp_path / "worlds/perf-world",
            world_id="perf-world",
            module_name="mansion_of_madness",
        )
        turn_id = journal.begin(kind="action", player_input="观察")
        journal.complete(
            turn_id,
            messages=[{"role": "assistant", "content": "你观察四周。"}],
            world_state=store.load(),
            narrative="你观察四周。",
            choices=[],
            diagnostics={"performance": {"phases_ms": {}}},
        )
    with session_scope(store.database_url) as session:
        turn = session.query(Turn).filter_by(world_id="perf-world", id=turn_id).one()
        auto = session.query(SaveSlot).filter_by(
            world_id="perf-world", slot_key="slot_000"
        ).one()
        assert auto.snapshot_id == turn.snapshot_id
        assert turn.record["diagnostics"]["performance"]["phases_ms"]["journal_commit"] >= 0
