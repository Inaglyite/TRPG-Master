"""Authoritative multiplayer membership, invitations, and investigator claims."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy.exc import IntegrityError

from .database import (
    AuditEvent,
    RoomAction,
    User,
    World,
    WorldInvestigator,
    WorldInvite,
    WorldMember,
    new_id,
    session_scope,
    utcnow,
)

MEMBER_ROLES = frozenset({"owner", "player", "viewer"})
INVITE_ROLES = frozenset({"player", "viewer"})
PLAY_MODES = frozenset({"solo", "multiplayer"})


def world_play_mode(metadata: dict | None) -> str:
    """Read the explicit play mode; legacy worlds without it are multiplayer."""
    return str((metadata or {}).get("play_mode") or "multiplayer")


@dataclass
class MultiplayerError(Exception):
    code: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_world(session, world_id: str) -> World:
    world = session.get(World, world_id)
    if world is None or world.status != "active":
        raise MultiplayerError("world_not_found", "房间不存在", 404)
    return world


def _require_member(session, world_id: str, user_id: str) -> WorldMember:
    member = session.query(WorldMember).filter_by(world_id=world_id, user_id=user_id).one_or_none()
    if member is None:
        raise MultiplayerError("not_a_member", "你不是该房间成员", 403)
    return member


def _require_owner(session, world_id: str, user_id: str) -> WorldMember:
    member = _require_member(session, world_id, user_id)
    if member.role != "owner":
        raise MultiplayerError("owner_required", "只有房主可以执行此操作", 403)
    return member


def _require_lobby_for_player_admission(world: World) -> None:
    room_status = str((world.metadata_json or {}).get("room_status") or "lobby")
    if room_status != "lobby":
        raise MultiplayerError(
            "room_already_started",
            "游戏开始后不能加入或提升为玩家",
            409,
        )


def _require_not_solo_world(world: World) -> None:
    if world_play_mode(world.metadata_json) == "solo":
        raise MultiplayerError(
            "solo_world",
            "私密单人世界不能邀请或加入",
            403,
        )


def create_invite(
    db_url: str,
    world_id: str,
    user_id: str,
    *,
    role: str = "player",
    expires_in_hours: int = 24,
    max_uses: int = 1,
) -> dict:
    if role not in INVITE_ROLES:
        raise MultiplayerError("invalid_role", "邀请角色必须是玩家或旁观者")
    expires_in_hours = max(1, min(int(expires_in_hours), 168))
    max_uses = max(1, min(int(max_uses), 16))
    token = secrets.token_urlsafe(24)
    now = utcnow()
    with session_scope(db_url) as session:
        world = _require_world(session, world_id)
        _require_not_solo_world(world)
        _require_owner(session, world_id, user_id)
        invite = WorldInvite(
            id=new_id("invite"),
            world_id=world_id,
            invited_by=user_id,
            token_hash=_hash_token(token),
            role=role,
            expires_at=now + timedelta(hours=expires_in_hours),
            max_uses=max_uses,
            used_count=0,
            created_at=now,
        )
        session.add(invite)
        session.flush()
        return {
            "invite_id": invite.id,
            "token": token,
            "world_id": world_id,
            "role": role,
            "expires_at": invite.expires_at.isoformat(),
            "max_uses": max_uses,
        }


def revoke_invite(db_url: str, world_id: str, invite_id: str, user_id: str) -> None:
    with session_scope(db_url) as session:
        _require_world(session, world_id)
        _require_owner(session, world_id, user_id)
        invite = session.get(WorldInvite, invite_id)
        if invite is None or invite.world_id != world_id:
            raise MultiplayerError("invite_not_found", "邀请不存在", 404)
        if invite.revoked_at is None:
            invite.revoked_at = utcnow()


def list_invites(db_url: str, world_id: str, user_id: str) -> dict:
    """List invitation metadata without ever returning stored token material."""
    now = utcnow()
    with session_scope(db_url) as session:
        _require_world(session, world_id)
        _require_owner(session, world_id, user_id)
        rows = (
            session.query(WorldInvite)
            .filter_by(world_id=world_id)
            .order_by(WorldInvite.created_at.desc(), WorldInvite.id.desc())
            .all()
        )
        invites = []
        for invite in rows:
            expires_at = invite.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            status = "active"
            if invite.revoked_at is not None:
                status = "revoked"
            elif expires_at <= now:
                status = "expired"
            elif invite.used_count >= invite.max_uses:
                status = "exhausted"
            invites.append(
                {
                    "invite_id": invite.id,
                    "role": invite.role,
                    "expires_at": expires_at.isoformat(),
                    "max_uses": invite.max_uses,
                    "used_count": invite.used_count,
                    "status": status,
                    "created_at": invite.created_at.isoformat(),
                }
            )
        return {"world_id": world_id, "invites": invites}


def accept_invite(db_url: str, token: str, user_id: str) -> dict:
    if not token or len(token) > 256:
        raise MultiplayerError("invite_invalid", "邀请码无效", 404)
    now = utcnow()
    with session_scope(db_url) as session:
        invite = (
            session.query(WorldInvite)
            .filter_by(token_hash=_hash_token(token))
            .with_for_update()
            .one_or_none()
        )
        if invite is None:
            raise MultiplayerError("invite_invalid", "邀请码无效", 404)
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if invite.revoked_at is not None:
            raise MultiplayerError("invite_revoked", "邀请已撤销", 410)
        if expires_at <= now:
            raise MultiplayerError("invite_expired", "邀请已过期", 410)
        existing = (
            session.query(WorldMember)
            .filter_by(world_id=invite.world_id, user_id=user_id)
            .one_or_none()
        )
        if existing is not None:
            return {"world_id": invite.world_id, "role": existing.role, "already_member": True}
        if invite.used_count >= invite.max_uses:
            raise MultiplayerError("invite_exhausted", "邀请使用次数已用完", 410)
        world = (
            session.query(World)
            .filter_by(id=invite.world_id, status="active")
            .with_for_update()
            .one_or_none()
        )
        if world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        _require_not_solo_world(world)
        # The world row serializes capacity and membership admission. Recheck
        # after acquiring it because the same account may accept two invites
        # concurrently in separate tabs.
        existing = (
            session.query(WorldMember)
            .filter_by(world_id=invite.world_id, user_id=user_id)
            .one_or_none()
        )
        if existing is not None:
            return {
                "world_id": invite.world_id,
                "role": existing.role,
                "already_member": True,
            }
        if invite.role == "player":
            _require_lobby_for_player_admission(world)
            max_players = max(
                2,
                min(int((world.metadata_json or {}).get("max_players") or 4), 4),
            )
            player_count = (
                session.query(WorldMember)
                .filter(
                    WorldMember.world_id == invite.world_id,
                    WorldMember.role.in_(("owner", "player")),
                )
                .count()
            )
            if player_count >= max_players:
                raise MultiplayerError("world_full", "房间玩家人数已满", 409)
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id=invite.world_id,
                user_id=user_id,
                role=invite.role,
            )
        )
        invite.used_count += 1
        return {"world_id": invite.world_id, "role": invite.role, "already_member": False}


def room_members(db_url: str, world_id: str, user_id: str) -> dict:
    with session_scope(db_url) as session:
        world = _require_world(session, world_id)
        _require_member(session, world_id, user_id)
        rows = (
            session.query(WorldMember, User)
            .join(User, User.id == WorldMember.user_id)
            .filter(WorldMember.world_id == world_id)
            .order_by(WorldMember.created_at, WorldMember.id)
            .all()
        )
        claims = {
            claim.controller_user_id: claim
            for claim in session.query(WorldInvestigator).filter_by(world_id=world_id).all()
            if claim.controller_user_id
        }
        return {
            "world_id": world_id,
            "module": world.module_name,
            "metadata": dict(world.metadata_json or {}),
            "members": [
                {
                    "user_id": member.user_id,
                    "username": account.username,
                    "role": member.role,
                    "investigator": (
                        {
                            "id": claims[member.user_id].id,
                            "character_key": claims[member.user_id].character_key,
                            "status": claims[member.user_id].status,
                        }
                        if member.user_id in claims
                        else None
                    ),
                }
                for member, account in rows
            ],
        }


def update_member_role(
    db_url: str, world_id: str, target_user_id: str, actor_user_id: str, role: str
) -> dict:
    if role not in {"player", "viewer"}:
        raise MultiplayerError("invalid_role", "成员角色必须是玩家或旁观者")
    with session_scope(db_url) as session:
        world = (
            session.query(World)
            .filter_by(id=world_id, status="active")
            .with_for_update()
            .one_or_none()
        )
        if world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        _require_not_solo_world(world)
        _require_owner(session, world_id, actor_user_id)
        target = _require_member(session, world_id, target_user_id)
        if target.role == "owner":
            raise MultiplayerError("owner_role_locked", "请使用房主移交功能", 409)
        if role == "player" and target.role != "player":
            _require_lobby_for_player_admission(world)
            max_players = max(
                2,
                min(int((world.metadata_json or {}).get("max_players") or 4), 4),
            )
            player_count = (
                session.query(WorldMember)
                .filter(
                    WorldMember.world_id == world_id,
                    WorldMember.role.in_(("owner", "player")),
                )
                .count()
            )
            if player_count >= max_players:
                raise MultiplayerError("world_full", "房间玩家人数已满", 409)
        target.role = role
        if role == "viewer":
            claim = (
                session.query(WorldInvestigator)
                .filter_by(world_id=world_id, controller_user_id=target_user_id)
                .one_or_none()
            )
            if claim:
                claim.controller_user_id = None
                claim.status = "available"
                claim.updated_at = utcnow()
        return {"user_id": target_user_id, "role": role}


def transfer_owner(
    db_url: str,
    world_id: str,
    target_user_id: str,
    actor_user_id: str,
) -> dict:
    if target_user_id == actor_user_id:
        raise MultiplayerError("already_owner", "该成员已经是房主", 409)
    with session_scope(db_url) as session:
        world = (
            session.query(World)
            .filter_by(id=world_id, status="active")
            .with_for_update()
            .one_or_none()
        )
        if world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        _require_not_solo_world(world)
        current = (
            session.query(WorldMember)
            .filter_by(world_id=world_id, user_id=actor_user_id)
            .with_for_update()
            .one_or_none()
        )
        if current is None or current.role != "owner":
            raise MultiplayerError("owner_required", "只有房主可以执行此操作", 403)
        target = _require_member(session, world_id, target_user_id)
        if target.role == "viewer":
            _require_lobby_for_player_admission(world)
            max_players = max(
                2,
                min(int((world.metadata_json or {}).get("max_players") or 4), 4),
            )
            player_count = (
                session.query(WorldMember)
                .filter(
                    WorldMember.world_id == world_id,
                    WorldMember.role.in_(("owner", "player")),
                )
                .count()
            )
            if player_count >= max_players:
                raise MultiplayerError(
                    "world_full",
                    "房主移交会超过房间玩家人数上限",
                    409,
                )
        current.role = "player"
        target.role = "owner"
        world.created_by = target_user_id
        world.updated_at = utcnow()
        return {
            "world_id": world_id,
            "previous_owner_user_id": actor_user_id,
            "owner_user_id": target_user_id,
        }


def _archive_world(
    db_url: str,
    world_id: str,
    actor_user_id: str,
    *,
    runtime_room_status: str | None,
    allow_active_solo: bool,
    reservation_action_id: str | None = None,
) -> dict:
    """Archive a world through one of the explicit product-level flows.

    ``allow_active_solo`` is intentionally private to this module: normal
    room deletion must still refuse an active game, while the dedicated
    "abandon this private solo adventure" flow may archive an *idle* solo
    game without pretending that it reached a narrative ending.  In both
    cases a durable running RoomAction is always a hard stop.
    """
    with session_scope(db_url) as session:
        world = session.query(World).filter_by(id=world_id).with_for_update().one_or_none()
        if world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        _require_owner(session, world_id, actor_user_id)
        if allow_active_solo and world_play_mode(world.metadata_json) != "solo":
            raise MultiplayerError(
                "solo_world_required",
                "只有云端单人冒险可以在进行中放弃",
                403,
            )
        if world.status == "archived":
            return {
                "world_id": world_id,
                "status": "archived",
                "already_archived": True,
                **({"abandoned": True} if allow_active_solo else {}),
            }
        # Durable authority: a running RoomAction row means a turn/control
        # operation is in flight. Checked only after the owner gate so that a
        # non-owner cannot distinguish busy from idle rooms.
        running_action = (
            session.query(RoomAction).filter_by(world_id=world_id, status="running").first()
        )
        room_status = runtime_room_status or str(
            (world.metadata_json or {}).get("room_status") or "lobby"
        )
        reservation = None
        if allow_active_solo:
            reservation = (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_id=str(reservation_action_id or ""))
                .with_for_update()
                .one_or_none()
            )
            if (
                reservation is None
                or reservation.status != "running"
                or reservation.submitted_by != actor_user_id
                or reservation.action_type != "solo_abandon"
            ):
                raise MultiplayerError(
                    "abandon_reservation_invalid",
                    "放弃请求已失效，请返回后重试",
                    409,
                )
            running_action = (
                session.query(RoomAction)
                .filter(
                    RoomAction.world_id == world_id,
                    RoomAction.status == "running",
                    RoomAction.action_id != reservation.action_id,
                )
                .first()
            )
        if running_action is not None or (
            not allow_active_solo and room_status in {"starting", "playing"}
        ):
            raise MultiplayerError(
                "room_active",
                (
                    "当前回合仍在处理，请稍后再放弃冒险"
                    if allow_active_solo
                    else "游戏进行中，请先结束当前游戏再删除房间"
                ),
                409,
            )
        now = utcnow()
        world.status = "archived"
        world.updated_at = now
        if reservation is not None:
            # The archive and its control lease commit together.  This is not
            # a game settlement: it merely closes the action used to serialize
            # a deliberate abandonment against turn writes.
            reservation.status = "completed"
        pending = (
            session.query(WorldInvite)
            .filter(
                WorldInvite.world_id == world_id,
                WorldInvite.revoked_at.is_(None),
            )
            .all()
        )
        for invite in pending:
            invite.revoked_at = now
        session.add(
            AuditEvent(
                id=new_id("audit"),
                user_id=actor_user_id,
                event_type="world_archived",
                world_id=world_id,
                success=True,
                details={
                    "room_status": room_status,
                    "invites_revoked": len(pending),
                    "archive_reason": (
                        "solo_abandoned" if allow_active_solo else "manual_delete"
                    ),
                    **(
                        {"action_id": reservation.action_id}
                        if reservation is not None
                        else {}
                    ),
                },
            )
        )
    return {
        "world_id": world_id,
        "status": "archived",
        **({"abandoned": True} if allow_active_solo else {}),
    }


def archive_world(
    db_url: str,
    world_id: str,
    actor_user_id: str,
    *,
    runtime_room_status: str | None = None,
) -> dict:
    """Logically delete an inactive world. Owner-only and idempotent.

    Keeps the world, turn, save, member and audit rows: flips ``worlds.status``
    to ``"archived"`` (which removes it from the normal room list and makes
    every world-scoped operation and WebSocket join fail with world_not_found),
    revokes every unredeemed invite, and writes a ``world_archived`` audit
    event in the same transaction. Calling it again on an already-archived
    world is an idempotent success; a missing world row is world_not_found.

    Owner authorization always runs before any activity check, so a non-owner
    cannot probe whether the room is currently busy. Activity is judged by the
    durable control plane (a running ``RoomAction`` row) plus the live room
    status supplied by the caller (``runtime_room_status``), which covers a
    loaded room whose persisted metadata may be stale.
    """
    return _archive_world(
        db_url,
        world_id,
        actor_user_id,
        runtime_room_status=runtime_room_status,
        allow_active_solo=False,
    )


def abandon_solo_world(
    db_url: str,
    world_id: str,
    actor_user_id: str,
    *,
    reservation_action_id: str,
    runtime_room_status: str | None = None,
) -> dict:
    """Archive an idle, private solo world without settling the case.

    This is deliberately not a wrapper around ``engine.settle_case``: giving
    up an investigation must not award module completion/reputation or claim
    that the narrative reached one of its authored endings.  A concurrent
    turn/control operation remains forbidden so a partial engine write cannot
    race this archival transaction.
    """
    return _archive_world(
        db_url,
        world_id,
        actor_user_id,
        runtime_room_status=runtime_room_status,
        allow_active_solo=True,
        reservation_action_id=reservation_action_id,
    )


def check_solo_abandon_access(
    db_url: str,
    world_id: str,
    actor_user_id: str,
) -> dict:
    """Authorize a possible solo abandonment before touching a live room lock.

    The HTTP handler must not let a viewer temporarily reserve a loaded
    room's in-memory action lock just by guessing its world id.  This narrow
    preflight deliberately performs the same owner and play-mode checks that
    the transactional abandon operation repeats later.
    """
    with session_scope(db_url) as session:
        world = session.query(World).filter_by(id=world_id).with_for_update().one_or_none()
        if world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        _require_owner(session, world_id, actor_user_id)
        if world_play_mode(world.metadata_json) != "solo":
            raise MultiplayerError(
                "solo_world_required",
                "只有云端单人冒险可以在进行中放弃",
                403,
            )
        if world.status == "archived":
            return {"world_id": world_id, "already_archived": True}
        return {"world_id": world_id, "already_archived": False}


def reserve_room_action(
    db_url: str,
    world_id: str,
    action_id: str,
    user_id: str,
    action_type: str,
    *,
    required_permission: str = "play",
) -> None:
    """Persist a durable running lease before an action reaches the engine."""
    if required_permission not in {"play", "manage"}:
        raise ValueError(f"未知房间行动权限: {required_permission}")
    try:
        with session_scope(db_url) as session:
            world = (
                session.query(World)
                .filter_by(id=world_id, status="active")
                .with_for_update()
                .one_or_none()
            )
            if world is None:
                raise MultiplayerError("world_not_found", "房间不存在", 404)
            member = (
                session.query(WorldMember)
                .filter_by(world_id=world_id, user_id=user_id)
                .with_for_update()
                .one_or_none()
            )
            if member is None:
                raise MultiplayerError("not_a_member", "你不是该房间成员", 403)
            if required_permission == "manage" and member.role != "owner":
                raise MultiplayerError("owner_required", "只有房主可以执行此操作", 403)
            if required_permission == "play" and member.role not in {"owner", "player"}:
                raise MultiplayerError("player_required", "旁观者不能提交行动", 403)
            # The world row lock above makes this a cross-process guard too.
            # GameRoom's in-memory lock handles the ordinary path, but a room
            # can be loading/recovering in another worker; never let two
            # distinct durable actions race past that boundary.
            other_running = (
                session.query(RoomAction)
                .filter(
                    RoomAction.world_id == world_id,
                    RoomAction.status == "running",
                    RoomAction.action_id != action_id,
                )
                .first()
            )
            if other_running is not None:
                raise MultiplayerError(
                    "room_turn_in_progress",
                    "房间正在处理上一项行动",
                    409,
                )
            existing = (
                session.query(RoomAction)
                .filter_by(world_id=world_id, action_id=action_id)
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                if existing.status != "failed":
                    raise MultiplayerError(
                        "duplicate_action",
                        "该行动已经提交",
                        409,
                    )
                existing.submitted_by = user_id
                existing.action_type = str(action_type or "action")[:40]
                existing.status = "running"
            else:
                session.add(
                    RoomAction(
                        id=new_id("room_action"),
                        world_id=world_id,
                        action_id=action_id,
                        submitted_by=user_id,
                        action_type=str(action_type or "action")[:40],
                        status="running",
                    )
                )
            session.flush()
    except MultiplayerError:
        raise
    except IntegrityError as exc:
        raise MultiplayerError("duplicate_action", "该行动已经提交", 409) from exc


def finish_room_action(
    db_url: str,
    world_id: str,
    action_id: str,
    status: str,
) -> None:
    """Mark the durable action lease after its authoritative terminal event."""
    if status not in {"completed", "failed", "unknown"}:
        raise ValueError(f"非法房间行动状态: {status}")
    with session_scope(db_url) as session:
        row = (
            session.query(RoomAction)
            .filter_by(world_id=world_id, action_id=action_id)
            .with_for_update()
            .one_or_none()
        )
        if row is not None and row.status in {"accepted", "running"}:
            row.status = status


def recover_room_actions(db_url: str, world_id: str) -> int:
    """Fail closed when a crash obscures whether an action already committed."""
    with session_scope(db_url) as session:
        rows = (
            session.query(RoomAction)
            .filter(
                RoomAction.world_id == world_id,
                RoomAction.status == "running",
            )
            .with_for_update()
            .all()
        )
        for row in rows:
            row.status = "unknown"
        return len(rows)


def remove_member(db_url: str, world_id: str, target_user_id: str, actor_user_id: str) -> None:
    with session_scope(db_url) as session:
        world = (
            session.query(World)
            .filter_by(id=world_id, status="active")
            .with_for_update()
            .one_or_none()
        )
        if world is None:
            raise MultiplayerError("world_not_found", "房间不存在", 404)
        actor = _require_member(session, world_id, actor_user_id)
        if actor_user_id != target_user_id and actor.role != "owner":
            raise MultiplayerError("owner_required", "只有房主可以移除其他成员", 403)
        target = _require_member(session, world_id, target_user_id)
        if target.role == "owner":
            raise MultiplayerError("owner_cannot_leave", "房主需要先移交房主身份", 409)
        claim = (
            session.query(WorldInvestigator)
            .filter_by(world_id=world_id, controller_user_id=target_user_id)
            .one_or_none()
        )
        if claim:
            claim.controller_user_id = None
            claim.status = "available"
            claim.updated_at = utcnow()
        session.delete(target)


def claim_investigator(
    db_url: str,
    world_id: str,
    character_key: str,
    user_id: str,
    *,
    character_ref: dict | None = None,
) -> dict:
    character_key = str(character_key or "").strip()
    if not character_key or len(character_key) > 200:
        raise MultiplayerError("invalid_character", "调查员标识无效")
    try:
        with session_scope(db_url) as session:
            member = _require_member(session, world_id, user_id)
            if member.role not in {"owner", "player"}:
                raise MultiplayerError("player_required", "旁观者不能占用调查员", 403)
            world = _require_world(session, world_id)
            room_status = str((world.metadata_json or {}).get("room_status") or "lobby")
            if room_status != "lobby":
                raise MultiplayerError(
                    "room_already_started",
                    "游戏开始后不能更换调查员",
                    409,
                )
            existing_user_claim = (
                session.query(WorldInvestigator)
                .filter_by(world_id=world_id, controller_user_id=user_id)
                .one_or_none()
            )
            if existing_user_claim and existing_user_claim.character_key != character_key:
                existing_user_claim.controller_user_id = None
                existing_user_claim.status = "available"
                existing_user_claim.updated_at = utcnow()
            claim = (
                session.query(WorldInvestigator)
                .filter_by(world_id=world_id, character_key=character_key)
                .with_for_update()
                .one_or_none()
            )
            if claim and claim.controller_user_id not in {None, user_id}:
                raise MultiplayerError("investigator_taken", "该调查员已被其他玩家选择", 409)
            if claim is None:
                claim = WorldInvestigator(
                    id=new_id("investigator"),
                    world_id=world_id,
                    character_key=character_key,
                    character_ref=dict(character_ref or {}),
                    controller_user_id=user_id,
                    status="claimed",
                )
                session.add(claim)
            else:
                claim.controller_user_id = user_id
                claim.status = "claimed"
                if character_ref:
                    claim.character_ref = dict(character_ref)
                claim.updated_at = utcnow()
            session.flush()
            return {"id": claim.id, "character_key": character_key, "user_id": user_id}
    except IntegrityError as exc:
        raise MultiplayerError("investigator_taken", "该调查员已被其他玩家选择", 409) from exc


def release_investigator(
    db_url: str,
    world_id: str,
    investigator_id: str,
    user_id: str,
) -> dict:
    with session_scope(db_url) as session:
        member = _require_member(session, world_id, user_id)
        world = _require_world(session, world_id)
        room_status = str((world.metadata_json or {}).get("room_status") or "lobby")
        if room_status != "lobby":
            raise MultiplayerError(
                "room_already_started",
                "游戏开始后不能更换调查员",
                409,
            )
        claim = session.get(WorldInvestigator, investigator_id)
        if claim is None or claim.world_id != world_id:
            raise MultiplayerError("investigator_not_found", "调查员不存在", 404)
        if claim.controller_user_id != user_id and member.role != "owner":
            raise MultiplayerError("claim_owner_required", "不能释放其他玩家的调查员", 403)
        previous_controller_user_id = claim.controller_user_id
        claim.controller_user_id = None
        claim.status = "available"
        claim.updated_at = utcnow()
        return {
            "id": claim.id,
            "character_key": claim.character_key,
            "user_id": previous_controller_user_id,
        }
