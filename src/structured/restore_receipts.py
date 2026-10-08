"""Owner-only cloud solo restoration audit; never an Agent domain command."""

import copy
import re
from dataclasses import dataclass

from sqlalchemy import select

from src.storage.database import GameCommand, World, WorldInvestigator, WorldMember

from .errors import StructuredError
from .ids import canonical_digest, new_row_id


@dataclass(frozen=True)
class OwnerRestoreRequest:
    user_id: str
    action_id: str
    slot_id: str
    expected_revision: int

    def __post_init__(self):
        if any(
            type(value) is not str or not value.strip() or len(value) > 160
            for value in (self.user_id, self.action_id, self.slot_id)
        ):
            raise StructuredError("invalid_action", "读档需要有效的操作者、行动编号与存档点。")
        if not re.fullmatch(r"slot_\d{3,}", self.slot_id):
            raise StructuredError("invalid_action", "存档点编号无效。")
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise StructuredError("invalid_action", "读档需要严格的当前世界版本整数。")

    @property
    def command_id(self) -> str:
        # Keep this control receipt separate from arbitrary domain command IDs.
        return f"restore:{canonical_digest(self.action_id)}"

    @property
    def payload(self) -> dict:
        return {"slot_id": self.slot_id, "expected_revision": self.expected_revision}


def authorize_owner_restore(session, context, request: OwnerRestoreRequest) -> tuple[str, ...]:
    world = session.get(World, context.world_id, with_for_update=True)
    if world is None or world.status != "active":
        raise StructuredError("unknown_world", "世界不存在或已归档，不能读档。")
    meta = world.metadata_json or {}
    if meta.get("execution_profile") != "structured_v1" or meta.get("play_mode") != "solo":
        raise StructuredError("invalid_action", "此通道只允许云端单人结构化世界读档。")
    members = session.scalars(
        select(WorldMember).where(WorldMember.world_id == context.world_id).with_for_update()
    ).all()
    if len(members) != 1 or members[0].role != "owner" or members[0].user_id != request.user_id:
        raise StructuredError("not_authorized", "只有单人世界的唯一房主可以读档。")
    claims = session.scalars(
        select(WorldInvestigator)
        .where(
            WorldInvestigator.world_id == context.world_id, WorldInvestigator.status == "claimed"
        )
        .with_for_update()
    ).all()
    if len(claims) != 1 or claims[0].controller_user_id != request.user_id:
        raise StructuredError("not_investigator_controller", "调查员控制关系不完整，不能直接读档。")
    return (str(claims[0].character_key),)


def existing_restore_receipt(session, context, request: OwnerRestoreRequest) -> dict | None:
    receipt = session.scalar(
        select(GameCommand).where(
            GameCommand.world_id == context.world_id, GameCommand.command_id == request.command_id
        )
    )
    if receipt is None:
        return None
    if receipt.principal != {"kind": "room_owner", "user_id": request.user_id}:
        raise StructuredError("not_authorized", "不能查询其他操作者的读档凭证。")
    if receipt.kind != "restore_save" or receipt.payload_digest != canonical_digest(
        request.payload
    ):
        raise StructuredError(
            "duplicate_request_conflict", "同一读档编号提交了不同内容，未再次回滚。"
        )
    return {**copy.deepcopy(receipt.result), "deduplicated": True}


def validate_owned_snapshot(snapshot: dict, investigator_ids: tuple[str, ...]) -> None:
    active = snapshot.get("active_investigator_id")
    roster = snapshot.get("investigators") or {}
    if (
        active not in investigator_ids
        or not isinstance(roster, dict)
        or not isinstance(roster.get(active), dict)
        or not isinstance(snapshot.get("pc"), dict)
    ):
        raise StructuredError(
            "invalid_action", "该存档早于调查员就绪或角色关系不兼容，请使用本场开局后的存档点。"
        )


def record_restore_receipt(
    session, context, request: OwnerRestoreRequest, result: dict, epoch: int
) -> None:
    session.add(
        GameCommand(
            id=new_row_id("cmd"),
            world_id=context.world_id,
            command_id=request.command_id,
            kind="restore_save",
            payload=request.payload,
            payload_digest=canonical_digest(request.payload),
            principal={"kind": "room_owner", "user_id": request.user_id},
            controller_epoch=epoch,
            status="committed",
            result=copy.deepcopy(result),
            revision=result["revision"],
            cause_id=request.action_id,
        )
    )
    session.flush()
