import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from src.app.player_notes_handlers import register_player_notes_handlers
from src.storage.player_notes import PlayerNotesStore


def endpoints(tmp_path):
    handlers = {}
    frames = []

    def handler(kind):
        def register(fn):
            handlers[kind] = fn
            return fn

        return register

    async def send(payload):
        frames.append(payload)

    register_player_notes_handlers(
        SimpleNamespace(handler=handler),
        SimpleNamespace(context=SimpleNamespace(world_id="current-world", world_dir=tmp_path)),
        SimpleNamespace(send=send),
    )
    return handlers, frames


def test_local_notes_stale_world_never_writes(tmp_path):
    handlers, frames = endpoints(tmp_path)
    asyncio.run(
        handlers["player_notes_update"](
            {"world_id": "old-world", "request_id": "old:save", "text": "不得跨世界写入"}
        )
    )
    assert PlayerNotesStore(tmp_path).load()["revision"] == 0
    assert frames[0]["type"] == "player_notes_error"
    assert frames[0]["world_id"] == "current-world"
    assert frames[0]["request_id"] == "old:save"


def test_local_notes_save_read_and_conflict_echo_actual_scope(tmp_path):
    handlers, frames = endpoints(tmp_path)
    asyncio.run(
        handlers["player_notes_update"](
            {"world_id": "current-world", "request_id": "save:1", "revision": 0, "text": "私人内容"}
        )
    )
    asyncio.run(handlers["player_notes_get"]({"request_id": "read:1"}))
    asyncio.run(
        handlers["player_notes_update"](
            {"request_id": "save:2", "revision": 0, "text": "旧版本覆盖"}
        )
    )
    assert [(f["type"], f["world_id"], f["request_id"]) for f in frames] == [
        ("player_notes", "current-world", "save:1"),
        ("player_notes", "current-world", "read:1"),
        ("player_notes_conflict", "current-world", "save:2"),
    ]
    assert frames[0]["saved"] is True
    assert all(f["text"] == "私人内容" and f["revision"] == 1 for f in frames)


def test_local_notes_read_error_is_correlated_and_sanitized(tmp_path):
    handlers, frames = endpoints(tmp_path)
    with patch.object(PlayerNotesStore, "load", side_effect=OSError("private-path-and-content")):
        asyncio.run(handlers["player_notes_get"]({"request_id": "read:failed"}))
    assert frames == [
        {
            "type": "player_notes_error",
            "message": "玩家笔记暂时不可用，请稍后重试",
            "world_id": "current-world",
            "request_id": "read:failed",
        }
    ]
