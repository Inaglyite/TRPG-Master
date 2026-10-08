"""Explicit human condition records, not inferred healing or Agent authority."""

import copy

from src.gameplay.investigators import (
    investigator_entity,
    project_active_investigator,
    stable_investigator_id,
)

from .domains import CommandContext, CommandResult, EventSpec, _stat_projection
from .errors import StructuredError
from .validation import validate_command


def record_condition(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    if getattr(ctx.principal, "kind", "") != "keeper":
        raise StructuredError("keeper_required", "人物状态记录只允许已授权的人类主持执行。")
    if state.get("game_over"):
        raise StructuredError("invalid_action", "游戏已结算，不能改写结案人物状态。")
    validate_command("record_condition", payload)
    investigator_id = stable_investigator_id(state, payload["investigator_id"])
    sheet = investigator_entity(state, investigator_id)
    if sheet is None:
        raise StructuredError("object_not_found", "找不到该调查员。")
    conditions = sheet.get("conditions", [])
    if not isinstance(conditions, list) or any(not isinstance(c, str) for c in conditions):
        raise StructuredError("invalid_action", "角色状态记录格式不兼容，请先检查角色卡。")
    roster = (state.get("investigators") or {}).get(investigator_id)
    roster_conditions = roster.get("conditions", []) if isinstance(roster, dict) else conditions
    if not isinstance(roster_conditions, list) or any(
        not isinstance(c, str) for c in roster_conditions
    ):
        raise StructuredError("invalid_action", "角色状态副本格式不兼容，请先检查角色卡。")
    if set(roster_conditions) != set(conditions):
        raise StructuredError(
            "check_conditions_changed", "角色状态副本不一致，本工具不会覆盖旧伤势；请先核对存档。"
        )
    condition = payload["condition"]
    before = condition in conditions
    after = payload["operation"] == "add"
    if before != payload["expected_present"]:
        raise StructuredError(
            "check_conditions_changed", "该状态已变化，请重新核对当前记录后提交。", retryable=True
        )
    basis = payload["basis"].strip()
    if not basis:
        raise StructuredError("invalid_action", "必须填写人物状态变更的依据。")
    if condition == "dead" and not after:
        raise StructuredError(
            "invalid_action", "死亡不能通过本工具解除；如需回滚请读档或创建分支。"
        )
    if "dead" in conditions and not (condition == "dead" and after):
        raise StructuredError("invalid_action", "不能通过本工具改变已死亡调查员的伤势。")
    if condition == "dead" and after and not before and sheet.get("hp", 0) > 0:
        raise StructuredError("invalid_action", "请先核对并明确结算生命值，HP 归零后才能记录死亡。")
    if not after and before and condition in {"dying", "unconscious"} and sheet.get("hp", 0) <= 0:
        raise StructuredError("invalid_action", "HP 仍为 0，不能解除濒死或昏迷；请先结算恢复生命。")
    entry = {
        "investigator_id": investigator_id,
        "condition": condition,
        "before": before,
        "after": after,
        "basis": basis,
        "user_id": ctx.principal.user_id,
    }
    # The persisted GameCommand retains principal, exact before/after and basis.
    # No-op records do not create a clinical fact, spend resources or invalidate
    # otherwise valid combat consent. Replays use the same command receipt.
    if before == after:
        return CommandResult(
            result={"status": "not_executed", "record": entry, "reason": "记录已是所选状态。"},
            bump_revision=False,
        )
    if after:
        if len(conditions) >= 64:
            raise StructuredError("invalid_action", "人物状态已达上限，不能再添加。")
        sheet["conditions"] = [*conditions, condition]
    else:
        sheet["conditions"] = [c for c in conditions if c != condition]
    project_active_investigator(state)
    projection = _stat_projection(sheet, investigator_id)
    return CommandResult(
        result={"status": "success", "record": entry},
        events=[
            EventSpec(
                "state_changed",
                projection,
                {"kind": "investigators", "investigator_ids": [investigator_id]},
            ),
            EventSpec("state_changed", copy.deepcopy(projection), {"kind": "keeper"}),
        ],
    )
