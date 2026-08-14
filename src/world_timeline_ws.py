"""WebSocket handlers for local save-slot and timeline operations.

``server.py`` owns process-wide configuration and builds a dependency bundle at
connection time.  Keeping these handlers here prevents the local WebSocket
adapter from growing into another application entry point while preserving the
same wire protocol and session-level locking discipline.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.database import World, WorldMember, new_id, session_scope
from src.persistence import load_game
from src.runtime import RuntimeContext
from src.ws_router import WsMessageRouter


@dataclass(frozen=True)
class WorldTimelineWsDependencies:
    """Process dependencies owned by the FastAPI entry point."""

    world_branches: Any
    database_url: str
    auth_required: Callable[[], bool]
    authorize_world: Callable[[str, str, str, str], None]
    audit: Callable[..., None]
    world_turn_lock: Callable[[RuntimeContext], threading.Lock]
    set_active_context: Callable[[RuntimeContext], None]
    load_theme: Callable[[RuntimeContext], dict]


def register_world_timeline_handlers(
    router: WsMessageRouter,
    *,
    engine: Any,
    outbound: Any,
    turn_gate: Any,
    reserve_turn: Callable[[], Awaitable[bool]],
    release_turn: Callable[[], None],
    resolve_speaker: Any,
    public_chat_events: Callable[[dict], list[dict]],
    send_save_panels: Callable[[], Awaitable[None]],
    send_character_state: Callable[[str | None], Awaitable[None]],
    world_context_payload: Callable[[], dict],
    world_list_payload: Callable[[], dict],
    user_id: str | None,
    auto_save_slot: str,
    dependencies: WorldTimelineWsDependencies,
) -> None:
    """Attach timeline handlers to one local WebSocket session router."""

    @router.handler("turn_branch_create")
    async def handle_turn_branch_create(data: dict) -> None:
        if not await reserve_turn():
            return
        turn_id = str(data.get("turn_id") or "")
        if not turn_id:
            release_turn()
            await outbound.send(
                {
                    "type": "turn_branch_failed",
                    "message": "缺少分支回合 ID",
                }
            )
            return
        try:
            branch = await asyncio.to_thread(
                dependencies.world_branches.create,
                engine.context,
                engine.turn_journal,
                turn_id,
                label=data.get("label", ""),
                user_id=user_id,
            )
            if dependencies.auth_required() and user_id:
                with session_scope(dependencies.database_url) as db_session:
                    world = db_session.get(World, branch.context.world_id)
                    world.created_by = user_id
                    db_session.add(
                        WorldMember(
                            id=new_id("member"),
                            world_id=branch.context.world_id,
                            user_id=user_id,
                            role="owner",
                        )
                    )
                dependencies.audit(
                    dependencies.database_url,
                    "world_branched",
                    user_id=user_id,
                    world_id=branch.context.world_id,
                    details={"source_turn_id": turn_id},
                )
            engine.switch_context(branch.context)
            resolve_speaker.clear()
            engine.adopt_message_history(branch.messages)
            turn_gate.rebind_world(dependencies.world_turn_lock(branch.context))
            dependencies.set_active_context(branch.context)
            history = engine.turn_journal.public_history()
            for turn in history:
                enriched = public_chat_events(turn)
                turn["narrative_segments"] = enriched
                turn["chat_events"] = enriched
        except Exception as exc:
            release_turn()
            await outbound.send(
                {
                    "type": "turn_branch_failed",
                    "source_turn_id": turn_id,
                    "message": str(exc) or "创建时间线分支失败",
                }
            )
            return
        release_turn()
        await outbound.send(
            {
                "type": "turn_branched",
                "source_turn_id": turn_id,
                "world_id": branch.context.world_id,
                "module_name": branch.context.module_name,
                "label": branch.label,
                "history": history,
            }
        )
        await outbound.send(world_context_payload())
        await outbound.send(world_list_payload())
        await send_save_panels()
        await send_character_state()

    @router.handler("world_switch")
    async def handle_world_switch(data: dict) -> None:
        if not turn_gate.try_acquire_session():
            await outbound.send(
                {
                    "type": "world_switch_failed",
                    "message": "当前回合尚未结束，暂时不能切换时间线。",
                }
            )
            return
        target_lock: threading.Lock | None = None
        target_lock_acquired = False
        try:
            target_world_id = str(data.get("world_id") or "")
            if dependencies.auth_required():
                if not user_id:
                    raise PermissionError("未登录")
                dependencies.authorize_world(
                    dependencies.database_url, user_id, target_world_id, "play"
                )
            context = dependencies.world_branches.open(target_world_id)
            target_lock = dependencies.world_turn_lock(context)
            if not target_lock.acquire(blocking=False):
                raise RuntimeError("目标时间线正在处理另一个回合，请稍后重试。")
            target_lock_acquired = True
            messages, _snapshot = load_game(auto_save_slot, context=context)
            if messages is None:
                raise RuntimeError("目标时间线没有可继续的自动存档，无法从存档开始。")
            engine.switch_context(context)
            resolve_speaker.clear()
            if messages is not None:
                engine.adopt_message_history(messages)
            turn_gate.rebind_world(target_lock)
            dependencies.set_active_context(context)
            history = engine.turn_journal.public_history()
            for turn in history:
                enriched = public_chat_events(turn)
                turn["narrative_segments"] = enriched
                turn["chat_events"] = enriched
            if user_id:
                dependencies.audit(
                    dependencies.database_url,
                    "world_switched",
                    user_id=user_id,
                    world_id=context.world_id,
                )
        except Exception as exc:
            await outbound.send(
                {
                    "type": "world_switch_failed",
                    "message": str(exc) or "切换时间线失败",
                }
            )
            return
        finally:
            if target_lock is not None and target_lock_acquired:
                target_lock.release()
            turn_gate.release_session()
        await outbound.send(
            {
                "type": "world_switched",
                "world_id": engine.context.world_id,
                "module_name": engine.context.module_name,
                "history": history,
            }
        )
        await outbound.send(world_context_payload())
        await outbound.send(world_list_payload())
        await outbound.send({"type": "theme", "theme": dependencies.load_theme(engine.context)})
        await send_save_panels()
        await send_character_state()

    @router.handler("world_rename")
    async def handle_world_rename(data: dict) -> None:
        """Rename a timeline display label without touching its data."""
        target_world_id = str(data.get("world_id") or "")
        try:
            if dependencies.auth_required():
                if not user_id:
                    raise PermissionError("未登录")
                dependencies.authorize_world(
                    dependencies.database_url, user_id, target_world_id, "manage"
                )
            renamed = dependencies.world_branches.rename_branch(
                target_world_id, data.get("label", "")
            )
        except Exception as exc:
            await outbound.send(
                {
                    "type": "world_rename_failed",
                    "world_id": target_world_id,
                    "message": str(exc) or "重命名时间线失败",
                }
            )
            return
        await outbound.send(
            {
                "type": "world_renamed",
                "world_id": renamed["world_id"],
                "label": renamed["label"],
            }
        )
        await outbound.send(world_list_payload())
        await send_save_panels()

    @router.handler("world_archive")
    async def handle_world_archive(data: dict) -> None:
        """Logically delete an inactive local timeline branch.

        The current-session reservation avoids racing its own turn.  A second
        lock on the target covers another local WebSocket session in this
        process, while ``WorldBranchService.archive_branch`` adds the durable
        database active-turn check for a competing backend process.
        """
        target_world_id = str(data.get("world_id") or "")
        if not turn_gate.try_acquire_session():
            await outbound.send(
                {
                    "type": "world_archive_failed",
                    "world_id": target_world_id,
                    "message": "当前回合尚未结束，暂时不能删除时间线。",
                }
            )
            return

        target_lock: threading.Lock | None = None
        target_lock_acquired = False
        try:
            if dependencies.auth_required():
                if not user_id:
                    raise PermissionError("未登录")
                dependencies.authorize_world(
                    dependencies.database_url, user_id, target_world_id, "manage"
                )
            target_context = dependencies.world_branches.open(target_world_id)
            target_lock = dependencies.world_turn_lock(target_context)
            if not target_lock.acquire(blocking=False):
                raise RuntimeError("目标时间线正在处理另一个回合，请稍后重试。")
            target_lock_acquired = True
            archived = dependencies.world_branches.archive_branch(
                target_world_id,
                active_world_id=engine.context.world_id,
            )
        except Exception as exc:
            await outbound.send(
                {
                    "type": "world_archive_failed",
                    "world_id": target_world_id,
                    "message": str(exc) or "删除时间线失败",
                }
            )
            return
        finally:
            if target_lock is not None and target_lock_acquired:
                target_lock.release()
            turn_gate.release_session()

        await outbound.send(
            {
                "type": "world_archived",
                "world_id": archived["world_id"],
                "fallback_world_id": archived["fallback_world_id"],
                "worlds": world_list_payload()["worlds"],
            }
        )
        await send_save_panels()

    @router.handler("adventure_rename")
    async def handle_adventure_rename(data: dict) -> None:
        """Rename a save-slot display label without touching its data."""
        root_world_id = str(data.get("root_world_id") or "")
        try:
            if dependencies.auth_required():
                if not user_id:
                    raise PermissionError("未登录")
                dependencies.authorize_world(
                    dependencies.database_url, user_id, root_world_id, "manage"
                )
            renamed = dependencies.world_branches.rename_slot(root_world_id, data.get("label", ""))
        except Exception as exc:
            await outbound.send(
                {
                    "type": "adventure_rename_failed",
                    "root_world_id": root_world_id,
                    "message": str(exc) or "重命名存档失败",
                }
            )
            return
        await outbound.send(
            {
                "type": "adventure_renamed",
                "root_world_id": renamed["root_world_id"],
                "slot_name": renamed["slot_name"],
            }
        )
        await send_save_panels()

    @router.handler("adventure_archive")
    async def handle_adventure_archive(data: dict) -> None:
        """Logically delete a save slot (root timeline and all branches).

        与单条时间线的 ``world_archive`` 同一纪律：会话闸门防本连接回合竞争，
        服务层事务内的 active-turn 检查覆盖其他本地连接/进程。删除是逻辑
        归档，回合与存档保留可恢复。
        """
        root_world_id = str(data.get("root_world_id") or "")
        if not turn_gate.try_acquire_session():
            await outbound.send(
                {
                    "type": "adventure_archive_failed",
                    "root_world_id": root_world_id,
                    "message": "当前回合尚未结束，暂时不能删除存档。",
                }
            )
            return
        try:
            if dependencies.auth_required():
                if not user_id:
                    raise PermissionError("未登录")
                dependencies.authorize_world(
                    dependencies.database_url, user_id, root_world_id, "manage"
                )
            archived = dependencies.world_branches.archive_tree(
                root_world_id,
                active_world_id=engine.context.world_id,
            )
        except Exception as exc:
            await outbound.send(
                {
                    "type": "adventure_archive_failed",
                    "root_world_id": root_world_id,
                    "message": str(exc) or "删除存档失败",
                }
            )
            return
        finally:
            turn_gate.release_session()

        await outbound.send(
            {
                "type": "adventure_archived",
                "root_world_id": archived["root_world_id"],
                "archived_count": archived["count"],
            }
        )
        await outbound.send(world_list_payload())
        await send_save_panels()
