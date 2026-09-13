"""Principal 解析与授权：身份只从服务端会话/成员表/委派记录解析。

客户端自报的 principal 或 controller_epoch 不作授权依据（协议 §5.1）。
房间管理角色（owner/player/viewer）与玩法授权（keeper、调查员控制权）是两个
维度：owner 不自动拥有主持秘密，keeper 不必占用调查员名额。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.database import KeeperControl, WorldInvestigator, WorldMember

from .errors import StructuredError


@dataclass(frozen=True)
class Principal:
    """服务端解析出的调用者。investigator_ids 是其控制的调查员。"""

    kind: str  # "player" | "keeper" | "agent"
    user_id: str = ""
    run_id: str = ""
    investigator_ids: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "user_id": self.user_id,
            "run_id": self.run_id,
            "investigator_ids": list(self.investigator_ids),
        }


def _member(session: Session, world_id: str, user_id: str) -> WorldMember | None:
    return session.execute(
        select(WorldMember).where(
            WorldMember.world_id == world_id,
            WorldMember.user_id == user_id,
        )
    ).scalar_one_or_none()


def controlled_investigators(session: Session, world_id: str, user_id: str) -> tuple[str, ...]:
    rows = session.execute(
        select(WorldInvestigator).where(
            WorldInvestigator.world_id == world_id,
            WorldInvestigator.controller_user_id == user_id,
            WorldInvestigator.status == "claimed",
        )
    ).scalars()
    return tuple(str(row.character_key) for row in rows)


def resolve_player_principal(session: Session, world_id: str, user_id: str) -> Principal:
    """玩家 principal：必须是房间成员；控制关系来自 WorldInvestigator。"""
    member = _member(session, world_id, user_id)
    if member is None or member.role not in {"owner", "player"}:
        raise StructuredError("not_authorized", "你不是该世界的玩家成员。")
    return Principal(
        kind="player",
        user_id=user_id,
        investigator_ids=controlled_investigators(session, world_id, user_id),
    )


def resolve_keeper_principal(session: Session, world_id: str, user_id: str) -> Principal:
    """主持 principal：需要独立的 keeper 授权（can_keeper），与 owner 无关。"""
    member = _member(session, world_id, user_id)
    if member is None or not member.can_keeper:
        raise StructuredError(
            "keeper_required", "你没有该世界的守秘人授权（keeper 与 owner 分别授予）。"
        )
    return Principal(kind="keeper", user_id=user_id)


def agent_principal(run_id: str) -> Principal:
    """Agent 运行时 principal：命令受理时再按 keeper_control 校验 epoch。"""
    if not run_id:
        raise StructuredError("not_authorized", "Agent 调用缺少 run_id。")
    return Principal(kind="agent", run_id=run_id)


def current_control(session: Session, world_id: str) -> KeeperControl:
    row = session.get(KeeperControl, world_id)
    if row is None:
        row = KeeperControl(world_id=world_id, controller_kind="none", controller_id="", epoch=0)
        session.add(row)
        session.flush()
    return row


def take_control(session: Session, world_id: str, keeper: Principal) -> KeeperControl:
    """人类主持接管/取得控制权：递增 epoch，旧 epoch 的迟到调用随之失效。"""
    if keeper.kind != "keeper":
        raise StructuredError("keeper_required", "只有获授权的守秘人可以取得控制权。")
    row = current_control(session, world_id)
    row.controller_kind = "human"
    row.controller_id = keeper.user_id
    row.epoch = int(row.epoch) + 1
    session.flush()
    return row


def bind_agent_control(session: Session, world_id: str, run_id: str) -> KeeperControl:
    row = current_control(session, world_id)
    row.controller_kind = "agent"
    row.controller_id = run_id
    row.epoch = int(row.epoch) + 1
    session.flush()
    return row


def check_command_authority(session: Session, world_id: str, principal: Principal) -> int:
    """主持命令的权限与控制权校验；返回受理时绑定的 controller_epoch。

    Agent 必须使用当前 epoch；人类 keeper 每次命令即时取得/确认控制权
    （单活动主持：另一人接管后旧 keeper 的命令被拒绝）。
    """
    control = current_control(session, world_id)
    if principal.kind == "agent":
        if control.controller_kind != "agent" or control.controller_id != principal.run_id:
            raise StructuredError(
                "controller_epoch_stale",
                "主持控制权已移交，本 Agent 运行实例的命令不再受理。",
            )
        return int(control.epoch)
    if principal.kind == "keeper":
        # 自称 keeper 不作数：每次命令都复核成员表里的 can_keeper 授权，
        # 使伪造的 keeper principal（例如仅有 owner 角色）在命令层同样被拒。
        member = _member(session, world_id, principal.user_id)
        if member is None or not member.can_keeper:
            raise StructuredError(
                "keeper_required", "你没有该世界的守秘人授权（keeper 与 owner 分别授予）。"
            )
        if control.controller_kind == "agent" or (
            control.controller_kind == "human"
            and control.controller_id
            and control.controller_id != principal.user_id
        ):
            raise StructuredError(
                "controller_epoch_stale",
                "主持控制权已由他人接管；请先取得控制权。",
            )
        if control.controller_kind == "none" or not control.controller_id:
            row = take_control(session, world_id, principal)
            return int(row.epoch)
        return int(control.epoch)
    raise StructuredError("keeper_required", "该命令需要守秘人权限。")
