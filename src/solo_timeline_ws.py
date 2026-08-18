"""云端单人（play_mode == "solo"）房间的专用时间线协议。

多人房间的 GameRoom / RoomEventHub / 控制面（WorldMember、RoomAction）全部按
world_id 绑定，本地模式 ``world_timeline_ws`` 的热切换 handler 无法直接搬进房间
管线。因此 solo 房间使用独立消息名（``solo_*``），多人房间对本地时间线消息的
拒绝逻辑（``UNSUPPORTED_ROOM_TYPES``）一行不动，安全边界是结构性的。

切换语义：不重绑运行中的房间，而是“提交指针 + 断开重连”。服务端在一个事务里
把树根 metadata 的 ``solo_current_world_id`` 指针和调查员 claim 一起移到目标
世界，广播 ``solo_world_switched`` 后拆除旧房间；客户端收到广播后重连目标世界，
由正常的房间引导恢复历史、存档与私有状态。claim 行随行迁移（而不是按世界复制）
是因为世界快照里的调查员实体以 claim.id 为键，同树快照全部沿用最初的 id。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from .auth import audit
from .database import (
    World,
    WorldInvestigator,
    WorldMember,
    new_id,
    session_scope,
    utcnow,
)
from .multiplayer import (
    MultiplayerError,
    reserve_room_action,
)
from .room_runtime import ActionReservationError, GameRoom
from .world_branches import WorldBranchService

logger = logging.getLogger("trpg.solo_timeline_ws")

SOLO_TIMELINE_MESSAGE_TYPES = frozenset(
    {
        "solo_world_list",
        "solo_branch_create",
        "solo_world_switch",
        "solo_world_rename",
        "solo_world_archive",
    }
)

# 树根 metadata_json 中记录“当前时间线”的键；缺省即根世界本身。
POINTER_KEY = "solo_current_world_id"

# 切换/重定向专用的关闭码：区别于房间删除的 4404，客户端据此重连而非退出。
SOLO_SWITCH_CLOSE_CODE = 4412


async def _reject(ws: Any, code: str, message: str) -> None:
    await ws.send_json(
        {
            "type": "room_action_rejected",
            "code": code,
            "message": message,
        }
    )


def _service(controller: Any) -> WorldBranchService:
    return WorldBranchService(
        controller.deps.project_root,
        controller.deps.runtime_root,
    )


def tree_root_id(session: Any, world_id: str) -> str:
    """沿 metadata.branch.parent_world_id 走到树根（带环保护）。"""
    root_id = world_id
    visited: set[str] = set()
    while root_id not in visited:
        visited.add(root_id)
        world = session.get(World, root_id)
        if world is None:
            break
        branch = dict(world.metadata_json or {}).get("branch")
        if not isinstance(branch, dict):
            break
        parent = str(branch.get("parent_world_id") or "").strip()
        if not parent:
            break
        root_id = parent
    return root_id


def solo_current_world_id_in_session(session: Any, world_id: str) -> str:
    """会话内版本：返回该 solo 世界树当前时间线的 world_id。

    指针无效（目标不存在/已归档/不属于同一棵树）时自愈回退到树根；
    没有指针的历史 solo 世界当前时间线就是根世界本身。
    """
    root_id = tree_root_id(session, world_id)
    root = session.get(World, root_id)
    if root is None or root.status != "active":
        return world_id
    pointer = str(dict(root.metadata_json or "").get(POINTER_KEY) or "").strip()
    if not pointer or pointer == world_id:
        return world_id if pointer else root_id
    target = session.get(World, pointer)
    if target is None or target.status != "active":
        return root_id
    if tree_root_id(session, pointer) != root_id:
        return root_id
    return pointer


def resolve_solo_current_world_id(db_url: str, world_id: str) -> str:
    """返回该 solo 世界树的当前时间线 world_id（供连接重定向与大厅续玩）。"""
    with session_scope(db_url) as session:
        return solo_current_world_id_in_session(session, world_id)


def _gate(room: GameRoom, role: str, db_url: str) -> MultiplayerError | None:
    """所有 solo 时间线操作的公共门禁。

    只认显式的 play_mode == "solo"，不按当前连接数推断；多人房间即使暂时
    只剩一人也继续拒绝。第二成员防线是防御性的：创建/邀请/角色变更路径
    本来就封死 solo 世界加人，但直接 SQL 没有数据库约束兜底。
    """
    if room.play_mode != "solo":
        return MultiplayerError(
            "solo_world_required",
            "只有私密单人世界支持时间线管理",
            403,
        )
    if role != "owner":
        return MultiplayerError("owner_required", "只有房主可以管理时间线", 403)
    with session_scope(db_url) as session:
        member_count = (
            session.query(WorldMember).filter_by(world_id=room.world_id).count()
        )
    if member_count > 1:
        return MultiplayerError(
            "solo_membership_violated",
            "单人世界存在额外成员，时间线操作已锁定",
            409,
        )
    return None


def _tree_entries(
    controller: Any, room: GameRoom
) -> list[dict]:
    """当前房间所属分支树的时间线条目（含 resumable 判定）。"""
    return _service(controller).list_worlds(
        room.engine.context.module_name,
        active_world_id=room.world_id,
    )


async def _send_timeline_lists(
    controller: Any, ws: Any, room: GameRoom, user_id: str
) -> None:
    """按本地会话的线协议形状回发 world_list + adventure_list。

    与本地 ``send_save_panels`` 的纪律一致：两个列表成对刷新，前端存档面板
    因此可以原样复用本地的时间线渲染路径。adventure_list 只保留当前房间
    所属的存档位（树），云端大厅的“我的冒险”列表不归这里管。
    """
    worlds = _tree_entries(controller, room)
    await ws.send_json(
        {
            "type": "world_list",
            "active_world_id": room.world_id,
            "worlds": worlds,
        }
    )
    tree_ids = {entry["world_id"] for entry in worlds}
    with session_scope(controller.deps.database_url()) as session:
        allowed = {
            row.world_id
            for row in session.query(WorldMember).filter_by(user_id=user_id).all()
        }
    adventures = _service(controller).list_adventures(
        active_world_id=room.world_id,
        module_name=room.engine.context.module_name,
        allowed_world_ids=allowed,
    )
    adventures = [
        adventure
        for adventure in adventures
        if any(t["world_id"] in tree_ids for t in adventure["timelines"])
    ]
    module_titles = {
        mod["id"]: mod.get("title") for mod in controller.deps.list_modules()
    }
    for adventure in adventures:
        adventure["module_title"] = module_titles.get(
            adventure["module_name"], adventure["module_name"]
        )
    await ws.send_json(
        {
            "type": "adventure_list",
            "active_world_id": room.world_id,
            "adventures": adventures,
        }
    )


async def _reserve(
    controller: Any,
    ws: Any,
    room: GameRoom,
    user_id: str,
    action_type: str,
) -> str | None:
    """房间级行动锁（内存 + 持久租约），模板同 solo-abandon。

    回合生成中、待确认请求悬挂时拒绝；成功返回 action_id，失败时已向
    客户端发送拒绝原因并释放内存锁，返回 None。
    """
    if (
        room.action_active
        or room.pending_reply_kind is not None
        or room.terminal_event_pending
    ):
        await _reject(
            ws,
            "room_turn_in_progress",
            "当前回合或确认请求尚未结束，请稍后再管理时间线",
        )
        return None
    action_id = f"{action_type}:{secrets.token_urlsafe(18)}"
    try:
        await room.reserve_control(user_id, action_id)
    except ActionReservationError as exc:
        await _reject(ws, exc.code, str(exc))
        return None
    # reserve_control 的 await 点可能到达 driver 事件，复查确认请求边缘。
    if room.pending_reply_kind is not None:
        room.release_action(terminal_status="failed")
        await _reject(
            ws,
            "room_turn_in_progress",
            "当前回合或确认请求尚未结束，请稍后再管理时间线",
        )
        return None
    try:
        reserve_room_action(
            controller.deps.database_url(),
            room.world_id,
            action_id,
            user_id,
            action_type,
            required_permission="manage",
        )
    except MultiplayerError as exc:
        room.release_action(terminal_status="failed")
        await _reject(ws, exc.code, str(exc))
        return None
    except Exception:
        room.release_action(terminal_status="failed")
        await _reject(ws, "reservation_unavailable", "行动暂时无法登记，请稍后重试")
        return None
    return action_id


def _move_claims(session: Any, from_world_id: str, to_world_id: str) -> None:
    """把已占用调查员 claim 随行迁到目标世界（快照实体以 claim.id 为键）。"""
    claims = (
        session.query(WorldInvestigator)
        .filter_by(world_id=from_world_id, status="claimed")
        .all()
    )
    for claim in claims:
        claim.world_id = to_world_id
        claim.updated_at = utcnow()


def _set_pointer(session: Any, root_id: str, current_world_id: str) -> None:
    root = (
        session.query(World).filter_by(id=root_id).with_for_update().one_or_none()
    )
    if root is None or root.status != "active":
        raise MultiplayerError("world_not_found", "存档不存在或已删除", 404)
    metadata = dict(root.metadata_json or {})
    metadata[POINTER_KEY] = current_world_id
    root.metadata_json = metadata
    root.updated_at = utcnow()


def _commit_branch_control_plane(
    db_url: str,
    *,
    room: GameRoom,
    user_id: str,
    branch_world_id: str,
) -> None:
    """分支创建后的云端控制面补全（单事务）。

    ``WorldBranchService.create`` 是本地语义：不知道 play_mode、不建成员行、
    不搬 claim。solo 房间的分支世界要成为可独立连接的房间，必须继承 solo
    约束与房间状态，并把“当前时间线”指针与 claim 一起切到分支。
    """
    with session_scope(db_url) as session:
        branch_world = (
            session.query(World)
            .filter_by(id=branch_world_id)
            .with_for_update()
            .one_or_none()
        )
        if branch_world is None or branch_world.status != "active":
            raise MultiplayerError("world_not_found", "分支世界创建失败", 500)
        source_world = session.get(World, room.world_id)
        if source_world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        source_metadata = dict(source_world.metadata_json or {})
        metadata = dict(branch_world.metadata_json or {})
        metadata["play_mode"] = "solo"
        metadata["max_players"] = 1
        metadata["room_status"] = str(source_metadata.get("room_status") or "lobby")
        if source_metadata.get("name"):
            metadata["name"] = source_metadata["name"]
        branch_world.metadata_json = metadata
        branch_world.updated_at = utcnow()
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id=branch_world_id,
                user_id=user_id,
                role="owner",
            )
        )
        _move_claims(session, room.world_id, branch_world_id)
        _set_pointer(session, tree_root_id(session, room.world_id), branch_world_id)


def commit_solo_switch(db_url: str, *, current_world_id: str, target_world_id: str) -> None:
    """切换时间线的控制面事务：校验同树后移动指针与 claim。

    WS（游戏内切换）与 HTTP（大厅切换）共用：当前世界由调用方按上下文
    给出（房间绑定世界 / 树根指针解析结果）。
    """
    with session_scope(db_url) as session:
        target = (
            session.query(World)
            .filter_by(id=target_world_id)
            .with_for_update()
            .one_or_none()
        )
        if target is None or target.status != "active":
            raise MultiplayerError("world_not_found", "目标时间线不存在或已删除", 404)
        root_id = tree_root_id(session, current_world_id)
        if tree_root_id(session, target_world_id) != root_id:
            raise MultiplayerError(
                "world_not_in_tree", "目标时间线不属于当前存档", 403
            )
        _move_claims(session, current_world_id, target_world_id)
        _set_pointer(session, root_id, target_world_id)


async def teardown_room_for_switch(
    room_manager: Any, room: GameRoom, target_world_id: str, *, label: str, reason: str
) -> None:
    """切换提交后的广播与旧房间拆除（模板同 _teardown_archived_room）。"""
    try:
        await room.hub.broadcast(
            {
                "type": "solo_world_switched",
                "world_id": target_world_id,
                "label": label,
                "reason": reason,
            }
        )
    except Exception:
        logger.exception("切换后广播 solo_world_switched 失败 world_id=%s", room.world_id)
    try:
        await room.hub.disconnect_all(
            code=SOLO_SWITCH_CLOSE_CODE, reason="时间线已切换"
        )
    except Exception:
        logger.exception("切换后断开房间连接失败 world_id=%s", room.world_id)
    try:
        await room_manager.remove(room.world_id, room)
    except Exception:
        logger.exception("切换后移除房间运行时失败 world_id=%s", room.world_id)
    if room.driver_transport is not None:
        try:
            await room.driver_transport.close_input()
        except Exception:
            logger.exception("切换后关闭房间输入失败 world_id=%s", room.world_id)


async def handle_solo_timeline_message(
    controller: Any,
    ws: Any,
    room: GameRoom,
    user: Any,
    role: str,
    data: dict,
) -> str:
    """处理一条 solo 时间线消息；返回 "handled" 或 "close"（连接将被拆除）。"""
    db_url = controller.deps.database_url()
    message_type = str(data.get("type") or "")
    denied = _gate(room, role, db_url)
    if denied is not None:
        await _reject(ws, denied.code, denied.message)
        return "handled"

    if message_type == "solo_world_list":
        await _send_timeline_lists(controller, ws, room, user.id)
        return "handled"

    if message_type == "solo_world_rename":
        target_world_id = str(data.get("world_id") or "").strip()
        tree_ids = {entry["world_id"] for entry in _tree_entries(controller, room)}
        if target_world_id not in tree_ids:
            await _reject(ws, "world_not_in_tree", "目标时间线不属于当前存档")
            return "handled"
        try:
            renamed = await asyncio.to_thread(
                _service(controller).rename_branch,
                target_world_id,
                data.get("label", ""),
            )
        except Exception as exc:
            await _reject(ws, "rename_failed", str(exc) or "重命名时间线失败")
            return "handled"
        audit(
            db_url,
            "world_renamed",
            user_id=user.id,
            world_id=target_world_id,
        )
        await ws.send_json(
            {
                "type": "world_renamed",
                "world_id": renamed["world_id"],
                "label": renamed["label"],
            }
        )
        await _send_timeline_lists(controller, ws, room, user.id)
        return "handled"

    if message_type == "solo_branch_create":
        return await _handle_branch_create(controller, ws, room, user, db_url, data)
    if message_type == "solo_world_switch":
        return await _handle_switch(controller, ws, room, user, db_url, data)
    if message_type == "solo_world_archive":
        return await _handle_archive(controller, ws, room, user, db_url, data)
    await _reject(ws, "unsupported_in_room", "该操作不能在共享房间中执行")
    return "handled"


async def _handle_branch_create(
    controller: Any,
    ws: Any,
    room: GameRoom,
    user: Any,
    db_url: str,
    data: dict,
) -> str:
    turn_id = str(data.get("turn_id") or "").strip()
    if not turn_id:
        await _reject(ws, "invalid_turn", "缺少分支回合 ID")
        return "handled"
    action_id = await _reserve(controller, ws, room, user.id, "solo_branch_create")
    if action_id is None:
        return "handled"
    committed = False
    branch_world_id = ""
    branch_label = ""
    try:
        branch = await asyncio.to_thread(
            _service(controller).create,
            room.engine.context,
            room.engine.turn_journal,
            turn_id,
            label=data.get("label", ""),
            user_id=user.id,
        )
        branch_world_id = branch.context.world_id
        branch_label = branch.label
        _commit_branch_control_plane(
            db_url,
            room=room,
            user_id=user.id,
            branch_world_id=branch_world_id,
        )
        committed = True
    except Exception as exc:
        if branch_world_id:
            # 控制面补全失败：分支世界不得成为无人认领的孤儿房间。
            try:
                with session_scope(db_url) as session:
                    orphan = session.get(World, branch_world_id)
                    if orphan is not None and orphan.status == "active":
                        orphan.status = "archived"
                        orphan.updated_at = utcnow()
            except Exception:
                logger.exception("清理孤儿分支世界失败 world_id=%s", branch_world_id)
        await _reject(ws, "branch_failed", str(exc) or "创建时间线分支失败")
        return "handled"
    finally:
        room.release_action(terminal_status="completed" if committed else "failed")
    audit(
        db_url,
        "world_branched",
        user_id=user.id,
        world_id=branch_world_id,
        details={"source_turn_id": turn_id},
    )
    # 与本地语义一致：创建分支后直接进入新分支（提交指针 + 断开重连）。
    await teardown_room_for_switch(
        controller.deps.room_manager(),
        room,
        branch_world_id,
        label=branch_label,
        reason="branch_created",
    )
    return "close"


async def _handle_switch(
    controller: Any,
    ws: Any,
    room: GameRoom,
    user: Any,
    db_url: str,
    data: dict,
) -> str:
    target_world_id = str(data.get("world_id") or "").strip()
    if not target_world_id:
        await _reject(ws, "invalid_world", "缺少目标时间线 ID")
        return "handled"
    if target_world_id == room.world_id:
        await _reject(ws, "already_active", "当前已经在该时间线")
        return "handled"
    entries = _tree_entries(controller, room)
    entry = next(
        (item for item in entries if item["world_id"] == target_world_id), None
    )
    if entry is None:
        await _reject(ws, "world_not_in_tree", "目标时间线不属于当前存档")
        return "handled"
    if not entry["resumable"]:
        await _reject(
            ws, "timeline_not_resumable", "目标时间线没有可继续的自动存档"
        )
        return "handled"
    action_id = await _reserve(controller, ws, room, user.id, "solo_world_switch")
    if action_id is None:
        return "handled"
    committed = False
    manager = controller.deps.room_manager()
    # 与目标世界的 get_or_create 串行化：另一个标签页可能正在连接它。
    async with manager.world_lifecycle(target_world_id):
        target_room = await manager.get(target_world_id)
        if target_room is not None and target_room.connected_users:
            room.release_action(terminal_status="failed")
            await _reject(
                ws, "timeline_in_use", "目标时间线正在其他连接中打开，请先关闭"
            )
            return "handled"
        try:
            commit_solo_switch(
                db_url,
                current_world_id=room.world_id,
                target_world_id=target_world_id,
            )
            committed = True
        except MultiplayerError as exc:
            await _reject(ws, exc.code, exc.message)
            return "handled"
        except Exception:
            logger.exception("切换 solo 时间线失败 world_id=%s", room.world_id)
            await _reject(ws, "switch_failed", "切换时间线失败，请稍后重试")
            return "handled"
        finally:
            room.release_action(terminal_status="completed" if committed else "failed")
    audit(db_url, "world_switched", user_id=user.id, world_id=target_world_id)
    await teardown_room_for_switch(
        controller.deps.room_manager(),
        room,
        target_world_id,
        label=str(entry["label"] or ""),
        reason="switched",
    )
    return "close"


async def _handle_archive(
    controller: Any,
    ws: Any,
    room: GameRoom,
    user: Any,
    db_url: str,
    data: dict,
) -> str:
    target_world_id = str(data.get("world_id") or "").strip()
    if not target_world_id:
        await _reject(ws, "invalid_world", "缺少目标时间线 ID")
        return "handled"
    tree_ids = {entry["world_id"] for entry in _tree_entries(controller, room)}
    if target_world_id not in tree_ids:
        await _reject(ws, "world_not_in_tree", "目标时间线不属于当前存档")
        return "handled"
    action_id = await _reserve(controller, ws, room, user.id, "solo_world_archive")
    if action_id is None:
        return "handled"
    committed = False
    manager = controller.deps.room_manager()
    async with manager.world_lifecycle(target_world_id):
        target_room = await manager.get(target_world_id)
        if target_room is not None and target_room.connected_users:
            room.release_action(terminal_status="failed")
            await _reject(
                ws, "timeline_in_use", "目标时间线正在其他连接中打开，请先关闭"
            )
            return "handled"
        try:
            archived = await asyncio.to_thread(
                _service(controller).archive_branch,
                target_world_id,
                active_world_id=room.world_id,
            )
            committed = True
        except Exception as exc:
            await _reject(ws, "archive_failed", str(exc) or "删除时间线失败")
            return "handled"
        finally:
            room.release_action(terminal_status="completed" if committed else "failed")
    audit(db_url, "world_archived", user_id=user.id, world_id=target_world_id)
    await ws.send_json(
        {
            "type": "world_archived",
            "world_id": archived["world_id"],
            "fallback_world_id": archived["fallback_world_id"],
        }
    )
    await _send_timeline_lists(controller, ws, room, user.id)
    return "handled"
