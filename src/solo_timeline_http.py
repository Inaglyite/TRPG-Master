"""云端单人时间线的 HTTP 控制面：大厅内管理，不需要进入房间。

WS 协议（``solo_timeline_ws``）服务游戏内场景；大厅“管理时间线”不应以
建立房间连接为代价，因此列表/切换/重命名/删除各有一个 HTTP 端点。权限与
锁定纪律与 WS 版完全一致：只认显式 play_mode == "solo"、房主本人、第二
成员防御性锁定、同树校验、房间行动锁 + 持久租约。创建分支仍只在游戏内
（绑定“当前进度”语义），大厅面板不提供。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auth import audit, request_user
from .database import Turn, World, WorldMember, session_scope
from .multiplayer import (
    MultiplayerError,
    finish_room_action,
    reserve_room_action,
    world_play_mode,
)
from .room_runtime import ActionReservationError, RoomManager
from .solo_timeline_ws import (
    commit_solo_switch,
    solo_current_world_id_in_session,
    teardown_room_for_switch,
    tree_root_id,
)
from .world_branches import WorldBranchService

logger = logging.getLogger("trpg.solo_timeline_http")


def _error(exc: MultiplayerError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.message, "code": exc.code},
        status_code=exc.status_code,
    )


def _service(project_root: Path, runtime_root: Path) -> WorldBranchService:
    return WorldBranchService(project_root, runtime_root)


def _solo_tree_preflight(db_url: str, world_id: str, user_id: str) -> dict:
    """大厅时间线操作的公共预检：solo + 房主 + 成员数 + 树解析。

    路径里的 world_id 可以是树内任意世界（根或分支），一律解析到树根与
    当前时间线后再操作，客户端拿着旧分支 id 也能正常工作。
    """
    with session_scope(db_url) as session:
        world = session.get(World, world_id)
        if world is None or world.status != "active":
            raise MultiplayerError("world_not_found", "存档不存在或已删除", 404)
        member = (
            session.query(WorldMember)
            .filter_by(world_id=world_id, user_id=user_id)
            .one_or_none()
        )
        if member is None or member.role != "owner":
            raise MultiplayerError("owner_required", "只有房主可以管理时间线", 403)
        if world_play_mode(world.metadata_json) != "solo":
            raise MultiplayerError(
                "solo_world_required", "只有私密单人世界支持时间线管理", 403
            )
        member_count = (
            session.query(WorldMember).filter_by(world_id=world_id).count()
        )
        if member_count > 1:
            raise MultiplayerError(
                "solo_membership_violated",
                "单人世界存在额外成员，时间线操作已锁定",
                409,
            )
        root_id = tree_root_id(session, world_id)
        current_id = solo_current_world_id_in_session(session, world_id)
        return {
            "root_id": root_id,
            "current_id": current_id,
            "module_name": world.module_name,
        }


def _tree_entries(
    project_root: Path, runtime_root: Path, module_name: str, current_id: str
) -> list[dict]:
    return _service(project_root, runtime_root).list_worlds(
        module_name,
        active_world_id=current_id,
    )


def _find_entry(entries: list[dict], target_world_id: str) -> dict:
    entry = next(
        (item for item in entries if item["world_id"] == target_world_id), None
    )
    if entry is None:
        raise MultiplayerError(
            "world_not_in_tree", "目标时间线不属于当前存档", 403
        )
    return entry


def register_solo_timeline_http_routes(
    router: APIRouter,
    *,
    database_url: Callable[[], str],
    room_manager: Callable[[], RoomManager],
    project_root: Path,
    runtime_root: Path,
) -> None:
    """大厅内的时间线管理端点（全部房主-only， solo 世界限定）。"""

    @router.get("/api/worlds/{world_id}/timelines")
    async def list_timelines(world_id: str, request: Request):
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            tree = _solo_tree_preflight(database_url(), world_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        worlds = await asyncio.to_thread(
            _tree_entries,
            project_root,
            runtime_root,
            tree["module_name"],
            tree["current_id"],
        )
        return {
            "root_world_id": tree["root_id"],
            "active_world_id": tree["current_id"],
            "worlds": worlds,
        }

    @router.post("/api/worlds/{world_id}/timelines/switch")
    async def switch_timeline(world_id: str, data: dict, request: Request):
        """大厅切换“当前时间线”：提交指针 + claim 迁移，已加载房间随拆随连。"""
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        target_world_id = str(data.get("target_world_id") or "").strip()
        if not target_world_id:
            return JSONResponse(
                {"detail": "缺少目标时间线 ID", "code": "invalid_world"},
                status_code=400,
            )
        try:
            tree = _solo_tree_preflight(database_url(), world_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        current_id = tree["current_id"]
        if target_world_id == current_id:
            # 幂等：大厅“继续游戏”可以对当前时间线直接复用此结果
            return {
                "root_world_id": tree["root_id"],
                "active_world_id": current_id,
            }
        try:
            entry = _find_entry(
                _tree_entries(
                    project_root, runtime_root, tree["module_name"], current_id
                ),
                target_world_id,
            )
            if not entry["resumable"]:
                raise MultiplayerError(
                    "timeline_not_resumable",
                    "目标时间线没有可继续的自动存档",
                    409,
                )
        except MultiplayerError as exc:
            return _error(exc)

        manager = room_manager()
        # 与两个世界的 get_or_create 串行化；排序取锁避免与 WS 切换路径死锁。
        lock_ids = sorted({current_id, target_world_id})
        action_id = f"solo_world_switch:{secrets.token_urlsafe(18)}"
        local_reservation = False
        reservation_attempted = False
        committed = False
        room = None
        async with manager.world_lifecycle(lock_ids[0]):
            async with manager.world_lifecycle(lock_ids[1]):
                try:
                    room = await manager.get(current_id)
                    if room is not None:
                        if (
                            room.action_active
                            or room.pending_reply_kind is not None
                            or room.terminal_event_pending
                        ):
                            raise MultiplayerError(
                                "room_turn_in_progress",
                                "当前回合或确认请求尚未结束，请稍后再切换",
                                409,
                            )
                        try:
                            await room.reserve_control(user.id, action_id)
                        except ActionReservationError as exc:
                            raise MultiplayerError(exc.code, str(exc), 409) from exc
                        local_reservation = True
                        if room.pending_reply_kind is not None:
                            raise MultiplayerError(
                                "room_turn_in_progress",
                                "当前回合或确认请求尚未结束，请稍后再切换",
                                409,
                            )
                    else:
                        # 房间未加载时用持久层兜底：当前世界有进行中回合不能切。
                        busy_turn = await asyncio.to_thread(
                            _world_has_active_turn, database_url(), current_id
                        )
                        if busy_turn:
                            raise MultiplayerError(
                                "room_turn_in_progress",
                                "当前世界有正在处理的回合，请稍后再切换",
                                409,
                            )
                    target_room = await manager.get(target_world_id)
                    if target_room is not None and target_room.connected_users:
                        raise MultiplayerError(
                            "timeline_in_use",
                            "目标时间线正在其他连接中打开，请先关闭",
                            409,
                        )
                    reservation_attempted = True
                    reserve_room_action(
                        database_url(),
                        current_id,
                        action_id,
                        user.id,
                        "solo_world_switch",
                        required_permission="manage",
                    )
                    commit_solo_switch(
                        database_url(),
                        current_world_id=current_id,
                        target_world_id=target_world_id,
                    )
                    committed = True
                except MultiplayerError as exc:
                    return _error(exc)
                except Exception:
                    logger.exception("大厅切换 solo 时间线失败 world_id=%s", world_id)
                    return JSONResponse(
                        {
                            "detail": "切换时间线时发生服务器错误，请稍后重试",
                            "code": "switch_unavailable",
                        },
                        status_code=503,
                    )
                finally:
                    if not committed and reservation_attempted:
                        try:
                            finish_room_action(
                                database_url(), current_id, action_id, "failed"
                            )
                        except Exception:
                            logger.exception(
                                "切换失败后清理持久租约失败 world_id=%s", current_id
                            )
                    if local_reservation and room is not None:
                        room.release_action(
                            terminal_status="completed" if committed else "failed"
                        )
                if room is not None:
                    # 旧世界房间可能正开在别的标签页：广播后拆除，它会重连到
                    # 新的当前时间线（与游戏内切换同一条客户端路径）。
                    await teardown_room_for_switch(
                        manager,
                        room,
                        target_world_id,
                        label=str(entry["label"] or ""),
                        reason="switched",
                    )
        if committed and reservation_attempted:
            finish_room_action(database_url(), current_id, action_id, "completed")
        audit(
            database_url(),
            "world_switched",
            user_id=user.id,
            world_id=target_world_id,
        )
        return {
            "root_world_id": tree["root_id"],
            "active_world_id": target_world_id,
        }

    @router.post("/api/worlds/{world_id}/timelines/rename")
    async def rename_timeline(world_id: str, data: dict, request: Request):
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        target_world_id = str(data.get("target_world_id") or "").strip()
        try:
            tree = _solo_tree_preflight(database_url(), world_id, user.id)
            _find_entry(
                _tree_entries(
                    project_root, runtime_root, tree["module_name"], tree["current_id"]
                ),
                target_world_id,
            )
            renamed = await asyncio.to_thread(
                _service(project_root, runtime_root).rename_branch,
                target_world_id,
                data.get("label", ""),
            )
        except MultiplayerError as exc:
            return _error(exc)
        except Exception as exc:
            return JSONResponse(
                {"detail": str(exc) or "重命名时间线失败", "code": "rename_failed"},
                status_code=409,
            )
        audit(
            database_url(),
            "world_renamed",
            user_id=user.id,
            world_id=target_world_id,
        )
        return renamed

    @router.post("/api/worlds/{world_id}/timelines/archive")
    async def archive_timeline(world_id: str, data: dict, request: Request):
        """大厅删除分支：不能删当前/主根（服务层复核），目标房间连接中拒绝。"""
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        target_world_id = str(data.get("target_world_id") or "").strip()
        try:
            tree = _solo_tree_preflight(database_url(), world_id, user.id)
            current_id = tree["current_id"]
            _find_entry(
                _tree_entries(
                    project_root, runtime_root, tree["module_name"], current_id
                ),
                target_world_id,
            )
        except MultiplayerError as exc:
            return _error(exc)

        manager = room_manager()
        action_id = f"solo_world_archive:{secrets.token_urlsafe(18)}"
        reservation_attempted = False
        committed = False
        async with manager.world_lifecycle(target_world_id):
            try:
                target_room = await manager.get(target_world_id)
                if target_room is not None and target_room.connected_users:
                    raise MultiplayerError(
                        "timeline_in_use",
                        "目标时间线正在其他连接中打开，请先关闭",
                        409,
                    )
                reservation_attempted = True
                reserve_room_action(
                    database_url(),
                    target_world_id,
                    action_id,
                    user.id,
                    "solo_world_archive",
                    required_permission="manage",
                )
                archived = await asyncio.to_thread(
                    _service(project_root, runtime_root).archive_branch,
                    target_world_id,
                    active_world_id=current_id,
                )
                committed = True
                if target_room is not None:
                    # 已卸载连接的空房间仍持有旧引擎：归档后不得再被复用。
                    try:
                        await manager.remove(target_world_id, target_room)
                    except Exception:
                        logger.exception(
                            "归档后移除空房间失败 world_id=%s", target_world_id
                        )
                    if target_room.driver_transport is not None:
                        try:
                            await target_room.driver_transport.close_input()
                        except Exception:
                            logger.exception(
                                "归档后关闭空房间输入失败 world_id=%s",
                                target_world_id,
                            )
            except MultiplayerError as exc:
                return _error(exc)
            except Exception as exc:
                return JSONResponse(
                    {"detail": str(exc) or "删除时间线失败", "code": "archive_failed"},
                    status_code=409,
                )
            finally:
                if reservation_attempted:
                    try:
                        finish_room_action(
                            database_url(),
                            target_world_id,
                            action_id,
                            "completed" if committed else "failed",
                        )
                    except Exception:
                        logger.exception(
                            "归档后清理持久租约失败 world_id=%s", target_world_id
                        )
        audit(
            database_url(),
            "world_archived",
            user_id=user.id,
            world_id=target_world_id,
        )
        return archived


def _world_has_active_turn(db_url: str, world_id: str) -> bool:
    with session_scope(db_url) as session:
        return (
            session.query(Turn.id)
            .filter_by(world_id=world_id, status="active")
            .first()
            is not None
        )
