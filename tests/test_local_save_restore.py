import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import SaveSlot, Snapshot, new_id, session_scope
from src.storage.database_turn_journal import DatabaseTurnJournal
from src.storage.persistence import save_game
from src.storage.world_branches import WorldBranchService


def receive_until(ws, kind):
    seen = []
    for _ in range(30):
        payload = ws.receive_json()
        seen.append(payload)
        if payload.get("type") == kind:
            return payload, seen
    raise AssertionError(f"missing {kind}: {[p['type'] for p in seen]}")


def test_local_slot_restores_state_and_dialogue_without_model_or_resume_turn(tmp_path):
    import server

    context = RuntimeContext.local("mansion_of_madness", project_root=PROJECT_ROOT, runtime_root=tmp_path)
    state = context.world_store.load()
    state["pc"]["hp"] = 7
    context.world_store.restore(state)
    messages = [
        {"role": "system", "content": "PRIVATE_SYSTEM"},
        {"role": "user", "content": "[引擎控制指令｜非玩家发言]PRIVATE_CONTROL"},
        {"role": "assistant", "content": "PRIVATE_DRAFT", "tool_calls": [{"id": "t", "type": "function", "function": {"name": "private", "arguments": "{}"}}]},
        {"role": "tool", "content": "PRIVATE_TOOL", "tool_call_id": "t"},
        {"role": "user", "content": "[玩家行动] 检查大门\n\n[引擎权威状态｜仅供守秘人，不得复述]PRIVATE_STATE"},
        {"role": "assistant", "content": "存档中的大门仍然紧闭。\n\n**你可以——**\n1. 敲门\n2. [自由行动] 你决定做什么？"},
    ]
    save_game(messages, "slot_001", context=context)
    state = copy.deepcopy(state)
    state["pc"]["hp"] = 1
    context.world_store.restore(state)

    with (
        patch("src.app.engine.API_KEY", "offline-test"),
        patch.object(server, "RUNTIME_ROOT", tmp_path),
        patch.object(server, "DATABASE_URL", context.database_url),
        patch.object(server, "WORLD_BRANCHES", WorldBranchService(PROJECT_ROOT, tmp_path)),
        patch.object(server.RuntimeContext, "local", return_value=context),
        patch.object(server.GameEngine, "handle_action", side_effect=AssertionError("restore must not generate")) as model_turn,
        TestClient(server.app) as client,
        client.websocket_connect("/ws") as ws,
    ):
        receive_until(ws, "save_list")
        ws.send_json({"type": "save_load", "slot_id": "slot_001", "request_id": "save-test-1"})
        loaded, seen = receive_until(ws, "loaded")
        assert loaded["request_id"] == "save-test-1"
        assert all(value >= 0 for value in loaded["timings"].values())
        assert [p["phase"] for p in seen if p["type"] == "save_load_progress"] == ["restoring", "history", "character"]
        ws.send_json({"type": "save_load_timing", "request_id": "save-test-1", "total_ms": 100, "response_ms": 60, "presentation_ms": 40})
        assert loaded["restore_only"] is True
        assert loaded["history_source"] == "saved_messages"
        assert len(loaded["history"]) == 1
        assert loaded["history"][0]["player_input"] == "检查大门"
        assert loaded["history"][0]["choices"][0]["label"] == "敲门"
        assert "PRIVATE" not in json.dumps(loaded)
        assert not any(p["type"] == "gm_turn_start" for p in seen)
        assert context.world_store.load()["pc"]["hp"] == 7
        ws.send_json({"type": "state"})
        current, _ = receive_until(ws, "state_data")
        assert json.loads(current["data"])["hp"] == 7
        ws.send_json({"type": "save_load", "slot_id": "slot_999"})
        error, _ = receive_until(ws, "error")
        assert error["message"] == "未找到存档。"
        ws.send_json({"type": "save_load", "slot_id": "slot_001"})
        assert receive_until(ws, "loaded")[0]["ok"] is True
        ws.send_json({"type": "switch_module", "module": "mansion_of_madness"})
        assert receive_until(ws, "module_list")[0]["active"] == "mansion_of_madness"
        model_turn.assert_not_called()


def test_timing_labels_reject_free_text_and_unbounded_data():
    from src.web.local_save_restore import timing_request_id

    assert timing_request_id("save-123-abc") == "save-123-abc"
    assert timing_request_id("save-\nPRIVATE_CONTEXT") == ""
    assert timing_request_id("save-" + "x" * 65) == ""
    assert timing_request_id({"request_id": "save-example"}) == ""


def test_restore_exception_finishes_waiting_without_exposing_private_error():
    from src.web.local_save_restore import restore_local_save

    outbound = SimpleNamespace(send=AsyncMock())
    release = Mock()
    engine = SimpleNamespace(load=Mock(side_effect=RuntimeError("PRIVATE_CONTEXT")))
    asyncio.run(restore_local_save(
        engine, outbound, "slot_001", request_id="save-error",
        reserve_turn=AsyncMock(return_value=True), release_turn=release,
        send_character_state=AsyncMock(),
    ))
    release.assert_called_once()
    payloads = [call.args[0] for call in outbound.send.call_args_list]
    assert payloads[-1]["type"] == "error"
    assert payloads[-1]["terminal"] is True
    assert "PRIVATE_CONTEXT" not in json.dumps(payloads)


