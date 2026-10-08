"""Cloud solo restore is a lifecycle control, never a legacy generation turn."""

import asyncio
import logging

from src.app.game_application import SaveNotFoundError
from src.multiplayer.solo_timeline_ws import _reject, _reserve
from src.storage.database_store import StaleRevisionError

from .branch import restore_structured_save
from .errors import StructuredError
from .restore_receipts import OwnerRestoreRequest

logger = logging.getLogger("trpg.structured_restore")
SOLO_RESTORE_CLOSE_CODE = 4413


async def handle_solo_restore(controller, ws, room, user, data) -> str:
    if set(data) - {"type", "world_id", "slot_id", "action_id", "expected_revision"}:
        await _reject(ws, "invalid_action", "读档请求含有未定义字段。")
        return "handled"
    if data.get("world_id") != room.world_id:
        await _reject(ws, "invalid_action", "读档请求不属于当前世界。")
        return "handled"
    try:
        request = OwnerRestoreRequest(
            user.id, data.get("action_id"), data.get("slot_id"), data.get("expected_revision")
        )
    except StructuredError as exc:
        await _reject(ws, exc.code, str(exc))
        return "handled"
    manager = controller.deps.room_manager()
    async with manager.world_lifecycle(room.world_id):
        if await manager.get(room.world_id) is not room:
            await _reject(ws, "stale_target", "房间运行状态已变化，请重新连接后读档。")
            return "handled"
        action_id = await _reserve(
            controller, ws, room, user.id, "solo_save_load", action_id=request.action_id
        )
        if action_id is None:
            return "handled"
        committed = False
        try:
            result = await asyncio.to_thread(
                restore_structured_save, room.engine.context, request.slot_id, owner_restore=request
            )
            committed = True
            if not result.get("deduplicated"):
                # Retire this runtime before releasing its control lock. Frames
                # already waiting on the gateway lock must be refused as well.
                room.terminal_event_pending = True
        except SaveNotFoundError:
            await _reject(ws, "save_not_found", "未找到这个存档点；当前进度没有被回滚。")
            return "handled"
        except StaleRevisionError:
            await _reject(ws, "revision_conflict", "世界版本已变化，请重新核对存档与当前进度。")
            return "handled"
        except StructuredError as exc:
            await _reject(ws, exc.code, str(exc))
            return "handled"
        except Exception:
            logger.exception("单人结构化读档失败 world_id=%s", room.world_id)
            await _reject(ws, "restore_failed", "存档暂时无法读取；当前进度未回滚，请重新检查。")
            return "handled"
        finally:
            room.release_action(terminal_status="completed" if committed else "failed")
        if result.get("deduplicated"):
            await _reject(ws, "duplicate_action", "此读档已处理，未再次回滚；请刷新核对当前进度。")
            return "handled"
        # All same-owner tabs must discard pre-restore queues, not just the
        # initiating tab. Rebuild this same world's runtime under lifecycle lock.
        try:
            await room.hub.broadcast(
                {
                    "type": "solo_save_restored",
                    "world_id": room.world_id,
                    "slot_id": result["slot_id"],
                    "revision": result["revision"],
                }
            )
        except Exception:
            logger.exception("读档提交后通知失败 world_id=%s", room.world_id)
        try:
            await room.hub.disconnect_all(
                code=SOLO_RESTORE_CLOSE_CODE, reason="存档已读取，请重新同步"
            )
        finally:
            await manager.remove(room.world_id, room)
            if room.driver_transport is not None:
                await room.driver_transport.close_input()
    return "close"
