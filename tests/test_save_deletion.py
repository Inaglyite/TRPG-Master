"""存档删除与旧文件式存档一次性导入的回归测试。

复现并锁定的问题：删除存档只删数据库记录，兼容导出文件（saves/slot_xxx/）
残留在磁盘；list_saves 每次调用都把残留文件重新导入，导致存档"复活"，
且每次导入都新增一条无引用的 legacy_import 快照，数据库持续膨胀。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import PROJECT_ROOT
from src.database import SaveSlot, Snapshot, session_scope
from src.persistence import delete_save, list_saves, save_game
from src.runtime import RuntimeContext


def _context(runtime_root: Path, world_id: str) -> RuntimeContext:
    return RuntimeContext.create(
        world_id,
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=runtime_root,
    )


def _slot_keys(context: RuntimeContext) -> list[str]:
    with session_scope(context.database_url) as session:
        return [
            row.slot_key
            for row in session.query(SaveSlot)
            .filter_by(world_id=context.world_id)
            .all()
        ]


def _legacy_import_snapshot_count(context: RuntimeContext) -> int:
    with session_scope(context.database_url) as session:
        return (
            session.query(Snapshot)
            .filter_by(world_id=context.world_id, kind="legacy_import")
            .count()
        )


def _write_legacy_slot(context: RuntimeContext, slot_id: str) -> Path:
    slot_dir = context.saves_dir / slot_id
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "messages.json").write_text(
        json.dumps([{"role": "user", "content": "旧存档消息"}]),
        encoding="utf-8",
    )
    (slot_dir / "snapshot.json").write_text(
        json.dumps({"revision": 1}),
        encoding="utf-8",
    )
    (slot_dir / "meta.json").write_text(
        json.dumps({"created_at": "2026-08-11T16:00:00", "label": "旧存档"}),
        encoding="utf-8",
    )
    return slot_dir


def test_delete_save_removes_compat_exports_and_never_revives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRPG_WRITE_COMPAT_EXPORTS", "1")
    with tempfile.TemporaryDirectory() as temp_dir:
        context = _context(Path(temp_dir), "save-delete-compat")
        save_game(
            [{"role": "user", "content": "打个招呼"}],
            "slot_001",
            context=context,
        )
        slot_dir = context.saves_dir / "slot_001"
        assert (slot_dir / "messages.json").is_file()
        assert "slot_001" in _slot_keys(context)

        delete_save("slot_001", context=context)

        assert not slot_dir.exists()
        assert "slot_001" not in _slot_keys(context)
        # 删除后再列出存档：没有兼容文件可导入，存档不得复活。
        assert "slot_001" not in {
            save["id"] for save in list_saves(context=context)
        }


def test_list_saves_imports_legacy_slot_only_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        context = _context(Path(temp_dir), "save-legacy-import-once")
        _write_legacy_slot(context, "slot_002")

        first = list_saves(context=context)
        assert "slot_002" in {save["id"] for save in first}
        assert _legacy_import_snapshot_count(context) == 1

        second = list_saves(context=context)
        assert [save["id"] for save in second].count("slot_002") == 1
        # 重复列出不得重复导入：legacy_import 快照数保持不变。
        assert _legacy_import_snapshot_count(context) == 1


def test_delete_save_cleans_legacy_files_so_imported_save_stays_deleted() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        context = _context(Path(temp_dir), "save-legacy-delete")
        slot_dir = _write_legacy_slot(context, "slot_003")
        # 先经列表完成一次性迁移（数据库有记录、文件仍在）。
        assert "slot_003" in {save["id"] for save in list_saves(context=context)}

        delete_save("slot_003", context=context)

        assert not slot_dir.exists()
        assert "slot_003" not in {
            save["id"] for save in list_saves(context=context)
        }


def test_delete_save_never_reports_success_when_compat_cleanup_fails() -> None:
    """文件删不掉时保留数据库记录，避免下一次 list_saves 复活存档。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        context = _context(Path(temp_dir), "save-delete-cleanup-failure")
        save_game(
            [{"role": "user", "content": "不能静默丢失"}],
            "slot_001",
            context=context,
        )
        with patch("src.persistence.shutil.rmtree", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError, match="denied"):
                delete_save("slot_001", context=context)

        # 目录和数据库记录均保留，调用方会收到失败而非错误的“已删除”。
        assert (context.saves_dir / "slot_001").is_dir()
        assert "slot_001" in _slot_keys(context)
