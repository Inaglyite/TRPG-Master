"""Transactional creation of multiplayer worlds and their runtime storage."""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

from .database import User, World, WorldMember, new_id, session_scope
from .module_registry import ModuleRegistry
from .multiplayer import PLAY_MODES, MultiplayerError
from .runtime import RuntimeContext


def _max_worlds_per_user() -> int:
    try:
        return max(
            1,
            min(100, int(os.environ.get("TRPG_MAX_WORLDS_PER_USER", "8"))),
        )
    except ValueError:
        return 8


def _max_players(data: dict) -> int:
    try:
        value = int(data.get("max_players") or 4)
    except (TypeError, ValueError) as exc:
        raise MultiplayerError(
            "invalid_max_players",
            "玩家上限必须是 2–4 的整数",
        ) from exc
    if value < 2 or value > 4:
        raise MultiplayerError(
            "invalid_max_players",
            "玩家上限必须是 2–4 的整数",
        )
    return value


def _play_mode_and_max_players(data: dict) -> tuple[str, int]:
    """Validate the explicit play mode; solo worlds are capped at one member."""
    play_mode = str(data.get("play_mode") or "multiplayer").strip()
    if play_mode not in PLAY_MODES:
        raise MultiplayerError(
            "invalid_play_mode",
            "play_mode 必须是 solo 或 multiplayer",
            400,
        )
    if play_mode == "solo":
        raw_max_players = data.get("max_players")
        if raw_max_players is not None:
            try:
                requested = int(raw_max_players)
            except (TypeError, ValueError) as exc:
                raise MultiplayerError(
                    "invalid_play_mode",
                    "私密单人世界不支持设置玩家上限",
                    400,
                ) from exc
            if requested != 1:
                raise MultiplayerError(
                    "invalid_play_mode",
                    "私密单人世界不支持设置玩家上限",
                    400,
                )
        return play_mode, 1
    return play_mode, _max_players(data)


async def create_owned_world(
    *,
    database_url: str,
    creator_id: str,
    creator_username: str,
    data: dict,
    module_registry: ModuleRegistry,
    default_module_name: str,
    project_root: Path,
    runtime_root: Path,
) -> dict:
    """Create the control-plane rows before materializing runtime files."""
    module = str(data.get("module") or default_module_name).strip()
    try:
        module_record = module_registry.resolve(module)
    except (FileNotFoundError, ValueError) as exc:
        raise MultiplayerError("module_not_found", "模组不存在", 404) from exc
    play_mode, max_players = _play_mode_and_max_players(data)
    name = str(data.get("name") or "").strip()[:120]
    name = name or f"{creator_username} 的房间"
    world_id = f"world-{secrets.token_hex(12)}"

    with session_scope(database_url) as session:
        # Serialize quota checks for concurrent requests from one account.
        session.query(User).filter_by(id=creator_id).with_for_update().one()
        active_worlds = (
            session.query(World)
            .filter(
                World.created_by == creator_id,
                World.status.in_(("active", "pending")),
            )
            .count()
        )
        world_limit = _max_worlds_per_user()
        if active_worlds >= world_limit:
            raise MultiplayerError(
                "world_limit_reached",
                f"每个账号最多保留 {world_limit} 个房间",
                429,
            )
        metadata = {
            "name": name,
            "room_status": "lobby",
            "max_players": max_players,
            "play_mode": play_mode,
        }
        session.add(
            World(
                id=world_id,
                module_name=module,
                module_id=module_record.package_id,
                module_version=module_record.version,
                created_by=creator_id,
                status="pending",
                metadata_json=metadata,
            )
        )
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id=world_id,
                user_id=creator_id,
                role="owner",
            )
        )

    try:
        context = await asyncio.to_thread(
            RuntimeContext.create,
            world_id,
            module,
            project_root=project_root,
            runtime_root=runtime_root,
        )
    except Exception:
        # Retain a diagnosable, non-joinable control-plane row.
        with session_scope(database_url) as session:
            world = session.get(World, world_id)
            if world is not None:
                world.status = "failed"
        raise

    with session_scope(database_url) as session:
        world = session.get(World, world_id)
        if world is None:
            raise RuntimeError(f"world disappeared during creation: {world_id}")
        world.status = "active"
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "name": name,
            "room_status": "lobby",
            "max_players": max_players,
            "play_mode": play_mode,
        }
    return {"world_id": context.world_id, "module": module}
