"""Authenticated multiplayer HTTP control-plane routes."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auth import audit, authorize_world, request_user
from .characters import list_character_options
from .database import World, WorldMember, new_id, session_scope
from .module_registry import ModuleRegistry
from .multiplayer import (
    MultiplayerError,
    accept_invite,
    claim_investigator,
    create_invite,
    list_invites,
    release_investigator,
    remove_member,
    revoke_invite,
    room_members,
    transfer_owner,
    update_member_role,
)
from .room_runtime import GameRoom, RoomManager
from .runtime import RuntimeContext


@dataclass(frozen=True)
class MultiplayerHttpDependencies:
    """Late-bound server state used by the multiplayer control plane."""

    database_url: Callable[[], str]
    room_manager: Callable[[], RoomManager]
    module_registry: ModuleRegistry
    default_module_name: str
    project_root: Path
    runtime_root: Path
    persist_room_control: Callable[[GameRoom], None]
    broadcast_room_state: Callable[[GameRoom], Awaitable[None]]


def _error(exc: MultiplayerError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.message, "code": exc.code},
        status_code=exc.status_code,
    )


def create_multiplayer_http_router(
    deps: MultiplayerHttpDependencies,
) -> APIRouter:
    router = APIRouter()

    def db_url() -> str:
        return deps.database_url()

    @router.get("/api/worlds")
    async def owned_worlds(request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        with session_scope(db_url()) as session:
            rows = (
                session.query(World, WorldMember)
                .join(WorldMember, WorldMember.world_id == World.id)
                .filter(WorldMember.user_id == user.id, World.status == "active")
                .all()
            )
            return {
                "worlds": [
                    {
                        "world_id": world.id,
                        "module": world.module_name,
                        "role": member.role,
                        "updated_at": world.updated_at.isoformat(),
                        "metadata": world.metadata_json,
                        "name": str((world.metadata_json or {}).get("name") or ""),
                        "status": str(
                            (world.metadata_json or {}).get("room_status") or "lobby"
                        ),
                        "max_players": int(
                            (world.metadata_json or {}).get("max_players") or 4
                        ),
                        "member_count": session.query(WorldMember)
                        .filter_by(world_id=world.id)
                        .count(),
                    }
                    for world, member in rows
                ]
            }

    @router.post("/api/worlds", status_code=201)
    async def create_world(data: dict, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        module = str(data.get("module") or deps.default_module_name)
        deps.module_registry.resolve(module)
        world_id = f"world-{secrets.token_hex(12)}"
        context = RuntimeContext.create(
            world_id,
            module,
            project_root=deps.project_root,
            runtime_root=deps.runtime_root,
        )
        with session_scope(db_url()) as session:
            world = session.get(World, world_id)
            world.created_by = user.id
            world.metadata_json = {
                **dict(world.metadata_json or {}),
                "name": str(data.get("name") or "").strip()[:120]
                or f"{user.username} 的房间",
                "room_status": "lobby",
                "max_players": max(2, min(int(data.get("max_players") or 4), 4)),
            }
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id=world_id,
                    user_id=user.id,
                    role="owner",
                )
            )
        audit(db_url(), "world_created", user_id=user.id, world_id=world_id)
        return {"world_id": context.world_id, "module": module}

    @router.post("/api/worlds/{world_id}/invites", status_code=201)
    async def create_world_invite(world_id: str, data: dict, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            result = create_invite(
                db_url(),
                world_id,
                user.id,
                role=str(data.get("role") or "player"),
                expires_in_hours=int(data.get("expires_in_hours") or 24),
                max_uses=int(data.get("max_uses") or 1),
            )
        except (TypeError, ValueError):
            return JSONResponse(
                {"detail": "邀请参数无效", "code": "invalid_invite"},
                status_code=400,
            )
        except MultiplayerError as exc:
            return _error(exc)
        audit(db_url(), "world_invite_created", user_id=user.id, world_id=world_id)
        return result

    @router.get("/api/worlds/{world_id}/invites")
    async def get_world_invites(world_id: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            return list_invites(db_url(), world_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)

    @router.delete("/api/worlds/{world_id}/invites/{invite_id}", status_code=204)
    async def delete_world_invite(world_id: str, invite_id: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            revoke_invite(db_url(), world_id, invite_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        audit(db_url(), "world_invite_revoked", user_id=user.id, world_id=world_id)

    @router.post("/api/invites/{token}/accept")
    async def join_world_by_invite(token: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            result = accept_invite(db_url(), token, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        audit(
            db_url(),
            "world_invite_accepted",
            user_id=user.id,
            world_id=result["world_id"],
        )
        return result

    @router.get("/api/worlds/{world_id}/members")
    async def get_world_members(world_id: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            return room_members(db_url(), world_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)

    @router.get("/api/worlds/{world_id}/investigators/options")
    async def get_world_investigator_options(world_id: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            authorize_world(db_url(), user.id, world_id, "read")
            with session_scope(db_url()) as db_session:
                world = db_session.get(World, world_id)
                if world is None or world.status != "active":
                    raise MultiplayerError("world_not_found", "房间不存在", 404)
                module_name = world.module_name
            context = RuntimeContext.create(
                world_id,
                module_name,
                project_root=deps.project_root,
                runtime_root=deps.runtime_root,
            )
            return list_character_options(module_name, context=context)
        except MultiplayerError as exc:
            return _error(exc)

    @router.patch("/api/worlds/{world_id}/members/{target_user_id}")
    async def patch_world_member(
        world_id: str,
        target_user_id: str,
        data: dict,
        request: Request,
    ):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            result = update_member_role(
                db_url(),
                world_id,
                target_user_id,
                user.id,
                str(data.get("role") or ""),
            )
        except MultiplayerError as exc:
            return _error(exc)
        room = await deps.room_manager().get(world_id)
        if room is not None:
            await room.hub.update_user_role(target_user_id, result["role"])
            if result["role"] == "viewer":
                room.set_ready(target_user_id, False)
                if room.current_actor_user_id == target_user_id:
                    room.assign_actor(room.owner_user_id)
                    deps.persist_room_control(room)
            await deps.broadcast_room_state(room)
        audit(
            db_url(),
            "world_member_role_changed",
            user_id=user.id,
            world_id=world_id,
            details={"target_user_id": target_user_id, "role": result["role"]},
        )
        return result

    @router.post("/api/worlds/{world_id}/owner")
    async def transfer_world_owner(world_id: str, data: dict, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        target_user_id = str(data.get("user_id") or "")
        try:
            result = transfer_owner(db_url(), world_id, target_user_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        room = await deps.room_manager().get(world_id)
        if room is not None:
            room.owner_user_id = target_user_id
            await room.hub.update_user_role(user.id, "player")
            await room.hub.update_user_role(target_user_id, "owner")
            if room.current_actor_user_id is None:
                room.assign_actor(target_user_id)
            deps.persist_room_control(room)
            await room.hub.broadcast(
                {
                    "type": "owner_changed",
                    "previous_owner_user_id": user.id,
                    "owner_user_id": target_user_id,
                }
            )
            await deps.broadcast_room_state(room)
        audit(
            db_url(),
            "world_owner_transferred",
            user_id=user.id,
            world_id=world_id,
            details={"owner_user_id": target_user_id},
        )
        return result

    @router.delete("/api/worlds/{world_id}/members/{target_user_id}", status_code=204)
    async def delete_world_member(world_id: str, target_user_id: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            remove_member(db_url(), world_id, target_user_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        room = await deps.room_manager().get(world_id)
        if room is not None:
            room.set_ready(target_user_id, False)
            if room.current_actor_user_id == target_user_id:
                room.assign_actor(room.owner_user_id)
                deps.persist_room_control(room)
            await room.hub.disconnect_user(target_user_id)
            room.connected_users.pop(target_user_id, None)
            await room.hub.broadcast(
                {"type": "member_removed", "user_id": target_user_id}
            )
            await deps.broadcast_room_state(room)
        audit(
            db_url(),
            "world_member_removed",
            user_id=user.id,
            world_id=world_id,
            details={"target_user_id": target_user_id},
        )

    @router.post("/api/worlds/{world_id}/investigators/claim")
    async def claim_world_investigator(world_id: str, data: dict, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        character_key = str(data.get("character_key") or "")
        try:
            with session_scope(db_url()) as db_session:
                world = db_session.get(World, world_id)
                if world is None:
                    raise MultiplayerError("world_not_found", "房间不存在", 404)
                module_name = world.module_name
            context = RuntimeContext.create(
                world_id,
                module_name,
                project_root=deps.project_root,
                runtime_root=deps.runtime_root,
            )
            options = list_character_options(module_name, context=context)
            selected = next(
                (
                    character
                    for group in options.get("groups", [])
                    for character in group.get("characters", [])
                    if str(character.get("id") or "") == character_key
                ),
                None,
            )
            if selected is None or not isinstance(selected.get("ref"), dict):
                raise MultiplayerError(
                    "invalid_character",
                    "调查员不在当前房间的角色列表中",
                )
            result = claim_investigator(
                db_url(),
                world_id,
                character_key,
                user.id,
                character_ref=selected["ref"],
            )
        except MultiplayerError as exc:
            return _error(exc)
        audit(
            db_url(),
            "investigator_claimed",
            user_id=user.id,
            world_id=world_id,
            details={"investigator_id": result["id"]},
        )
        return result

    @router.delete(
        "/api/worlds/{world_id}/investigators/{investigator_id}/claim",
        status_code=204,
    )
    async def release_world_investigator(
        world_id: str,
        investigator_id: str,
        request: Request,
    ):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            release_investigator(db_url(), world_id, investigator_id, user.id)
        except MultiplayerError as exc:
            return _error(exc)
        audit(
            db_url(),
            "investigator_released",
            user_id=user.id,
            world_id=world_id,
            details={"investigator_id": investigator_id},
        )

    return router
