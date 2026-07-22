"""Authoritative multiplayer membership, invitations, and investigator claims."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy.exc import IntegrityError

from .database import (
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
        _require_world(session, world_id)
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
        if invite.role == "player":
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
        _require_owner(session, world_id, actor_user_id)
        target = _require_member(session, world_id, target_user_id)
        if target.role == "owner":
            raise MultiplayerError("owner_role_locked", "请使用房主移交功能", 409)
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
        world = _require_world(session, world_id)
        current = _require_owner(session, world_id, actor_user_id)
        target = _require_member(session, world_id, target_user_id)
        current.role = "player"
        target.role = "owner"
        world.created_by = target_user_id
        world.updated_at = utcnow()
        return {
            "world_id": world_id,
            "previous_owner_user_id": actor_user_id,
            "owner_user_id": target_user_id,
        }


def reserve_room_action(
    db_url: str,
    world_id: str,
    action_id: str,
    user_id: str,
    action_type: str,
) -> None:
    """Persist idempotency before an action reaches the shared engine."""
    try:
        with session_scope(db_url) as session:
            _require_member(session, world_id, user_id)
            session.add(
                RoomAction(
                    id=new_id("room_action"),
                    world_id=world_id,
                    action_id=action_id,
                    submitted_by=user_id,
                    action_type=str(action_type or "action")[:40],
                    status="accepted",
                )
            )
            session.flush()
    except IntegrityError as exc:
        raise MultiplayerError("duplicate_action", "该行动已经提交", 409) from exc


def remove_member(db_url: str, world_id: str, target_user_id: str, actor_user_id: str) -> None:
    with session_scope(db_url) as session:
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


def release_investigator(db_url: str, world_id: str, investigator_id: str, user_id: str) -> None:
    with session_scope(db_url) as session:
        member = _require_member(session, world_id, user_id)
        claim = session.get(WorldInvestigator, investigator_id)
        if claim is None or claim.world_id != world_id:
            raise MultiplayerError("investigator_not_found", "调查员不存在", 404)
        if claim.controller_user_id != user_id and member.role != "owner":
            raise MultiplayerError("claim_owner_required", "不能释放其他玩家的调查员", 403)
        claim.controller_user_id = None
        claim.status = "available"
        claim.updated_at = utcnow()
