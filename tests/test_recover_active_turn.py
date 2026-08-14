from __future__ import annotations

import copy
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.database import (
    AuditEvent,
    Base,
    Snapshot,
    Turn,
    World,
    WorldState,
    get_engine,
    session_scope,
)
from src.database_turn_journal import DatabaseTurnJournal
from src.turn_journal import TurnJournalError
from tools.recover_active_turn import main


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'trpg-master.db'}"


def _seed_world(url: str, world_id: str) -> None:
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="mansion_of_madness"))


def _make_remote_owner(url: str, world_id: str, turn_id: str) -> None:
    """Turn a freshly-created record into a legacy/remote fail-closed case."""
    with session_scope(url) as session:
        row = session.scalar(
            select(Turn).where(Turn.world_id == world_id, Turn.id == turn_id)
        )
        assert row is not None
        record = copy.deepcopy(row.record)
        record["owner_host"] = "other-host.example"
        record["owner_pid"] = 424242
        row.record = record


def test_force_recovery_requires_current_turn_and_owner_token_then_audits(tmp_path: Path):
    url = _database_url(tmp_path)
    world_id = "force-recovery-world"
    _seed_world(url, world_id)
    world_dir = tmp_path / "worlds" / world_id

    with patch.dict(
        os.environ,
        {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"},
    ):
        original = DatabaseTurnJournal(
            world_dir,
            world_id=world_id,
            module_name="mansion_of_madness",
            owner_token="remote-owner",
        )
        turn_id = original.begin(kind="action", player_input="检查书桌")
        _make_remote_owner(url, world_id, turn_id)

        operator = DatabaseTurnJournal(
            world_dir,
            world_id=world_id,
            module_name="mansion_of_madness",
            owner_token="new-owner",
        )
        candidates = operator.active_turn_recovery_candidates()
        assert len(candidates) == 1
        assert candidates[0]["turn_id"] == turn_id
        assert candidates[0]["status"] == "active"
        assert candidates[0]["kind"] == "action"
        assert candidates[0]["created_at"]
        assert candidates[0]["owner_token"] == "remote-owner"

        with pytest.raises(TurnJournalError, match="owner token 已变化"):
            operator.force_interrupt_active_turn(
                expected_turn_id=turn_id,
                expected_owner_token="stale-owner-token",
                reason="原服务已经停止",
                operator="test-operator",
            )
        assert operator.recovery_status(turn_id)["requested"]["status"] == "active"

        interrupted = operator.force_interrupt_active_turn(
            expected_turn_id=turn_id,
            expected_owner_token=candidates[0]["owner_token"],
            reason="原服务已经停止",
            operator="test-operator",
        )

    assert interrupted["status"] == "interrupted"
    assert interrupted["force_recovery"]["mode"] == "local_cli"
    with session_scope(url) as session:
        row = session.scalar(
            select(Turn).where(Turn.world_id == world_id, Turn.id == turn_id)
        )
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.world_id == world_id,
                AuditEvent.event_type == "turn_force_recovered",
            )
        )
    assert row is not None and row.status == "interrupted"
    assert audit is not None
    assert audit.details == {
        "turn_id": turn_id,
        "reason": "原服务已经停止",
        "operator": "test-operator",
        "mode": "local_cli",
        "owner_metadata_present": True,
    }


