"""H2 私有 context checkpoint 元数据 plumbing 的定向测试。

覆盖：
- ``ContextCheckpoint`` 严格校验 / roundtrip / 不可变 / merge / public copy；
- manual save 私有 roundtrip（``load_game_artifacts``）与公开列表隐藏
  （``list_saves`` / ``list_tree_saves`` / legacy import）；
- file ``TurnJournal.complete`` 与 ``DatabaseTurnJournal.complete`` 写入
  ``record["context"]``；DB 侧同时合并进 auto SaveSlot metadata；
- recovery / diagnostic / public_history / list_completed 不暴露 context；
- 非法 checkpoint 写前失败；无 checkpoint 完全兼容。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ai.context.context_checkpoint import ContextCheckpoint, public_copy, resolve_checkpoint
from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import (
    Base,
    SaveSlot,
    Turn,
    World,
    get_engine,
    session_scope,
)
from src.storage.database_store import DatabaseWorldStore
from src.storage.database_turn_journal import DatabaseTurnJournal
from src.storage.persistence import list_saves, load_game, load_game_artifacts, save_game
from src.storage.turn_journal import TurnJournal
from src.storage.world_branches import WorldBranchService

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _checkpoint(**overrides: object) -> ContextCheckpoint:
    values: dict[str, object] = {
        "session_id": "ctx-session",
        "session_epoch": 1,
        "sequence": 0,
        "surface_digest": DIGEST_A,
        "source_turn_id": None,
    }
    values.update(overrides)
    return ContextCheckpoint(**values)  # type: ignore[arg-type]


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def _seed_world(url: str, world_id: str) -> DatabaseWorldStore:
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="module-a"))
    store = DatabaseWorldStore(url, world_id, Path("/unused") / world_id)
    store.initialize({"schema_version": 0, "revision": 0, "pc": {"hp": 10}})
    return store


def _runtime(tmp_path: Path, world_id: str) -> RuntimeContext:
    return RuntimeContext.create(
        world_id,
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# ContextCheckpoint 单元
# ---------------------------------------------------------------------------


def test_checkpoint_roundtrip_and_immutability() -> None:
    cp = _checkpoint(source_turn_id="turn_x")
    dumped = cp.to_dict()
    assert dumped == {
        "schema_version": 1,
        "session_id": "ctx-session",
        "session_epoch": 1,
        "sequence": 0,
        "source_turn_id": "turn_x",
        "surface_digest": DIGEST_A,
    }
    assert ContextCheckpoint.from_mapping(dumped) == cp
    assert ContextCheckpoint.from_mapping(_checkpoint().to_dict()).source_turn_id is None
    with pytest.raises(AttributeError):
        cp.session_id = "其他"  # type: ignore[misc]  # frozen：不可变


def test_checkpoint_strict_validation() -> None:
    base = _checkpoint().to_dict()
    bads: list[dict[str, object]] = [
        {**base, "session_id": ""},
        {**base, "session_id": "   "},
        {**base, "session_id": " session"},
        {**base, "session_id": 123},
        {**base, "session_epoch": 0},
        {**base, "session_epoch": -1},
        {**base, "session_epoch": "1"},
        {**base, "session_epoch": True},
        {**base, "sequence": -1},
        {**base, "sequence": True},
        {**base, "surface_digest": "a" * 63},
        {**base, "surface_digest": "A" + "a" * 63},
        {**base, "surface_digest": "g" + "a" * 63},
        {**base, "surface_digest": 123},
        {**base, "unknown_field": "x"},
        {**base, "schema_version": 2},
        {**base, "schema_version": True},
        {**base, "source_turn_id": 123},
        {**base, "source_turn_id": " turn_x"},
    ]
    for bad in bads:
        with pytest.raises(ValueError):
            ContextCheckpoint.from_mapping(bad)
    with pytest.raises(TypeError):
        ContextCheckpoint.from_mapping(["not", "a", "mapping"])
    # 空字符串 source_turn_id 规范化为 None，roundtrip 稳定
    normalized = ContextCheckpoint.from_mapping({**base, "source_turn_id": ""})
    assert normalized.source_turn_id is None
    assert ContextCheckpoint.from_mapping(normalized.to_dict()) == normalized
    # Direct construction cannot bypass the same validation contract.
    with pytest.raises(ValueError):
        _checkpoint(session_epoch=0)
    with pytest.raises(ValueError):
        _checkpoint(surface_digest="not-a-digest")


def test_merge_into_and_public_copy() -> None:
    cp = _checkpoint()
    metadata = {"created_at": "2026-08-11T00:00:00", "label": "存档"}
    merged = cp.merge_into(metadata)
    assert merged["context"] == cp.to_dict()
    assert "context" not in metadata  # 不修改原 dict
    assert merged["created_at"] == metadata["created_at"]
    assert public_copy(merged) == {"created_at": "2026-08-11T00:00:00", "label": "存档"}
    assert public_copy({"no": "context"}) == {"no": "context"}
    assert resolve_checkpoint(cp) is cp
    assert resolve_checkpoint(cp.to_dict()) == cp
    assert resolve_checkpoint(None) is None
    with pytest.raises(ValueError):
        resolve_checkpoint({**cp.to_dict(), "session_epoch": 0})


# ---------------------------------------------------------------------------
# manual save：私有 roundtrip / 公开列表隐藏 / legacy 兼容
# ---------------------------------------------------------------------------


def test_manual_save_private_roundtrip_and_public_listing(tmp_path: Path) -> None:
    context = _runtime(tmp_path, "cp-world")
    checkpoint = _checkpoint(
        session_id="ctx-session-9",
        session_epoch=3,
        sequence=42,
        surface_digest=DIGEST_B,
        source_turn_id="turn_x",
    )
    save_game(
        [{"role": "user", "content": "你好"}],
        "slot_001",
        context=context,
        checkpoint=checkpoint,
    )
    # 私有 roundtrip：内部读取保留 context
    messages, _snapshot, metadata = load_game_artifacts("slot_001", context=context)
    assert messages == [{"role": "user", "content": "你好"}]
    assert ContextCheckpoint.from_mapping(metadata["context"]) == checkpoint
    # load_game 返回形状保持不变（2 元组，不泄露 context）
    loaded = load_game("slot_001", context=context)
    assert isinstance(loaded, tuple) and len(loaded) == 2
    assert loaded[0] == messages
    # 公开列表隐藏 context
    entries = list_saves(context=context)
    assert len(entries) == 1
    assert all("context" not in entry for entry in entries)


def test_legacy_import_roundtrips_private_context(tmp_path: Path) -> None:
    context = _runtime(tmp_path, "cp-legacy")
    checkpoint = _checkpoint(session_id="legacy-session", session_epoch=1, sequence=0)
    slot_dir = context.saves_dir / "slot_001"
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "messages.json").write_text(
        json.dumps([{"role": "user", "content": "旧档"}]),
        encoding="utf-8",
    )
    (slot_dir / "snapshot.json").write_text(
        json.dumps({"revision": 1}),
        encoding="utf-8",
    )
    (slot_dir / "meta.json").write_text(
        json.dumps(checkpoint.merge_into({"created_at": "2026-08-11T00:00:00"})),
        encoding="utf-8",
    )
    # 数据库是读取权威：触发 on-demand legacy import
    messages, _snapshot, metadata = load_game_artifacts("slot_001", context=context)
    assert messages == [{"role": "user", "content": "旧档"}]
    assert ContextCheckpoint.from_mapping(metadata["context"]) == checkpoint
    # 公开列表同样剥离
    assert all("context" not in entry for entry in list_saves(context=context))


def test_list_tree_saves_strips_private_context(tmp_path: Path) -> None:
    context = _runtime(tmp_path, "cp-tree")
    save_game(
        [{"role": "user", "content": "你好"}],
        "slot_001",
        context=context,
        checkpoint=_checkpoint(),
    )
    service = WorldBranchService(PROJECT_ROOT, tmp_path)
    saves = service.list_tree_saves("mansion_of_madness", active_world_id="cp-tree")
    assert saves
    assert all("context" not in entry for entry in saves)


# ---------------------------------------------------------------------------
# file TurnJournal.complete
# ---------------------------------------------------------------------------


def _file_journal(tmp_path: Path, world_id: str = "file-world") -> TurnJournal:
    return TurnJournal(
        tmp_path / "worlds" / world_id,
        world_id=world_id,
        module_name="file-module",
    )


def test_file_journal_complete_writes_context_without_auto_save(tmp_path: Path) -> None:
    journal = _file_journal(tmp_path)
    turn_id = journal.begin(kind="action", player_input="检查")
    checkpoint = _checkpoint(source_turn_id=turn_id)
    record = journal.complete(
        turn_id,
        messages=[{"role": "assistant", "content": "你检查了。"}],
        world_state={"revision": 1},
        narrative="你检查了。",
        choices=[],
        checkpoint=checkpoint,
    )
    assert record["context"] == checkpoint.to_dict()
    stored = journal.read(turn_id)
    assert ContextCheckpoint.from_mapping(stored["context"]) == checkpoint
    # recovery / diagnostic / public_history / list_completed 不暴露 context
    status = journal.recovery_status(turn_id)
    for value in status.values():
        assert "context" not in (value or {})
    assert "context" not in journal.diagnostic_report(turn_id)
    assert all("context" not in entry for entry in journal.public_history(turn_id))
    assert all("context" not in entry for entry in journal.list_completed())


def test_file_journal_rejects_invalid_checkpoint_before_write(tmp_path: Path) -> None:
    journal = _file_journal(tmp_path)
    turn_id = journal.begin(kind="action", player_input="p")
    with pytest.raises(ValueError):
        journal.complete(
            turn_id,
            messages=[{"role": "assistant", "content": "x"}],
            world_state={"revision": 1},
            narrative="x",
            choices=[],
            checkpoint={"session_id": "", "session_epoch": 1, "sequence": 0, "surface_digest": DIGEST_A},
        )
    # 写前失败：回合仍是 active，record 未写 context
    index = json.loads(journal.index_path.read_text(encoding="utf-8"))
    assert index["active_turn_id"] == turn_id
    record = journal.read(turn_id)
    assert record["status"] == "active"
    assert "context" not in record


def test_file_journal_without_checkpoint_stays_compatible(tmp_path: Path) -> None:
    journal = _file_journal(tmp_path)
    turn_id = journal.begin(kind="action", player_input="p")
    record = journal.complete(
        turn_id,
        messages=[{"role": "assistant", "content": "你检查了。"}],
        world_state={"revision": 1},
        narrative="你检查了。",
        choices=[],
    )
    assert "context" not in record
    messages, snapshot = journal.load_artifacts(turn_id)
    assert messages == [{"role": "assistant", "content": "你检查了。"}]
    assert snapshot == {"revision": 1}


# ---------------------------------------------------------------------------
# DatabaseTurnJournal.complete：Turn + auto SaveSlot 一致
# ---------------------------------------------------------------------------


def test_db_complete_turn_and_save_slot_share_checkpoint(tmp_path: Path) -> None:
    world_id = "db-cp-world"
    url = _sqlite_url(tmp_path)
    _seed_world(url, world_id)
    with patch.dict(
        os.environ,
        {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"},
    ):
        journal = DatabaseTurnJournal(
            tmp_path / "worlds" / world_id,
            world_id=world_id,
            module_name="module-a",
        )
        turn_id = journal.begin(kind="action", player_input="检查")
        checkpoint = _checkpoint(
            session_id="db-session",
            session_epoch=2,
            sequence=7,
            surface_digest=DIGEST_B,
            source_turn_id=turn_id,
        )
        record = journal.complete(
            turn_id,
            messages=[{"role": "assistant", "content": "你检查了。"}],
            world_state={"revision": 1, "pc": {"hp": 8}},
            narrative="你检查了。",
            choices=[],
            checkpoint=checkpoint,
        )
        assert record["context"] == checkpoint.to_dict()
        with session_scope(url) as session:
            turn = session.query(Turn).filter_by(world_id=world_id).one()
            save = (
                session.query(SaveSlot)
                .filter_by(world_id=world_id, slot_key="slot_000")
                .one()
            )
            assert turn.record["context"] == checkpoint.to_dict()
            assert save.metadata_json["context"] == checkpoint.to_dict()
            assert save.metadata_json["context"] == turn.record["context"]
        # recovery / diagnostic / public_history / list_completed 不暴露 context
        status = journal.recovery_status(turn_id)
        for value in status.values():
            assert "context" not in (value or {})
        assert "context" not in journal.diagnostic_report(turn_id)
        assert all("context" not in entry for entry in journal.public_history(turn_id))
        assert all("context" not in entry for entry in journal.list_completed())


def test_db_complete_rejects_invalid_checkpoint_before_write(tmp_path: Path) -> None:
    world_id = "db-cp-bad"
    url = _sqlite_url(tmp_path)
    _seed_world(url, world_id)
    with patch.dict(
        os.environ,
        {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"},
    ):
        journal = DatabaseTurnJournal(
            tmp_path / "worlds" / world_id,
            world_id=world_id,
            module_name="module-a",
        )
        turn_id = journal.begin(kind="action", player_input="p")
        with pytest.raises(ValueError):
            journal.complete(
                turn_id,
                messages=[{"role": "assistant", "content": "x"}],
                world_state={"revision": 1},
                narrative="x",
                choices=[],
                checkpoint={
                    "session_id": "s",
                    "session_epoch": 0,
                    "sequence": 0,
                    "surface_digest": DIGEST_A,
                },
            )
        # 写前失败：turn 仍是 active，auto SaveSlot 未创建
        with session_scope(url) as session:
            turn = session.query(Turn).filter_by(world_id=world_id).one()
            assert turn.status == "active"
            assert "context" not in (turn.record or {})
            assert session.query(SaveSlot).filter_by(world_id=world_id).count() == 0


def test_db_complete_without_checkpoint_stays_compatible(tmp_path: Path) -> None:
    world_id = "db-cp-none"
    url = _sqlite_url(tmp_path)
    _seed_world(url, world_id)
    with patch.dict(
        os.environ,
        {"TRPG_DATABASE_URL": url, "TRPG_WRITE_COMPAT_EXPORTS": "0"},
    ):
        journal = DatabaseTurnJournal(
            tmp_path / "worlds" / world_id,
            world_id=world_id,
            module_name="module-a",
        )
        turn_id = journal.begin(kind="action", player_input="p")
        record = journal.complete(
            turn_id,
            messages=[{"role": "assistant", "content": "你检查了。"}],
            world_state={"revision": 1},
            narrative="你检查了。",
            choices=[],
        )
        assert "context" not in record
        with session_scope(url) as session:
            save = (
                session.query(SaveSlot)
                .filter_by(world_id=world_id, slot_key="slot_000")
                .one()
            )
            assert "context" not in (save.metadata_json or {})
        assert "context" not in journal.recovery_status(turn_id)["latest_completed"]


# ---------------------------------------------------------------------------
# save_game：非法 checkpoint 写前失败 / 无 checkpoint 兼容
# ---------------------------------------------------------------------------


def test_save_game_rejects_invalid_checkpoint_before_write(tmp_path: Path) -> None:
    context = _runtime(tmp_path, "cp-save-bad")
    with pytest.raises(ValueError):
        save_game(
            [{"role": "user", "content": "你好"}],
            "slot_001",
            context=context,
            checkpoint={"session_id": "s", "session_epoch": 1, "sequence": -1, "surface_digest": DIGEST_A},
        )
    # 没有任何存档被写入
    assert list_saves(context=context) == []


def test_save_game_without_checkpoint_stays_compatible(tmp_path: Path) -> None:
    context = _runtime(tmp_path, "cp-save-none")
    save_game([{"role": "user", "content": "你好"}], "slot_001", context=context)
    _messages, _snapshot, metadata = load_game_artifacts("slot_001", context=context)
    assert "context" not in metadata
    assert load_game("slot_001", context=context)[0] == [{"role": "user", "content": "你好"}]
