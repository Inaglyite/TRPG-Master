"""结构化世界探测的对偶验证：世界缺失 / 数据库故障时不得放行任何操作。

背景：M1 之后房间连接循环会先按 world 元数据判定该世界是否 structured_v1
（`gateway.is_structured`、`structured_frame_gate_reason` → `room_world_modes`）。
`room_world_modes` 在世界行缺失时返回 legacy —— 这只是**模式判定**的 fail-soft，
绝不是访问权。本文件用对偶用例把这条边界钉死：

1. 世界不存在：结构化帧仍被拒（认证用户 `not_authorized`；本地操作者路径
   由命令服务以 `unknown_world` 拒绝），且不产生世界状态 / 事件 / 世界行。
2. 世界不存在：房间连接不取得访问权（`authorize_world` 403 → 4403 关闭），
   且不向引擎提交任何行动。
3. 数据库不可用：探测失败即结束，但不得有任何执行副作用（无提交、无 ack）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from src.auth.service import create_user
from src.multiplayer.messages import run_room_message_loop
from src.multiplayer.room_runtime import GameRoom, RoomEventHub
from src.storage.database import (
    Base,
    EventOutbox,
    GameCommand,
    PlayerRequest,
    World,
    WorldInvestigator,
    WorldMember,
    WorldState,
    get_engine,
    session_scope,
)
from src.structured.gateway import StructuredGateway
from src.structured.room_integration import room_world_modes, structured_frame_gate_reason

MISSING_WORLD = "world-that-does-not-exist"
# 三类 fail-closed 拒绝码：都没有「按 legacy 继续执行」这一分支。
FAIL_CLOSED_CODES = {"unknown_world", "not_authorized", "keeper_required"}


class _Collector:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def send(self, envelope: dict) -> None:
        self.received.append(envelope)


class _Socket:
    """喂预备消息，喂完即断连（与既有房间夹具同形）。"""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.sent: list[dict] = []
        self.close_codes: list[int] = []

    async def receive_text(self) -> str:
        if not self._messages:
            raise RuntimeError("test complete")
        return json.dumps(self._messages.pop(0))

    async def send_json(self, payload: dict) -> None:
        self.sent.append(dict(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_codes.append(code)


class _Driver:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    async def submit(self, payload: str) -> None:
        self.submitted.append(json.loads(payload))


def _db(tmp_path: Path) -> str:
    """建出完整 schema 的临时库：不含任何世界行。"""
    url = f"sqlite:///{tmp_path / 'probe-duals.db'}"
    Base.metadata.create_all(get_engine(url))
    return url


def _action_frame(world_id: str, request_id: str, *, investigator_id: str = "inv-probe") -> dict:
    return {
        "type": "action_request",
        "protocol_version": 1,
        "world_id": world_id,
        "expected_revision": 0,
        "investigator_id": investigator_id,
        "request_id": request_id,
        "action": {"kind": "move", "destination_scene_id": "study"},
    }


def _assert_no_world_footprint(url: str, world_id: str) -> None:
    """缺失世界不得留下任何受理痕迹：世界/状态/受理队列/命令/事件全为空。"""
    with session_scope(url) as session:
        assert session.get(World, world_id) is None
        assert session.get(WorldState, world_id) is None
        assert session.query(PlayerRequest).filter_by(world_id=world_id).count() == 0
        assert session.query(GameCommand).filter_by(world_id=world_id).count() == 0
        assert session.query(EventOutbox).filter_by(world_id=world_id).count() == 0


def _submit_probe_frame(url: str, world_id: str, user_id: str | None):
    """提交一帧并回传 (收到的帧, 抛出的异常)。

    现状记录：缺失世界的拒绝帧无法落 event_outbox（world_id 外键指向 worlds，
    而行不存在），因此客户端可能完全收不到拒绝帧、调用方拿到 IntegrityError。
    安全不变量与之一致（不执行、不落账），但反馈是静默的——已登记待后端确认。
    """
    caller = _Collector()
    try:
        asyncio.run(
            StructuredGateway(url).handle_frame(
                world_id=world_id,
                user_id=user_id,
                frame=_action_frame(world_id, f"req-{user_id or 'local'}"),
                deliver=caller.send,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 现状之一：拒绝无法持久化时直接抛出
        return caller.received, exc
    return caller.received, None


def _assert_fail_closed(received: list[dict], error: Exception | None) -> None:
    """接受两种现状，但绝不接受「被受理」：要么 fail-closed 错误帧，要么异常。"""
    if received:
        codes = [envelope["payload"]["code"] for envelope in received]
        assert all(code in FAIL_CLOSED_CODES for code in codes), codes
    else:
        assert error is not None, "既没有拒绝帧也没有异常：请求可能被静默受理"


def test_missing_world_probe_is_mode_detection_only(tmp_path: Path) -> None:
    """探测层的 fail-soft 只决定「走不走结构化」；执行权仍由服务端逐帧判定。"""
    url = _db(tmp_path)

    # 模式判定：缺失世界按 legacy（连接循环据此不启用结构化入口）。
    assert structured_frame_gate_reason(url, MISSING_WORLD) is None
    assert room_world_modes(url, MISSING_WORLD) == ("legacy", "human")

    # 对偶：认证用户不是成员 → 没有调查员/keeper 权限，帧不被受理。
    received, error = _submit_probe_frame(url, MISSING_WORLD, "stranger")
    _assert_fail_closed(received, error)
    if received:  # 能落库时必须给出明确的成员拒绝码
        assert received[0]["payload"]["code"] == "not_authorized"
    _assert_no_world_footprint(url, MISSING_WORLD)


def test_missing_world_rejects_local_operator_frame_without_executing(tmp_path: Path) -> None:
    """本地无账号路径同样不放行：拒绝码 fail-closed，且不建世界/状态/受理记录。"""
    url = _db(tmp_path)
    received, error = _submit_probe_frame(url, MISSING_WORLD, None)
    _assert_fail_closed(received, error)
    _assert_no_world_footprint(url, MISSING_WORLD)

    # 本地引导 `ensure_local_operator` 会为任意 world_id 建成员行（不校验 World
    # 是否存在），但该行随拒绝落库失败一并回滚，库中不留孤儿成员记录。
    # 这条断言是数据卫生的期望值：将来若改成「先提交引导再拒绝」，此处应报警。
    with session_scope(url) as session:
        assert session.query(WorldMember).filter_by(world_id=MISSING_WORLD).count() == 0


def test_missing_world_room_connection_gets_no_access(tmp_path: Path) -> None:
    """房间路径：成员校验仍是最终边界，缺失世界拿不到连接。"""
    url = _db(tmp_path)
    driver = _Driver()
    room = GameRoom(
        MISSING_WORLD,
        SimpleNamespace(),
        RoomEventHub(MISSING_WORLD),
        "owner",
        current_actor_user_id="owner",
        status="lobby",
    )
    room.driver_transport = driver
    socket = _Socket([{"type": "start", "action_id": "missing-world-start"}])
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: url),
        room_roster=lambda _world_id: ([], set()),
    )

    with patch("src.multiplayer.messages.websocket_user", return_value=object()):
        asyncio.run(
            run_room_message_loop(
                controller,
                socket,
                room,
                SimpleNamespace(id="stranger"),
                room.world_id,
                "stranger-tab",
                "player",
            )
        )

    assert socket.close_codes == [4403]
    assert driver.submitted == []
    assert room.status == "lobby"


def test_legacy_world_structured_frame_never_falls_back_to_legacy_turn(tmp_path: Path) -> None:
    """降级对偶：legacy 世界收到结构化帧 → `profile_mismatch`，绝不落回旧回合管线。

    已有用例覆盖网关层拒绝（`test_structured_ws.py::test_legacy_world_gets_profile_mismatch`）；
    这里补房间路径：结构化帧既不会被当成旧文字行动提交给引擎，也不会改写世界状态。
    """
    url = _db(tmp_path)
    owner = create_user(url, "legacy_room_owner", "owner password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-legacy-room",
                module_name="mansion_of_madness",
                module_id="mansion_of_madness",
                module_version="1",
                created_by=owner.id,
                status="active",
                metadata_json={"execution_profile": "legacy"},
            )
        )
        # 真实成员行：让本用例检验 profile 门禁而不是先被成员校验拦下。
        session.add(
            WorldMember(
                id="m-legacy-room",
                world_id="world-legacy-room",
                user_id=owner.id,
                role="owner",
            )
        )
        # 已认领调查员：principal 解析通过后，请求才会走到 profile 门禁。
        session.add(
            WorldInvestigator(
                id="wi-legacy-room",
                world_id="world-legacy-room",
                character_key="inv-legacy-room",
                character_ref={"type": "inline", "data": {"name": "旧世界调查员"}},
                controller_user_id=owner.id,
                status="claimed",
            )
        )

    driver = _Driver()
    room = GameRoom(
        "world-legacy-room",
        SimpleNamespace(),
        RoomEventHub("world-legacy-room"),
        "owner",
        current_actor_user_id="owner",
        status="playing",
    )
    room.driver_transport = driver
    socket = _Socket(
        [
            _action_frame(
                "world-legacy-room",
                "req-legacy-structured",
                investigator_id="inv-legacy-room",
            )
        ]
    )
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: url),
        room_roster=lambda _world_id: ([], set()),
    )

    with (
        patch("src.multiplayer.messages.websocket_user", return_value=object()),
        patch("src.multiplayer.messages.authorize_world", return_value="owner"),
    ):
        with pytest.raises(RuntimeError, match="test complete"):
            asyncio.run(
                run_room_message_loop(
                    controller,
                    socket,
                    room,
                    SimpleNamespace(id=owner.id),  # 真实成员，才能走到 profile 门禁
                    room.world_id,
                    "owner-tab",
                    "owner",
                )
            )

    codes = [m["payload"]["code"] for m in socket.sent if m.get("type") == "request_error"]
    assert codes == ["profile_mismatch"], socket.sent
    assert driver.submitted == []  # 没有降级成旧回合
    with session_scope(url) as session:
        assert session.get(WorldState, "world-legacy-room") is None
        # 已存在的世界能把拒绝落库（与「世界缺失时外键拒绝、客户端收不到帧」相对照）；
        # 但落库的只能是 request_error，不能出现任何受理/成功事件。
        outbox_types = {
            row.event_type
            for row in session.query(EventOutbox).filter_by(world_id="world-legacy-room")
        }
        assert outbox_types <= {"request_error"}, outbox_types


def test_database_failure_during_probe_executes_nothing(tmp_path: Path) -> None:
    """真实数据库故障：探测失败即结束连接循环，但不得有任何执行副作用。

    当前实现（`src/multiplayer/messages.py`，后端负责人域内）在探测抛错时直接结束
    循环，客户端不会收到拒绝帧——已作为残余缺口登记；这里钉住的是安全不变量：
    没有提交、没有 ack、房间状态不变。
    """
    url = f"sqlite:///{tmp_path / 'no-such-dir' / 'broken.db'}"
    with pytest.raises(OperationalError):  # 正控制：该 URL 确实打不开
        create_engine(url).connect()

    driver = _Driver()
    room = GameRoom(
        "world-broken-db",
        SimpleNamespace(),
        RoomEventHub("world-broken-db"),
        "owner",
        current_actor_user_id="owner",
        status="lobby",
    )
    room.driver_transport = driver
    socket = _Socket([{"type": "start", "action_id": "broken-db-start"}])
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: url),
        room_roster=lambda _world_id: ([], set()),
    )

    async def scenario() -> None:
        try:
            await run_room_message_loop(
                controller,
                socket,
                room,
                SimpleNamespace(id="owner"),
                room.world_id,
                "owner-tab",
                "owner",
            )
        except Exception:
            pass  # 当前实现以异常结束循环；安全不变量在下面断言

    asyncio.run(scenario())

    assert driver.submitted == []
    assert room.status == "lobby"
    assert room.action_active is False
    # 任何回给客户端的帧都只能是 fail-closed 错误，绝不能是受理 ack。
    assert not [m for m in socket.sent if m.get("type") in {"action_ack", "action_status"}]
    assert all(
        m.get("type") in {"request_error", "room_action_rejected", "error"} for m in socket.sent
    )