def test_journal_save_uses_saved_turn_boundary_instead_of_future_history(tmp_path):
    from src.app.engine import GameEngine
    from src.web.local_save_restore import _saved_history_projection, restore_local_save

    context = RuntimeContext.create("restore-boundary", "mansion_of_madness", project_root=PROJECT_ROOT, runtime_root=tmp_path)
    journal = DatabaseTurnJournal(context.world_dir, world_id=context.world_id, module_name=context.module_name)
    messages = [{"role": "system", "content": "PRIVATE"}]
    first = journal.begin(kind="action", player_input="检查大门")
    messages.append({"role": "assistant", "content": "存档中的门还关着。"})
    journal.complete(first, messages=messages, world_state=context.world_store.load(), narrative="存档中的门还关着。", choices=[])
    with session_scope(context.database_url) as session:
        auto = session.query(SaveSlot).filter_by(world_id=context.world_id, slot_key="slot_000").one()
        session.add(SaveSlot(id=new_id("save"), world_id=context.world_id, slot_key="slot_002", kind="manual", snapshot_id=auto.snapshot_id, messages=copy.deepcopy(auto.messages), metadata_json=copy.deepcopy(auto.metadata_json)))
    second = journal.begin(kind="action", player_input="打开门")
    messages.append({"role": "assistant", "content": "存档之后才发生的开门。"})
    journal.complete(second, messages=messages, world_state=context.world_store.load(), narrative="存档之后才发生的开门。", choices=[])
    with patch("src.app.engine.API_KEY", "offline-test"):
        engine = GameEngine(context)
    engine.prepare_session()
    engine.load("slot_002")
    restored, source = _saved_history_projection(engine, "slot_002")
    assert source == "turn_journal"
    assert [turn["turn_id"] for turn in restored] == [first]
    assert "存档之后" not in json.dumps(restored, ensure_ascii=False)
    outbound = SimpleNamespace(send=AsyncMock())
    asyncio.run(restore_local_save(
        engine, outbound, "slot_002", reserve_turn=AsyncMock(return_value=True),
        release_turn=Mock(), send_character_state=AsyncMock(),
    ))
    loaded = outbound.send.call_args.args[0]
    assert loaded["history_source"] == "turn_journal"
    assert [turn["turn_id"] for turn in loaded["history"]] == [first]


def test_manual_and_branch_saves_keep_exact_journal_boundary_including_legacy_slots(tmp_path):
    from src.ai.context.context_checkpoint import ContextCheckpoint
    from src.app.engine import GameEngine
    from src.web.local_save_restore import _saved_history_projection

    context = RuntimeContext.create("branch-restoration", "mansion_of_madness", project_root=PROJECT_ROOT, runtime_root=tmp_path)
    journal = DatabaseTurnJournal(context.world_dir, world_id=context.world_id, module_name=context.module_name)
    messages = [{"role": "system", "content": "PRIVATE"}, {"role": "assistant", "content": "最初的书房。"}]
    first = journal.begin(kind="action", player_input="查看书房")
    journal.complete(first, messages=messages, world_state=context.world_store.load(), narrative="最初的书房。", choices=[])
    checkpoint = ContextCheckpoint(session_id="private-session", session_epoch=1, sequence=0, surface_digest="0" * 64, source_turn_id=first)
    save_game(messages, "slot_002", context=context, checkpoint=checkpoint)
    with session_scope(context.database_url) as session:
        slot = session.query(SaveSlot).filter_by(world_id=context.world_id, slot_key="slot_002").one()
        snapshot = session.get(Snapshot, slot.snapshot_id)
        assert snapshot.source_turn_id == first
        snapshot.source_turn_id = None  # Manual saves produced before the fix.
    with patch("src.app.engine.API_KEY", "offline-test"):
        engine = GameEngine(context)
    engine.prepare_session()
    assert _saved_history_projection(engine, "slot_002")[1] == "turn_journal"

    branch = WorldBranchService(PROJECT_ROOT, tmp_path).create(context, journal, first)
    with patch("src.app.engine.API_KEY", "offline-test"):
        branch_engine = GameEngine(branch.context)
    branch_engine.prepare_session()
    branch_engine.load("slot_000")
    restored, source = _saved_history_projection(branch_engine, "slot_000")
    assert source == "turn_journal"
    assert [turn["turn_id"] for turn in restored] == [first]
    with session_scope(branch.context.database_url) as session:
        slot = session.query(SaveSlot).filter_by(world_id=branch.context.world_id, slot_key="slot_000").one()
        snapshot = session.get(Snapshot, slot.snapshot_id)
        assert snapshot.source_turn_id == first
        snapshot.source_turn_id = None  # Initial branch saves from older builds.
    assert _saved_history_projection(branch_engine, "slot_000")[1] == "turn_journal"
    # A later text-only save must not be mistaken for the branch's root turn.
    save_game(messages + [{"role": "assistant", "content": "之后未记入回合的文字。"}], "slot_003", context=branch.context)
    branch_engine.load("slot_003")
    assert _saved_history_projection(branch_engine, "slot_003")[1] == "saved_messages"