def test_force_recovery_can_fence_a_legacy_empty_owner_token(tmp_path: Path):
    url = _database_url(tmp_path)
    world_id = "legacy-empty-owner-world"
    _seed_world(url, world_id)
    world_dir = tmp_path / "worlds" / world_id

    with patch.dict(
        os.environ,
        {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"},
    ):
        original = DatabaseTurnJournal(
            world_dir,
            world_id=world_id,
            module_name="mansion_of_madness",
            owner_token="legacy-owner",
        )
        turn_id = original.begin(kind="opening", player_input=None)
        with session_scope(url) as session:
            row = session.scalar(
                select(Turn).where(Turn.world_id == world_id, Turn.id == turn_id)
            )
            assert row is not None
            record = copy.deepcopy(row.record)
            record.pop("owner_pid", None)
            record.pop("owner_host", None)
            record["owner_token"] = ""
            row.owner_token = ""
            row.record = record

        operator = DatabaseTurnJournal(
            world_dir,
            world_id=world_id,
            module_name="mansion_of_madness",
            owner_token="new-owner",
            recover_on_init=False,
        )
        candidate = operator.active_turn_recovery_candidates()[0]
        assert candidate["owner_token"] == ""
        interrupted = operator.force_interrupt_active_turn(
            expected_turn_id=turn_id,
            expected_owner_token="",
            reason="旧记录没有 owner token",
            operator="test-operator",
        )

    assert interrupted["status"] == "interrupted"


def test_forced_interrupt_fences_old_completion_before_snapshot_or_state_write(
    tmp_path: Path,
):
    url = _database_url(tmp_path)
    world_id = "force-fence-world"
    _seed_world(url, world_id)
    world_dir = tmp_path / "worlds" / world_id
    with session_scope(url) as session:
        session.add(
            WorldState(
                world_id=world_id,
                schema_version=1,
                revision=3,
                state={"schema_version": 1, "revision": 3},
            )
        )

    with patch.dict(
        os.environ,
        {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"},
    ):
        original = DatabaseTurnJournal(
            world_dir,
            world_id=world_id,
            module_name="mansion_of_madness",
            owner_token="old-owner",
        )
        turn_id = original.begin(kind="action", player_input="检查书桌")
        operator = DatabaseTurnJournal(
            world_dir,
            world_id=world_id,
            module_name="mansion_of_madness",
            owner_token="operator-owner",
            recover_on_init=False,
        )
        owner_token = operator.active_turn_recovery_candidates()[0]["owner_token"]
        operator.force_interrupt_active_turn(
            expected_turn_id=turn_id,
            expected_owner_token=owner_token,
            reason="维护窗口中确认旧 worker 已停止",
            operator="test-operator",
        )

        with patch.object(original, "_row", wraps=original._row) as row_reader:
            with pytest.raises(TurnJournalError, match="状态为 interrupted"):
                original.complete(
                    turn_id,
                    messages=[],
                    world_state={"schema_version": 1, "revision": 4},
                    narrative="旧 worker 不能提交。",
                    choices=[],
                    expected_world_revision=3,
                )
        assert row_reader.call_args.kwargs["for_update"] is True

        with patch.object(original, "_row", wraps=original._row) as row_reader:
            observed = original.finish_incomplete(turn_id, status="failed")
        assert observed["status"] == "interrupted"
        assert row_reader.call_args.kwargs["for_update"] is True

    with session_scope(url) as session:
        state = session.get(WorldState, world_id)
        snapshots = session.scalars(
            select(Snapshot).where(Snapshot.world_id == world_id)
        ).all()
    assert state is not None and state.revision == 3
    assert snapshots == []


def test_local_cli_is_read_only_until_explicit_fenced_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    url = _database_url(tmp_path)
    world_id = "force-recovery-cli-world"
    _seed_world(url, world_id)
    world_dir = tmp_path / "worlds" / world_id
    monkeypatch.setenv("TRPG_DATABASE_URL", url)
    monkeypatch.setenv("TRPG_WRITE_COMPAT_EXPORTS", "0")

    original = DatabaseTurnJournal(
        world_dir,
        world_id=world_id,
        module_name="mansion_of_madness",
        owner_token="remote-owner",
    )
    turn_id = original.begin(kind="opening", player_input=None)
    _make_remote_owner(url, world_id, turn_id)

    with (
        patch("src.database.initialize_database") as database_initialize,
        patch("src.runtime.initialize_database") as runtime_initialize,
        patch.object(Base.metadata, "create_all") as create_all,
    ):
        assert main(["--world-id", world_id, "--runtime-root", str(tmp_path)]) == 0
    database_initialize.assert_not_called()
    runtime_initialize.assert_not_called()
    create_all.assert_not_called()
    inspection = capsys.readouterr().out
    assert turn_id in inspection
    assert "owner_token=remote-owner" in inspection
    with session_scope(url) as session:
        assert session.scalar(select(Turn.status).where(Turn.id == turn_id)) == "active"

    assert main([
        "--world-id",
        world_id,
        "--runtime-root",
        str(tmp_path),
        "--expected-turn-id",
        turn_id,
        "--expected-owner-token",
        "remote-owner",
        "--reason",
        "维护者已经确认服务停止",
        "--operator",
        "test-cli",
        "--force",
        "--yes",
    ]) == 0
    assert "已中断回合" in capsys.readouterr().out
    with session_scope(url) as session:
        row = session.scalar(select(Turn).where(Turn.id == turn_id))
    assert row is not None and row.status == "interrupted"
