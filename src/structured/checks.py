"""检定生命周期与普通掷骰（协议 §6.2、§3.4）。

check_request 创建即持久化；恰好结算一次；执行时复核控制权与相关条件。
普通骰不推进世界 revision、不触发剧情。骰点用 src/gameplay/percentile 的
共享实现（与战斗层同一套百分骰规则）。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.gameplay.percentile import REQUIRED_RANKS, success_level
from src.storage.database import CheckRequest, PlayerRequest

from .domains import KEEPER, PUBLIC, CommandContext, CommandResult, EventSpec
from .errors import StructuredError
from .ids import new_row_id, new_stable_id

_DIFFICULTY_DIVISOR = {"regular": 1, "hard": 2, "extreme": 5}
_LEVEL_LABEL = {
    "critical": "critical_success",
    "extreme": "extreme_success",
    "hard": "hard_success",
    "regular": "regular_success",
    "failure": "failure",
    "fumble": "fumble",
}
_DICE_SPEC_RE = re.compile(r"^(\d{1,2})d(\d{1,3})([+-]\d{1,3})?$")

DICE_COUNT_LIMIT = 10
DICE_SIDES_LIMIT = 100
DICE_MODIFIER_LIMIT = 999


def parse_dice_spec(spec: str) -> tuple[int, int, int]:
    """受限表达式 NdM±K；超限制拒绝（协议 §3.4）。"""
    match = _DICE_SPEC_RE.match(spec.strip())
    if match is None:
        raise StructuredError("invalid_action", "掷骰表达式格式应为 NdM 或 NdM±K。")
    count, sides = int(match.group(1)), int(match.group(2))
    modifier = int(match.group(3) or 0)
    if not 1 <= count <= DICE_COUNT_LIMIT:
        raise StructuredError("invalid_action", "骰子个数需在 1–10 之间。")
    if not 2 <= sides <= DICE_SIDES_LIMIT:
        raise StructuredError("invalid_action", "骰面需在 2–100 之间。")
    if abs(modifier) > DICE_MODIFIER_LIMIT:
        raise StructuredError("invalid_action", "修正值超出限制。")
    return count, sides, modifier


def _get_check(session, world_id: str, check_request_id: str) -> CheckRequest:
    row = session.execute(
        select(CheckRequest).where(
            CheckRequest.world_id == world_id,
            CheckRequest.check_request_id == check_request_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise StructuredError("request_not_found", f"检定请求不存在：{check_request_id}")
    return row


def _skill_value(state: dict, investigator_id: str, skill: str) -> int:
    investigators = state.get("investigators")
    sheet = None
    if isinstance(investigators, dict) and investigator_id in investigators:
        sheet = investigators[investigator_id]
    elif isinstance(state.get("pc"), dict):
        sheet = state["pc"]
    if not isinstance(sheet, dict):
        raise StructuredError("object_not_found", f"调查员不存在：{investigator_id}")
    skills = sheet.get("skills", {})
    if not isinstance(skills, dict) or skill not in skills:
        # 不存在的技能不得按默认 50 落账（旧默认 50 是已修复的漏洞）。
        raise StructuredError("invalid_action", f"该调查员没有技能：{skill}")
    try:
        return int(skills[skill])
    except (TypeError, ValueError) as exc:
        raise StructuredError("invalid_action", f"技能值不可用：{skill}") from exc


def _conditions_snapshot(state: dict, investigator_id: str, target: Any) -> dict:
    return {
        "scene_id": str((state.get("current_scene") or {}).get("id") or ""),
        "investigator_id": investigator_id,
        "target": target if isinstance(target, dict) else None,
    }


def _check_conditions(state: dict, row: CheckRequest) -> None:
    conditions = row.conditions or {}
    scene_id = conditions.get("scene_id")
    current = str((state.get("current_scene") or {}).get("id") or "")
    if scene_id is not None and str(scene_id) != current:
        raise StructuredError(
            "check_conditions_changed", "场景已变化，该检定失效；请由守秘人重新请求。"
        )
    target = conditions.get("target")
    if isinstance(target, dict) and target.get("kind") == "npc":
        present = (state.get("current_scene") or {}).get("npcs_present", [])
        if str(target.get("id")) not in {str(value) for value in present}:
            raise StructuredError("check_conditions_changed", "检定对象已不在场，该检定失效。")


def cmd_request_check(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    if ctx.session is None:
        raise StructuredError("internal_error", "request_check 需要数据库会话。")
    investigator_id = str(payload.get("investigator_id") or "")
    skill = str(payload.get("skill") or "").strip()
    difficulty = str(payload.get("difficulty") or "regular")
    if not investigator_id or not skill:
        raise StructuredError("invalid_action", "request_check 需要 investigator_id 与 skill。")
    if difficulty not in _DIFFICULTY_DIVISOR:
        raise StructuredError("invalid_action", "difficulty 只支持 regular/hard/extreme。")
    bonus_penalty = payload.get("bonus_penalty", 0)
    if isinstance(bonus_penalty, bool) or not isinstance(bonus_penalty, int):
        raise StructuredError("invalid_action", "bonus_penalty 必须是整数。")
    bonus_penalty = max(-2, min(2, bonus_penalty))
    attempt = str(payload.get("attempt") or "").strip()[:200]
    if not attempt:
        raise StructuredError("invalid_action", "attempt 不能为空。")
    visibility = str(payload.get("visibility") or "public")
    if visibility not in {"public", "keeper"}:
        raise StructuredError("invalid_action", "visibility 只支持 public/keeper。")
    target = payload.get("target")
    # 权威角色状态取值：技能值在创建时就按卡校验，防止等待一张永远无法结算的卡。
    _skill_value(state, investigator_id, skill)

    check_request_id = new_stable_id("chk")
    row = CheckRequest(
        id=new_row_id("chkrow"),
        world_id=ctx.world_id,
        check_request_id=check_request_id,
        investigator_id=investigator_id,
        skill=skill,
        difficulty=difficulty,
        bonus_penalty=bonus_penalty,
        attempt=attempt,
        known_cost=str(payload.get("known_cost") or "")[:200],
        visibility=visibility,
        status="pending",
        conditions=_conditions_snapshot(state, investigator_id, target),
        result={},
        related_request_id=str(payload.get("related_request_id") or "")[:160],
        created_by=getattr(ctx.principal, "user_id", "") or None,
    )
    ctx.session.add(row)
    related = row.related_request_id
    if related:
        request = ctx.session.execute(
            select(PlayerRequest).where(
                PlayerRequest.world_id == ctx.world_id,
                PlayerRequest.request_id == related,
            )
        ).scalar_one_or_none()
        if request is not None and request.status in {"queued", "processing"}:
            request.status = "awaiting_player"
    ctx.session.flush()
    audience = dict(PUBLIC) if visibility == "public" else dict(KEEPER)
    return CommandResult(
        result={"status": "success", "check_request_id": check_request_id},
        events=[
            EventSpec(
                "check_requested",
                {
                    "check_request_id": check_request_id,
                    "investigator_id": investigator_id,
                    "skill": skill,
                    "difficulty": difficulty,
                    "bonus_penalty": bonus_penalty,
                    "attempt": attempt,
                    "known_cost": row.known_cost,
                    "visibility": visibility,
                },
                audience,
            )
        ],
    )


def resolve_pending_check(
    session,
    state: dict,
    world_id: str,
    check_request_id: str,
    ctx: CommandContext,
) -> tuple[CheckRequest, CommandResult]:
    """结算一张待检定卡（恰好一次）。玩家按钮与 keeper 命令共用此核。"""
    row = _get_check(session, world_id, check_request_id)
    if row.status == "resolved":
        raise StructuredError(
            "check_already_resolved", "该检定已被结算，不会重复掷骰。", retryable=False
        )
    if row.status != "pending":
        raise StructuredError("check_not_pending", f"该检定不在等待状态：{row.status}")
    _check_conditions(state, row)
    value = _skill_value(state, row.investigator_id, row.skill)
    threshold = max(1, value // _DIFFICULTY_DIVISOR[row.difficulty])
    roll, candidates = ctx.roll_d100(int(row.bonus_penalty))
    rank, level = success_level(roll, value)
    outcome = "success" if rank >= REQUIRED_RANKS[row.difficulty] else "failure"
    detail = f"{roll} ≤ {threshold}" if outcome == "success" else f"{roll} > {threshold}"
    result = {
        "roll": roll,
        "candidates": candidates,
        "target_value": threshold,
        "level": _LEVEL_LABEL[level],
        "outcome": outcome,
        "detail": detail,
    }
    row.status = "resolved"
    row.result = result
    row.resolved_at = datetime.now(UTC)
    if row.related_request_id:
        related = session.execute(
            select(PlayerRequest).where(
                PlayerRequest.world_id == world_id,
                PlayerRequest.request_id == row.related_request_id,
            )
        ).scalar_one_or_none()
        if related is not None and related.status == "awaiting_player":
            related.status = "processing"
    session.flush()
    audience = dict(PUBLIC) if row.visibility == "public" else dict(KEEPER)
    return row, CommandResult(
        result={"status": "success", "check_request_id": check_request_id, **result},
        events=[
            EventSpec(
                "check_resolved",
                {
                    "check_request_id": check_request_id,
                    "investigator_id": row.investigator_id,
                    "skill": row.skill,
                    "target_value": threshold,
                    "roll": roll,
                    "level": _LEVEL_LABEL[level],
                    "outcome": outcome,
                    "detail": detail,
                },
                audience,
            )
        ],
    )


def cmd_resolve_check(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    if ctx.session is None:
        raise StructuredError("internal_error", "resolve_check 需要数据库会话。")
    check_request_id = str(payload.get("check_request_id") or "")
    if not check_request_id:
        raise StructuredError("invalid_action", "缺少 check_request_id。")
    _row, outcome = resolve_pending_check(ctx.session, state, ctx.world_id, check_request_id, ctx)
    return outcome


def decline_pending_check(
    session, world_id: str, check_request_id: str, *, reason: str
) -> CommandResult:
    row = _get_check(session, world_id, check_request_id)
    if row.status != "pending":
        raise StructuredError("check_not_pending", f"该检定不在等待状态：{row.status}")
    row.status = "declined"
    row.resolved_at = datetime.now(UTC)
    if row.related_request_id:
        related = session.execute(
            select(PlayerRequest).where(
                PlayerRequest.world_id == world_id,
                PlayerRequest.request_id == row.related_request_id,
            )
        ).scalar_one_or_none()
        if related is not None and related.status == "awaiting_player":
            related.status = "processing"
    session.flush()
    audience = dict(PUBLIC) if row.visibility == "public" else dict(KEEPER)
    return CommandResult(
        result={"status": "success", "check_request_id": check_request_id, "decision": "decline"},
        events=[
            EventSpec(
                "check_cancelled",
                {"check_request_id": check_request_id, "reason": reason or "玩家放弃"},
                audience,
            )
        ],
    )


def roll_free(ctx: CommandContext, spec: str) -> tuple[dict, list[EventSpec]]:
    """普通掷骰：立即结算、不改变世界状态、事件固定注明“普通掷骰”。"""
    count, sides, modifier = parse_dice_spec(spec)
    values = [ctx.rng(sides) + 1 for _ in range(count)]
    total = sum(values) + modifier
    result = {
        "expression": spec.strip(),
        "dice": [{"sides": sides, "values": values}],
        "total": total,
        "modifier": modifier,
    }
    return result, [
        EventSpec("roll_resolved", {"expression": spec.strip(), **result, "note": "普通掷骰"})
    ]
