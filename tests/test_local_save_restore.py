import copy
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import SaveSlot, new_id, session_scope
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
        ws.send_json({"type": "save_load", "slot_id": "slot_001"})
        loaded, seen = receive_until(ws, "loaded")
        assert loaded["restore_only"] is True
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


def test_journal_save_uses_saved_turn_boundary_instead_of_future_history(tmp_path):
    from src.app.engine import GameEngine
    from src.web.local_save_restore import saved_public_history

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
    restored = saved_public_history(engine, "slot_002")
    assert [turn["turn_id"] for turn in restored] == [first]
    assert "存档之后" not in json.dumps(restored, ensure_ascii=False)
