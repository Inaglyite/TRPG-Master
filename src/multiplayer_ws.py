"""Authoritative multiplayer WebSocket adapter and recovery boundary."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .asset_payload import enrich_pc_for_frontend
from .auth import authorize_world, validate_websocket_origin, websocket_user
from .characters import list_character_options
from .database import World, WorldInvestigator, WorldMember, session_scope, utcnow
from .investigators import public_investigator_roster, visible_clues_for_investigator
from .model_settings import ModelSettings
from .multiplayer import MultiplayerError, reserve_room_action, room_members
from .player_notes import PlayerNotesConflict, PlayerNotesStore
from .room_runtime import (
    ActionReservationError,
    GameRoom,
    RoomConnection,
    RoomDriverTransport,
    RoomEventHub,
    RoomManager,
)
from .runtime import RuntimeContext

UNSUPPORTED_ROOM_TYPES = frozenset(
    {"load", "quit", "world_switch", "switch_module", "turn_branch_create", "model_settings_update"}
)
OWNER_CONTROL_TYPES = frozenset(
    {"save", "save_delete", "save_create", "save_rename", "settle_case"}
)
MUTATING_TURN_TYPES = frozenset({"start", "continue", "save_load", "action", "turn_rewrite"})
OWNER_TURN_TYPES = frozenset({"start", "save_load", "turn_rewrite"})


@dataclass(frozen=True)
class MultiplayerWsDependencies:
    database_url: Callable[[], str]
    room_manager: Callable[[], RoomManager]
    active_model_settings: Callable[[], ModelSettings]
    engine_factory: Callable[[RuntimeContext], Any]
    run_ws_session: Callable[..., Awaitable[None]]
    list_modules: Callable[[], list[dict]]
    load_theme: Callable[[RuntimeContext], dict]
    model_settings_payload: Callable[[ModelSettings], dict]
    enrich_clues: Callable[[dict, dict | None, RuntimeContext | None], dict]
    project_root: Path
    runtime_root: Path


class MultiplayerWsController:
    def __init__(self, deps: MultiplayerWsDependencies):
        self.deps = deps

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_websocket_route("/ws/room", self.websocket)
        return router

    async def room_bootstrap(self, ws: WebSocket, room: GameRoom) -> None:
        engine = room.engine
        await ws.send_json(
            {
                "type": "module_list",
                "modules": self.deps.list_modules(),
                "active": engine.context.module_name,
                "world_id": engine.context.world_id,
                "module_name": engine.context.module_name,
            }
        )
        await ws.send_json(
            {
                "type": "character_list",
                **list_character_options(engine.context.module_name, context=engine.context),
            }
        )
        await ws.send_json({"type": "theme", "theme": self.deps.load_theme(engine.context)})
        await ws.send_json(
            self.deps.model_settings_payload(
                ModelSettings.validated(engine.narrative_model, engine.judgement_model)
            )
        )
        await ws.send_json({"type": "save_list", "saves": engine.list_saves()})

    def room_private_recovery_payload(self, room: GameRoom, user_id: str) -> dict:
        """Build recovery data visible only to one authenticated room member."""
        try:
            world_state = room.engine.context.world_store.load()
            controllers = world_state.get("investigator_controllers", {})
            investigators = world_state.get("investigators", {})
            investigator_id = controllers.get(user_id) if isinstance(controllers, dict) else None
            own_pc = (
                investigators.get(investigator_id, {}) if isinstance(investigators, dict) else {}
            )
            if not own_pc and user_id == room.owner_user_id:
                own_pc = world_state.get("pc", {})
            pc_data = enrich_pc_for_frontend(
                own_pc if isinstance(own_pc, dict) else {},
                room.engine.context,
            )
            clues_data = self.deps.enrich_clues(
                visible_clues_for_investigator(
                    world_state.get("clues_found", {}),
                    investigator_id,
                ),
                world_state,
                room.engine.context,
            )
        except Exception:
            investigator_id, pc_data, clues_data = None, {}, {}
        try:
            notes = PlayerNotesStore(
                room.engine.context.world_dir,
                user_id=user_id,
            ).load()
        except (OSError, TypeError, ValueError, RuntimeError):
            notes = {"text": "", "revision": 0}
        return {
            "investigator_id": investigator_id,
            "pc": pc_data,
            "clues": clues_data,
            "player_notes": notes,
        }

    async def room_full_recovery_payload(self, room: GameRoom, user_id: str) -> dict:
        """Build a public recovery image plus the requesting member's private state."""
        try:
            history = room.engine.turn_journal.public_history()
        except Exception:
            history = []
        try:
            state = room.engine.context.world_store.load()
            investigators = public_investigator_roster(state)
            active_investigator_id = state.get("active_investigator_id")
        except Exception:
            investigators = []
            active_investigator_id = None
        return {
            "type": "room_full_state",
            "status": room.status,
            "owner_user_id": room.owner_user_id,
            "current_actor_user_id": room.current_actor_user_id,
            "ready_user_ids": sorted(room.ready_users),
            "online_user_ids": sorted(room.connected_users),
            "latest_event_id": await room.hub.latest_event_id(),
            "history": history,
            "investigators": investigators,
            "active_investigator_id": active_investigator_id,
            "private_state": self.room_private_recovery_payload(room, user_id),
        }

    async def broadcast_room_state(self, room: GameRoom) -> None:
        connections = await room.hub.connection_snapshot()
        await room.hub.broadcast(
            {
                "type": "room_state",
                "status": room.status,
                "owner_user_id": room.owner_user_id,
                "current_actor_user_id": room.current_actor_user_id,
                "ready_user_ids": sorted(room.ready_users),
                "online_user_ids": sorted({item["user_id"] for item in connections}),
            }
        )

    def persist_room_control(self, room: GameRoom) -> None:
        with session_scope(self.deps.database_url()) as db_session:
            world = db_session.get(World, room.world_id)
            if world is None:
                return
            metadata = dict(world.metadata_json or {})
            metadata["room_status"] = room.status
            metadata["current_actor_user_id"] = room.current_actor_user_id
            world.metadata_json = metadata
            world.updated_at = utcnow()

    def room_roster(self, world_id: str) -> tuple[list[dict], set[str]]:
        """Return claimed investigators and every member required to be ready."""
        with session_scope(self.deps.database_url()) as db_session:
            playable_members = {
                member.user_id
                for member in db_session.query(WorldMember)
                .filter(WorldMember.world_id == world_id)
                .all()
                if member.role in {"owner", "player"}
            }
            claims = (
                db_session.query(WorldInvestigator)
                .filter_by(world_id=world_id, status="claimed")
                .all()
            )
            roster = [
                {
                    "investigator_id": claim.id,
                    "user_id": claim.controller_user_id,
                    "character_ref": dict(claim.character_ref or {}),
                }
                for claim in claims
                if claim.controller_user_id in playable_members
            ]
        return roster, playable_members

    async def retire_room_after_grace(
        self, world_id: str, room: GameRoom, idle_seconds: float
    ) -> None:
        await asyncio.sleep(idle_seconds)
        while not room.connected_users:
            if await self.deps.room_manager().remove_if_idle(world_id, idle_seconds=idle_seconds):
                if room.driver_transport is not None:
                    await room.driver_transport.close_input()
                if room.driver_task is not None:
                    try:
                        await room.driver_task
                    except (RuntimeError, asyncio.CancelledError):
                        pass
                return
            if not room.action_active:
                return
            # A turn keeps running when its last viewer disconnects. Retire as soon
            # as the authoritative commit releases the room action lock.
            await asyncio.sleep(0.5)

    async def report_room_driver_exit(self, room: GameRoom, task: asyncio.Task) -> None:
        room.release_action()
        if task.cancelled() or not room.connected_users:
            return
        try:
            error = task.exception()
        except (asyncio.CancelledError, RuntimeError):
            return
        if error is not None:
            await room.hub.broadcast(
                {
                    "type": "room_error",
                    "code": "room_driver_stopped",
                    "message": "房间运行时意外停止，请重新进入房间",
                }
            )

    async def websocket(self, ws: WebSocket):
        """Join one authoritative shared engine using the authenticated user session."""
        try:
            validate_websocket_origin(ws)
            user = websocket_user(ws, self.deps.database_url())
            if user is None:
                await ws.close(code=4401, reason="未登录或会话已过期")
                return
            world_id = str(ws.query_params.get("world_id") or "")
            if not world_id:
                await ws.close(code=4400, reason="缺少房间 ID")
                return
            role = authorize_world(self.deps.database_url(), user.id, world_id, "read")
            with session_scope(self.deps.database_url()) as db_session:
                world = db_session.get(World, world_id)
                if world is None or world.status != "active":
                    await ws.close(code=4404, reason="房间不存在")
                    return
                module_name = world.module_name
                owner_member = (
                    db_session.query(WorldMember)
                    .filter_by(world_id=world_id, role="owner")
                    .one_or_none()
                )
                if owner_member is None:
                    await ws.close(code=4403, reason="房间没有有效房主")
                    return
                owner_user_id = owner_member.user_id

            def create_room() -> GameRoom:
                context = RuntimeContext.create(
                    world_id,
                    module_name,
                    project_root=self.deps.project_root,
                    runtime_root=self.deps.runtime_root,
                )
                engine = self.deps.engine_factory(context)
                engine.configure_models(
                    self.deps.active_model_settings().narrative_model,
                    self.deps.active_model_settings().judgement_model,
                )
                engine.prepare_session()
                room = GameRoom(
                    world_id,
                    engine,
                    RoomEventHub(world_id),
                    owner_user_id,
                    current_actor_user_id=(
                        str((world.metadata_json or {}).get("current_actor_user_id") or "")
                        or owner_user_id
                    ),
                    status=str((world.metadata_json or {}).get("room_status") or "lobby"),
                )
                transport = RoomDriverTransport(room)
                room.driver_transport = transport
                return room

            room, created = await self.deps.room_manager().get_or_create(world_id, create_room)
            await ws.accept()
            connection_id = f"connection_{secrets.token_hex(12)}"
            await room.hub.attach(RoomConnection(connection_id, user.id, role, ws))
            first_user_connection = room.member_connected(user.id)
            if created:
                room.driver_task = asyncio.create_task(
                    self.deps.run_ws_session(
                        room.driver_transport, room.engine, user_id=owner_user_id
                    )
                )
                room.driver_task.add_done_callback(
                    lambda task: asyncio.create_task(self.report_room_driver_exit(room, task))
                )
            else:
                await self.room_bootstrap(ws, room)
            await ws.send_json(await self.room_full_recovery_payload(room, user.id))
            if first_user_connection:
                await room.hub.broadcast(
                    {
                        "type": "member_joined",
                        "user_id": user.id,
                        "role": role,
                    }
                )
            await self.broadcast_room_state(room)
        except Exception as exc:
            if ws.client_state.name != "DISCONNECTED":
                await ws.close(code=4403, reason=str(exc)[:120] or "连接被拒绝")
            return

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if websocket_user(ws, self.deps.database_url()) is None:
                    await ws.close(code=4401, reason="登录会话已过期")
                    break
                try:
                    role = authorize_world(self.deps.database_url(), user.id, world_id, "read")
                except Exception:
                    await ws.close(code=4403, reason="房间成员权限已被移除")
                    break
                message_type = str(data.get("type") or "")
                if message_type == "ping":
                    await ws.send_json({"type": "pong"})
                    continue
                if message_type == "room_ack":
                    accepted = await room.hub.acknowledge(
                        connection_id, int(data.get("event_id") or 0)
                    )
                    if not accepted:
                        await ws.send_json({"type": "protocol_error", "code": "invalid_room_ack"})
                    continue
                if message_type == "room_sync":
                    replay = await room.hub.replay_after(
                        connection_id, int(data.get("after_event_id") or 0)
                    )
                    if replay["gap"]:
                        await ws.send_json(
                            {
                                "type": "room_event_gap",
                                "latest_event_id": replay["latest_event_id"],
                            }
                        )
                        await ws.send_json(await self.room_full_recovery_payload(room, user.id))
                    else:
                        for event in replay["events"]:
                            await ws.send_json(event)
                    continue
                if message_type == "room_ready":
                    if role == "viewer":
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "player_required",
                                "message": "旁观者不能准备游戏",
                            }
                        )
                        continue
                    room.set_ready(user.id, bool(data.get("ready", True)))
                    await self.broadcast_room_state(room)
                    continue
                if message_type == "actor_assign":
                    if role != "owner":
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "owner_required",
                                "message": "只有房主可以指定行动者",
                            }
                        )
                        continue
                    target_user_id = str(data.get("user_id") or "")
                    member_state = room_members(self.deps.database_url(), world_id, user.id)
                    eligible = {
                        member["user_id"]
                        for member in member_state["members"]
                        if member["role"] in {"owner", "player"}
                    }
                    if target_user_id not in eligible:
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "invalid_actor",
                                "message": "目标成员不能成为行动者",
                            }
                        )
                        continue
                    room.assign_actor(target_user_id)
                    self.persist_room_control(room)
                    await room.hub.broadcast({"type": "actor_changed", "user_id": target_user_id})
                    await self.broadcast_room_state(room)
                    continue
                if message_type in {"player_notes_get", "player_notes_update"}:
                    notes_store = PlayerNotesStore(
                        room.engine.context.world_dir,
                        user_id=user.id,
                    )
                    try:
                        if message_type == "player_notes_get":
                            payload = {"type": "player_notes", **notes_store.load()}
                        else:
                            saved = notes_store.save(
                                data.get("text", ""),
                                expected_revision=(
                                    int(data["revision"])
                                    if data.get("revision") is not None
                                    else None
                                ),
                            )
                            payload = {"type": "player_notes", "saved": True, **saved}
                    except PlayerNotesConflict as exc:
                        payload = {
                            "type": "player_notes_conflict",
                            "message": str(exc),
                            **notes_store.load(),
                        }
                    except (OSError, TypeError, ValueError, RuntimeError) as exc:
                        payload = {
                            "type": "player_notes_error",
                            "message": str(exc) or "玩家笔记保存失败",
                        }
                    await ws.send_json(payload)
                    continue
                if message_type == "state":
                    try:
                        world_state = room.engine.context.world_store.load()
                        controllers = world_state.get("investigator_controllers", {})
                        investigators = world_state.get("investigators", {})
                        investigator_id = (
                            controllers.get(user.id) if isinstance(controllers, dict) else None
                        )
                        own_pc = (
                            investigators.get(investigator_id, {})
                            if isinstance(investigators, dict)
                            else {}
                        )
                        if not own_pc and user.id == room.owner_user_id:
                            own_pc = world_state.get("pc", {})
                        pc_data = enrich_pc_for_frontend(
                            own_pc if isinstance(own_pc, dict) else {},
                            room.engine.context,
                        )
                        clues_data = self.deps.enrich_clues(
                            visible_clues_for_investigator(
                                world_state.get("clues_found", {}),
                                investigator_id,
                            ),
                            world_state,
                            room.engine.context,
                        )
                    except Exception:
                        pc_data, clues_data = {}, {}
                    await ws.send_json(
                        {
                            "type": "state_data",
                            "data": json.dumps(pc_data, ensure_ascii=False),
                            "clues": json.dumps(clues_data, ensure_ascii=False),
                        }
                    )
                    continue
                if message_type == "turn_recovery_get":
                    await ws.send_json(await self.room_full_recovery_payload(room, user.id))
                    continue
                if message_type == "module_list":
                    await ws.send_json(
                        {
                            "type": "module_list",
                            "modules": self.deps.list_modules(),
                            "active": room.engine.context.module_name,
                        }
                    )
                    continue
                if message_type == "character_list":
                    await ws.send_json(
                        {
                            "type": "character_list",
                            **list_character_options(
                                room.engine.context.module_name,
                                context=room.engine.context,
                            ),
                        }
                    )
                    continue
                if message_type == "save_list":
                    await ws.send_json({"type": "save_list", "saves": room.engine.list_saves()})
                    continue
                if message_type == "model_settings_get":
                    await ws.send_json(
                        self.deps.model_settings_payload(
                            ModelSettings.validated(
                                room.engine.narrative_model,
                                room.engine.judgement_model,
                            )
                        )
                    )
                    continue
                if message_type == "turn_diagnostics_get":
                    if role != "owner":
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "owner_required",
                                "message": "只有房主可以查看回合诊断",
                            }
                        )
                    else:
                        await ws.send_json(
                            {
                                "type": "turn_diagnostics",
                                "diagnostics": room.engine.turn_diagnostics(data.get("turn_id")),
                            }
                        )
                    continue
                if message_type in {"suggest_reply", "decision_reply"}:
                    if room.current_actor_user_id != user.id:
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "not_current_actor",
                                "message": "只有当前行动者可以作出这个决定",
                            }
                        )
                        continue
                    reply_kind = "suggest" if message_type == "suggest_reply" else "decision"
                    if not room.accept_pending_reply(
                        reply_kind,
                        user.id,
                        request_id=data.get("decision_id"),
                    ):
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "stale_reply",
                                "message": "该确认请求已经失效或尚未发起",
                            }
                        )
                        continue
                if message_type in UNSUPPORTED_ROOM_TYPES:
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "unsupported_in_room",
                            "message": "该操作不能在共享房间中执行",
                        }
                    )
                    continue
                if message_type in OWNER_CONTROL_TYPES and role != "owner":
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "owner_required",
                            "message": "只有房主可以执行房间管理操作",
                        }
                    )
                    continue
                if message_type in OWNER_CONTROL_TYPES:
                    action_id = str(data.get("action_id") or "")
                    try:
                        await room.reserve_control(user.id, action_id)
                        reserve_room_action(
                            self.deps.database_url(),
                            world_id,
                            action_id,
                            user.id,
                            message_type,
                        )
                    except (ActionReservationError, MultiplayerError) as exc:
                        room.release_action()
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": exc.code,
                                "message": str(exc),
                            }
                        )
                        continue
                if message_type in MUTATING_TURN_TYPES:
                    if message_type in OWNER_TURN_TYPES and role != "owner":
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "owner_required",
                                "message": "只有房主可以执行该房间控制操作",
                            }
                        )
                        continue
                    roster, playable_members = self.room_roster(world_id)
                    claims_by_user = {
                        str(item["user_id"]): item for item in roster if item.get("user_id")
                    }
                    actor_id = room.current_actor_user_id or user.id
                    actor_claim = claims_by_user.get(actor_id)
                    if actor_claim is None:
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "investigator_required",
                                "message": "当前行动者需要先选择调查员",
                            }
                        )
                        continue
                    if message_type == "start":
                        if room.status != "lobby":
                            await ws.send_json(
                                {
                                    "type": "room_action_rejected",
                                    "code": "room_already_started",
                                    "message": "房间已经开始游戏",
                                }
                            )
                            continue
                        missing_claims = sorted(playable_members - claims_by_user.keys())
                        missing_ready = sorted(playable_members - room.ready_users)
                        missing_online = sorted(playable_members - room.connected_users.keys())
                        if missing_claims or missing_ready or missing_online:
                            await ws.send_json(
                                {
                                    "type": "room_action_rejected",
                                    "code": "room_not_ready",
                                    "message": "所有玩家选择调查员并准备后才能开始",
                                    "missing_claim_user_ids": missing_claims,
                                    "missing_ready_user_ids": missing_ready,
                                    "missing_online_user_ids": missing_online,
                                }
                            )
                            continue
                    action_id = str(data.get("action_id") or "")
                    try:
                        await room.reserve_action(
                            user.id,
                            action_id,
                            require_current_actor=message_type not in OWNER_TURN_TYPES,
                        )
                    except ActionReservationError as exc:
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": exc.code,
                                "message": str(exc),
                            }
                        )
                        continue
                    try:
                        reserve_room_action(
                            self.deps.database_url(),
                            world_id,
                            action_id,
                            user.id,
                            message_type,
                        )
                    except MultiplayerError as exc:
                        room.release_action()
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": exc.code,
                                "message": str(exc),
                            }
                        )
                        continue
                    if message_type == "start":
                        room.status = "playing"
                        self.persist_room_control(room)
                        await self.broadcast_room_state(room)
                        data["character_ref"] = actor_claim["character_ref"]
                        data["_room_roster"] = roster
                    data["_room_investigator_id"] = actor_claim["investigator_id"]
                    data["_room_actor_user_id"] = actor_id
                passthrough_types = (
                    MUTATING_TURN_TYPES
                    | OWNER_CONTROL_TYPES
                    | {
                        "suggest_reply",
                        "decision_reply",
                    }
                )
                if message_type not in passthrough_types:
                    await ws.send_json(
                        {
                            "type": "protocol_error",
                            "code": "unsupported_room_message",
                            "message_type": message_type,
                        }
                    )
                    continue
                data["_room_user_id"] = user.id
                data["_room_connection_id"] = connection_id
                try:
                    await room.driver_transport.submit(json.dumps(data, ensure_ascii=False))
                except Exception:
                    room.release_action()
                    raise
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await room.hub.detach(connection_id)
            last_user_connection = room.member_disconnected(user.id)
            if last_user_connection:
                await room.hub.broadcast({"type": "member_left", "user_id": user.id})
            await self.broadcast_room_state(room)
            if not room.connected_users:
                idle_seconds = max(0.0, float(os.environ.get("TRPG_ROOM_IDLE_SECONDS", "30")))
                asyncio.create_task(self.retire_room_after_grace(world_id, room, idle_seconds))
