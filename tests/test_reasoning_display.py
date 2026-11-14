"""Opt-in private display and independently controlled reasoning debug evidence."""

import asyncio
import copy
import json
import stat
from types import SimpleNamespace

import pytest
from test_provider_adapter import _chunk, _host, _stream
from test_structured_commands import make_structured_world

from src.ai.model.reasoning_display import ReasoningDisplay
from src.app import reasoning_debug
from src.app.reasoning_events import deliver_room_reasoning, private_event_sink
from src.multiplayer.room_runtime import GameRoom, RoomConnection, RoomDriverTransport, RoomEventHub
from src.storage.database import WorldMember, session_scope
from src.structured.reasoning_runtime import publish_reasoning


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr(reasoning_debug, "LOG_DIR", tmp_path)
    monkeypatch.setattr(reasoning_debug, "_HANDLER", None)
    monkeypatch.setenv("TRPG_REASONING_DEBUG", "1")
    monkeypatch.setenv("TRPG_DIAGNOSTICS", "1")
    monkeypatch.setenv("TRPG_DIAGNOSTIC_CONTENT", "1")
    yield
    if reasoning_debug._HANDLER:
        reasoning_debug._HANDLER.close()


def test_stream_reasoning_is_private_and_never_becomes_narrative(tmp_path):
    host = _host(
        [_chunk(reasoning="秘密推理"), _chunk(content="雨落在窗外。"), _chunk(finish="stop")]
    )
    events = []
    host.cb.on_private_event = events.append
    original = copy.deepcopy(host.messages)
    text, calls = _stream(host)
    assert text == "雨落在窗外。"
    assert calls == []
    assert [e["phase"] for e in events] == ["start", "delta", "end"]
    assert "".join(e.get("text", "") for e in events) == "秘密推理"
    assert host.messages == original
    row = json.loads((tmp_path / "reasoning-debug.jsonl").read_text())
    assert row["text"] == "秘密推理"
    assert row["request_id"] == events[0]["request_id"]
    assert row["world_id"] == "w-adapter"


def test_display_is_bounded_but_provider_passback_stays_exact(tmp_path):
    events = []
    host = SimpleNamespace(cb=SimpleNamespace(on_private_event=events.append))
    stream = ReasoningDisplay(host, "request-1", "test-model", "story")
    text = "🔍" * 70000
    stream.feed_text(text)
    stream.finish()
    stream.finish()
    assert stream.text == text
    assert sum(len(e.get("text", "")) for e in events) == 65536
    assert all(len(e.get("text", "").encode("utf-16-le")) // 2 <= 4096 for e in events)
    assert events[-1]["truncated"]
    assert len([e for e in events if e["phase"] == "end"]) == 1
    row = json.loads((tmp_path / "reasoning-debug.jsonl").read_text())
    assert row["truncated"] and len(row["text"]) == 65536


def test_debug_redacts_cross_chunk_credentials_and_can_be_disabled(tmp_path, monkeypatch):
    host = SimpleNamespace(cb=SimpleNamespace(on_private_event=lambda _: None))
    stream = ReasoningDisplay(host, "req", "test-model", "story")
    stream.feed_text("sk-")
    stream.feed_text("supersecret123")
    stream.finish("interrupted")
    path = tmp_path / "reasoning-debug.jsonl"
    assert "supersecret" not in path.read_text()
    assert json.loads(path.read_text())["status"] == "interrupted"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_text()
    monkeypatch.setenv("TRPG_REASONING_DEBUG", "0")
    other = ReasoningDisplay(host, "req2", "test-model", "story")
    other.feed_text("do not log")
    other.finish()
    assert path.read_text() == before


def test_no_reasoning_creates_no_panel_events_or_debug_file(tmp_path):
    events = []
    stream = ReasoningDisplay(
        SimpleNamespace(cb=SimpleNamespace(on_private_event=events.append)), "r", "m", "story"
    )
    stream.consume(_chunk(content="只有正文"))
    stream.finish()
    assert events == []
    assert not (tmp_path / "reasoning-debug.jsonl").exists()


def test_private_sink_bypasses_turn_journal():
    transient, recorded = [], []
    sink = private_event_sink(SimpleNamespace(emit=transient.append), recorded.append)
    event = {"type": "model_reasoning", "phase": "delta", "text": "隐藏思考"}
    sink(event)
    sink({"kind": "clue", "text": "旧私有事件"})
    assert transient == [event]
    assert recorded == [{"type": "private_event", "kind": "clue", "text": "旧私有事件"}]


def test_room_reasoning_is_keeper_only_and_absent_from_replay(tmp_path):
    async def run():
        context = make_structured_world(tmp_path)
        hub = RoomEventHub(context.world_id)
        room = GameRoom(
            world_id=context.world_id,
            engine=SimpleNamespace(context=context),
            hub=hub,
            owner_user_id="u-owner",
        )
        boxes = {}

        class Socket:
            def __init__(self, box):
                self.box = box

            async def send_json(self, payload):
                self.box.append(payload)

        for user, role in [("u-owner", "owner"), ("u-keeper", "player"), ("u-alice", "player")]:
            boxes[user] = []
            await hub.attach(RoomConnection(user, user, role, Socket(boxes[user])))
        event = {"type": "model_reasoning", "world_id": context.world_id, "text": "主持秘密"}
        await RoomDriverTransport(room).send_json(event)
        assert len(boxes["u-keeper"]) == 1
        assert boxes["u-owner"] == boxes["u-alice"] == []
        assert await hub.latest_event_id() == 0
        with session_scope(context.database_url) as session:
            member = (
                session.query(WorldMember)
                .filter_by(world_id=context.world_id, user_id="u-keeper")
                .one()
            )
            member.can_keeper = False
        await deliver_room_reasoning(room, event)
        assert len(boxes["u-keeper"]) == 1
        room.play_mode = "solo"
        await deliver_room_reasoning(room, event)
        assert len(boxes["u-owner"]) == 1
        assert await hub.latest_event_id() == 0

    asyncio.run(run())


def test_nonstreaming_agent_reasoning_uses_private_callback(tmp_path):
    async def run():
        frames = []

        async def deliver(frame):
            frames.append(frame)

        await publish_reasoning(
            "world-a", "model-a", "私有分析", deliver, trigger_request_id="player-request",
            status="interrupted",
        )
        assert [e["phase"] for e in frames] == ["start", "delta", "end"]
        assert all(e["world_id"] == "world-a" for e in frames)
        assert frames[-1]["status"] == "interrupted"
        row = json.loads((tmp_path / "reasoning-debug.jsonl").read_text())
        assert row["trigger_request_id"] == "player-request"

    asyncio.run(run())
