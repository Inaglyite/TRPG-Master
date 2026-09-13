"""世界创建与本地身份引导（协议 §2 execution_profile / keeper_mode）。

- 创建世界时校验并写入 execution_profile / keeper_mode；结构化世界的创建者
  自动获得 can_keeper（keeper 与 owner 分离，但创建者默认两者兼有，之后可
  分别授予/收回）。
- 本地无账号模式：隐式 ``local`` 操作者 = owner + can_keeper + 世界内全部
  调查员，使协议授权链路在本地与云端完全一致，没有第二套规则。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.database import User, WorldInvestigator, WorldMember, new_id, utcnow

from .errors import StructuredError

EXECUTION_PROFILES = frozenset({"legacy", "structured_v1"})
KEEPER_MODES = frozenset({"human", "assisted", "agent"})

LOCAL_OPERATOR_USER_ID = "local"


def validate_profile_metadata(execution_profile: str, keeper_mode: str) -> None:
    if execution_profile not in EXECUTION_PROFILES:
        raise StructuredError(
            "unsupported_protocol",
            f"未知 execution_profile：{execution_profile}",
            retryable=False,
        )
    if keeper_mode not in KEEPER_MODES:
        raise StructuredError(
            "unsupported_protocol", f"未知 keeper_mode：{keeper_mode}", retryable=False
        )


def apply_profile_metadata(metadata: dict, *, execution_profile: str, keeper_mode: str) -> dict:
    """校验并返回写入 profile 字段后的 metadata（不修改入参）。"""
    validate_profile_metadata(execution_profile, keeper_mode)
    merged = dict(metadata)
    merged["execution_profile"] = execution_profile
    merged["keeper_mode"] = keeper_mode
    return merged


def grant_keeper(session: Session, world_id: str, user_id: str) -> None:
    """把 keeper 授权授予已有成员（与 owner 角色独立）。"""
    member = session.execute(
        select(WorldMember).where(
            WorldMember.world_id == world_id,
            WorldMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    if member is None:
        raise StructuredError("not_authorized", "只能给房间成员授予守秘人权限。")
    if not member.can_keeper:
        member.can_keeper = True
        session.flush()


def ensure_local_operator(session: Session, world_id: str) -> None:
    """本地无账号世界：确保隐式 local 操作者存在且具备 owner + can_keeper。"""
    user = session.get(User, LOCAL_OPERATOR_USER_ID)
    if user is None:
        session.add(
            User(
                id=LOCAL_OPERATOR_USER_ID,
                username="local",
                password_hash="",
                created_at=utcnow(),
            )
        )
        session.flush()
    member = session.execute(
        select(WorldMember).where(
            WorldMember.world_id == world_id,
            WorldMember.user_id == LOCAL_OPERATOR_USER_ID,
        )
    ).scalar_one_or_none()
    if member is None:
        session.add(
            WorldMember(
                id=new_id("member"),
                world_id=world_id,
                user_id=LOCAL_OPERATOR_USER_ID,
                role="owner",
                can_keeper=True,
            )
        )
    else:
        changed = False
        if member.role != "owner":
            member.role = "owner"
            changed = True
        if not member.can_keeper:
            member.can_keeper = True
            changed = True
        if not changed:
            return
    session.flush()


def local_player_investigator_ids(session: Session, world_id: str, state: dict) -> tuple[str, ...]:
    """本地操作者的可控调查员：显式认领优先，否则世界内全部调查员。"""
    claimed = session.execute(
        select(WorldInvestigator.character_key).where(
            WorldInvestigator.world_id == world_id,
            WorldInvestigator.controller_user_id == LOCAL_OPERATOR_USER_ID,
            WorldInvestigator.status == "claimed",
        )
    ).scalars()
    claimed_ids = tuple(str(key) for key in claimed)
    if claimed_ids:
        return claimed_ids
    investigators = state.get("investigators")
    if isinstance(investigators, dict) and investigators:
        return tuple(sorted(str(key) for key in investigators))
    return ("pc",)
