"""结构化协议 × 多人房间：帧分派、按连接过滤的广播、无模型开场。

房间的每个成员连接在 run_room_message_loop 里各自收帧；结构化帧不进
共享引擎的旧回合管线（效果所有权），直接由命令服务落库，事件经
RoomEventHub.send_direct 按接收者 principal 过滤后逐连接投递。

human keeper_mode 的结构化房间：开局只物化调查员名册 + 翻转房间状态 +
下发快照，不建模型会话、不做 BYOK 就绪检查、不跑开场回合。
"""

from __future__ import annotations

import logging

from src.gameplay.investigators import initialize_investigator_roster
from src.storage.database import World, WorldInvestigator, session_scope

from .bootstrap import KEEPER_MODES
from .gateway import STRUCTURED_FRAME_TYPES, StructuredGateway, world_modes
from .service import audience_visible, wire_envelope

logger = logging.getLogger("trpg.structured_room")

_gateways: dict[str, StructuredGateway] = {}


def gateway_for(database_url: str) -> StructuredGateway:
    gateway = _gateways.get(database_url)
    if gateway is None:
        gateway = StructuredGateway(database_url)
        _gateways[database_url] = gateway
    return gateway


def room_world_modes(database_url: str, world_id: str) -> tuple[str, str]:
    with session_scope(database_url) as session:
        world = session.get(World, world_id)
        if world is None:
            return ("legacy", "human")
        return world_modes(world.metadata_json)


async def handle_room_structured_frame(
    controller,
    room,
    ws,
    user,
    world_id: str,
    connection_id: str,
    frame: dict,
) -> None:
    """处理一条结构化帧：错误回发起方；事件按每个连接的 principal 过滤。"""
    gateway = gateway_for(controller.deps.database_url())
    # 本帧涉及连接的 principal 只解析一次（一帧产生的事件共享接收者集合）。
    principals: dict[str, object] = {}

    async def deliver(envelope: dict) -> None:
        await ws.send_json(envelope)

    async def broadcast(envelope: dict) -> None:
        await _broadcast(envelope, include_origin=False)

    async def broadcast_all(envelope: dict) -> None:
        """Agent 运行没有发起连接：发起玩家也必须收到 agent 产生的事件。"""
        await _broadcast(envelope, include_origin=True)

    async def _broadcast(envelope: dict, *, include_origin: bool) -> None:
        audience = envelope.get("audience") or {"kind": "public"}
        wire = wire_envelope(envelope)
        for connection in await room.hub.connection_snapshot():
            other_id = connection["connection_id"]
            if other_id == connection_id and not include_origin:
                continue  # 发起方已由 deliver 按自身 principal 投递
            other_user = str(connection["user_id"])
            if other_user not in principals:
                principals[other_user] = gateway.connection_principal(world_id, other_user)
            principal = principals[other_user]
            if principal is None:
                continue
            if not audience_visible(audience, principal):
                continue
            await room.hub.send_direct(other_id, wire)

    await gateway.handle_frame(
        world_id=world_id,
        user_id=user.id,
        frame=frame,
        deliver=deliver,
        broadcast=broadcast,
        agent_broadcast=broadcast_all,
    )


async def handle_structured_room_start(
    controller,
    room,
    ws,
    user,
    world_id: str,
) -> None:
    """结构化房间的无模型开场：翻转状态 + 全员快照，不启动引擎回合。"""
    if room.status != "lobby":
        await ws.send_json(
            {
                "type": "room_action_rejected",
                "code": "room_already_started",
                "message": "房间已经开始游戏",
            }
        )
        return
    gateway = gateway_for(controller.deps.database_url())
    principal = gateway.connection_principal(world_id, user.id)
    if principal is None or principal.kind != "keeper":
        await ws.send_json(
            {
                "type": "room_action_rejected",
                "code": "keeper_required",
                "message": "结构化房间由获授权的守秘人开局（keeper 与房主分别授权）",
            }
        )
        return
    # 把玩家认领的调查员物化进世界状态（与旧模式 handle_start 同一步，只是
    # 不跑开场回合）。没有这一步，世界状态里没有 investigators 名册，
    # 命令服务看不到任何调查员：grant_clue/request_check/adjust_stat 的目标
    # 都会 object_not_found，玩家 principal 也拿不到自己的 investigator_id，
    # 快照只能退化成 public_investigator_roster 的兜底 id。
    try:
        # 注意标识空间：结构化层（player/keeper principal、audience 过滤、
        # check 归属）统一以 **character_key** 作为调查员 id（见
        # principal.controlled_investigators 与 bootstrap.local_player_investigator_ids）。
        # 房间的 claim 行 id 是另一套标识，这里必须用 character_key 作状态键，
        # 否则发布给甲的定向事件在按 principal 过滤时会被丢掉。
        with session_scope(controller.deps.database_url()) as session:
            claims = (
                session.query(WorldInvestigator)
                .filter_by(world_id=world_id, status="claimed")
                .all()
            )
            roster = [
                {
                    "investigator_id": str(claim.character_key),
                    "user_id": str(claim.controller_user_id or ""),
                    "character_ref": dict(claim.character_ref or {}),
                }
                for claim in claims
                if claim.controller_user_id and claim.character_key
            ]
        if not roster:
            await ws.send_json(
                {
                    "type": "room_action_rejected",
                    "code": "investigator_required",
                    "message": "房间中还没有玩家选择调查员",
                }
            )
            return
        initialize_investigator_roster(
            room.engine.context,
            roster,
            active_investigator_id=str(roster[0]["investigator_id"]),
        )
    except Exception as exc:  # noqa: BLE001 - 开局失败要回执而不是静默
        logger.warning("结构化房间开局物化调查员名册失败：%s", exc)
        await ws.send_json(
            {
                "type": "room_action_rejected",
                "code": "investigator_required",
                "message": str(exc) or "调查员名册不可用，请重新选择角色",
            }
        )
        return
    controller.set_room_status(room, "playing")
    await controller.broadcast_room_state(room)
    # 全员按各自 principal 重同步（keeper 与玩家看到不同的快照投影）。
    for connection in await room.hub.connection_snapshot():
        envelope = gateway.snapshot_envelope(world_id=world_id, user_id=str(connection["user_id"]))
        if envelope is not None:
            await room.hub.send_direct(connection["connection_id"], envelope)


def structured_frame_gate_reason(database_url: str, world_id: str) -> str | None:
    """返回 None 表示非结构化世界；否则返回该世界结构化帧的 keeper_mode 提示。"""
    profile, keeper_mode = room_world_modes(database_url, world_id)
    if profile != "structured_v1":
        return None
    if keeper_mode not in KEEPER_MODES:  # 防御：metadata 损坏时按 human 处理
        logger.warning("world %s 的 keeper_mode 非法：%s", world_id, keeper_mode)
    return keeper_mode


__all__ = [
    "STRUCTURED_FRAME_TYPES",
    "gateway_for",
    "handle_room_structured_frame",
    "handle_structured_room_start",
    "room_world_modes",
    "structured_frame_gate_reason",
]
