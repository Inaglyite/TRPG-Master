from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.multiplayer_messages import run_room_message_loop
from src.room_runtime import GameRoom, RoomDriverTransport, RoomEventHub


class _QueueSocket:
    """Feed prepared messages, then abort the loop like a disconnect."""

    def __init__(self, messages: list[dict], on_next=None):
        self._messages = list(messages)
        self._on_next = on_next
        self.sent: list[dict] = []

    async def receive_text(self):
        if not self._messages:
            raise RuntimeError("test complete")
        if self._on_next is not None:
            self._on_next()
        return json.dumps(self._messages.pop(0))

    async def send_json(self, payload):
        self.sent.append(dict(payload))


class _Driver:
    def __init__(self):
        self.submitted: list[dict] = []

    async def submit(self, payload):
        self.submitted.append(json.loads(payload))


class _Controller:
    """Minimal room controller; roster is mutable so the test can re-claim."""

    def __init__(self, roster: list[dict], playable_members: set[str]):
        self.deps = SimpleNamespace(database_url=lambda: "sqlite://")
        self.roster = roster
        self.playable_members = set(playable_members)

    def room_roster(self, _world_id: str):
        return (list(self.roster), set(self.playable_members))

    def set_room_status(self, room: GameRoom, status: str) -> None:
        room.status = status

    async def broadcast_room_state(self, room: GameRoom) -> None:
        pass


def test_next_start_after_settlement_uses_owner_as_actor():
    """成功结案后旧 actor 释放 claim 不再卡住下一次 start（最小回归）。

    结案前 current_actor_user_id 是上一局的 player。旧 actor 在 lobby 释放
    调查员 claim 后，未修复时 start 沿用 player 作为 actor_id 且无 claim，
    即使全员 ready/在线也会被 investigator_required 拒绝。修复后行动者重置
    为当前房主：第一次 start 只按正常门禁提示 player 缺 claim
    （room_not_ready），player 补选后第二次 start 正常提交且首位行动者是
    owner，room actor 不再为 None。
    """
    owner_claim = {
        "investigator_id": "inv-owner",
        "user_id": "owner",
        "character_ref": {"type": "inline", "data": {"name": "Owner"}},
    }
    player_claim = {
        "investigator_id": "inv-player",
        "user_id": "player",
        "character_ref": {"type": "inline", "data": {"name": "Player"}},
    }
    room = GameRoom(
        "world-settle-then-start",
        SimpleNamespace(),
        RoomEventHub("world-settle-then-start"),
        "owner",
        current_actor_user_id="player",
        status="playing",
        ready_users={"owner", "player"},
        connected_users={"owner": 1, "player": 1},
    )
    driver = _Driver()
    room.driver_transport = driver

    async def scenario():
        # 成功结案：行动者重置为当前房主，房间回到大厅，ready 清空。
        # case_settled 是房主控制行动，先预留控制锁才会走 terminal 分支。
        await room.reserve_control("owner", "settle-actor-1")
        await RoomDriverTransport(room).send_json(
            {
                "type": "case_settled",
                "ok": True,
                "ending_type": "good",
                "title": "封印重归寂静",
                "summary": "调查员阻止了仪式。",
            }
        )
        assert room.current_actor_user_id == "owner"
        assert room.status == "lobby"
        assert room.ready_users == set()

        # 下一局全员重新确认 ready（room_ready 协议的效果；ready_users 是
        # 纯内存态）。旧 actor player 在 lobby 释放了调查员 claim，roster
        # 里只剩 owner 的认领。
        room.set_ready("owner", True)
        room.set_ready("player", True)
        controller = _Controller([owner_claim], {"owner", "player"})
        calls = {"count": 0}

        def re_claim_player() -> None:
            # 第二次 start 前 player 重新认领调查员，满足全员 claim 门禁。
            calls["count"] += 1
            if calls["count"] == 2:
                controller.roster.append(player_claim)

        socket = _QueueSocket(
            [
                {"type": "start", "action_id": "settled-start-1"},
                {"type": "start", "action_id": "settled-start-2"},
            ],
            on_next=re_claim_player,
        )
        with (
            patch("src.multiplayer_messages.websocket_user", return_value=object()),
            patch("src.multiplayer_messages.authorize_world", return_value="owner"),
            patch("src.multiplayer_messages.reserve_room_action"),
        ):
            with pytest.raises(RuntimeError, match="test complete"):
                await run_room_message_loop(
                    controller,
                    socket,
                    room,
                    SimpleNamespace(id="owner"),
                    room.world_id,
                    "owner-tab",
                    "owner",
                )

        # 修复后第一次 start 走正常门禁：提示 player 缺 claim
        # （room_not_ready），而不是旧 actor 无 claim 导致的
        # investigator_required 死锁。
        not_ready = next(
            item for item in socket.sent if item["type"] == "room_action_rejected"
        )
        assert not_ready["code"] == "room_not_ready"
        assert not_ready["missing_claim_user_ids"] == ["player"]
        assert not_ready["missing_ready_user_ids"] == []
        assert not_ready["missing_online_user_ids"] == []
        assert all(
            item["code"] != "investigator_required"
            for item in socket.sent
            if item["type"] == "room_action_rejected"
        )

        # player 补选后第二次 start 正常提交，首位行动者即房主，
        # room actor 不为 None。
        start_message = next(item for item in driver.submitted if item["type"] == "start")
        assert start_message["_room_actor_user_id"] == "owner"
        assert room.current_actor_user_id == "owner"
        assert room.status == "starting"

    asyncio.run(scenario())
