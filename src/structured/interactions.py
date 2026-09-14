"""当前交互线程（第 2 层上下文）：跨请求存活的「已讨论/已约定目标」。

设计边界（S3 取证结论的落地，见 docs/evidence/transition_real_model/S3_ROOT_CAUSE_FOR_BACKEND.md）：

- 线程是**记录**，不是执行授权：平台从不因线程存在而执行任何动作；
  是否执行由主持（人类/Agent）在下一轮按当时情境重新判断。
- 请求进入终态后线程仍可保留——玩家稍后的「那就过去」靠线程里的稳定
  目标 ID 承接，而不是让模型从自由文本里重新猜。
- 追问/坚持/取消/改目的地的理解由主持完成；线程操作是主持收尾请求时
  的显式参数（resolve_intent.thread），平台不加任何文本关键词规则。
- 玩家取消自己的等待中请求时，关联线程一并取消（状态联动，不是文本推断）。

读档 fail-closed：存档点之后创建/更新的线程按 branch.restore 的规则删除或
取消，不做半吊子恢复（与 player_requests 的既有契约一致）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from src.storage.database import EventOutbox, InteractionThread, utcnow

from .errors import StructuredError
from .ids import new_row_id, new_stable_id

OPEN = "open"
TERMINAL = {"completed", "cancelled", "superseded"}

_PENDING_KINDS = {"freeform", "move", "present_clue", "use_item", "other"}
_WAITING_ON_KINDS = {"", "party", "keeper"}  # 其余值按调查员 ID 处理（只限长）
_MAX_REQUEST_IDS = 20


def normalize_pending_action(raw: Any) -> dict:
    """校验并归一化「尚未执行的行动」记录（与协议 schema 同形）。"""
    if not isinstance(raw, dict):
        raise StructuredError("invalid_action", "pending_action 必须是对象。")
    kind = str(raw.get("kind") or "other")
    if kind not in _PENDING_KINDS:
        raise StructuredError("invalid_action", "pending_action.kind 不合法。")
    pending: dict = {"kind": kind}
    for key, limit in (("note", 300), ("target", 160), ("destination_scene_id", 160)):
        value = str(raw.get(key) or "")[:limit]
        if value:
            pending[key] = value
    if (
        not pending.get("note")
        and not pending.get("destination_scene_id")
        and not pending.get("target")
    ):
        raise StructuredError("invalid_action", "必须说明尚未执行什么（pending_action）。")
    return pending


def normalize_disclosed(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise StructuredError("invalid_action", "disclosed 必须是字符串数组。")
    return [str(item)[:200] for item in raw if str(item).strip()][:8]


def _normalize_waiting_on(raw: Any) -> str:
    return str(raw or "")[:160]


def _next_sequence(session, world_id: str) -> int:
    current = session.execute(
        select(func.max(EventOutbox.sequence)).where(EventOutbox.world_id == world_id)
    ).scalar_one()
    return int(current or 0) + 1


def get_thread(session, world_id: str, thread_id: str) -> InteractionThread | None:
    return session.execute(
        select(InteractionThread).where(
            InteractionThread.world_id == world_id,
            InteractionThread.thread_id == thread_id,
        )
    ).scalar_one_or_none()


def list_open_threads(session, world_id: str) -> list[InteractionThread]:
    return (
        session.execute(
            select(InteractionThread)
            .where(
                InteractionThread.world_id == world_id,
                InteractionThread.status == OPEN,
            )
            .order_by(InteractionThread.created_sequence, InteractionThread.created_at)
        )
        .scalars()
        .all()
    )


def public_projection(row: InteractionThread) -> dict:
    """玩家公开投影（本人/主持可见）：不含内部修订号，绝不携带主持私密判断。"""
    return {
        "thread_id": row.thread_id,
        "status": row.status,
        "investigator_id": row.investigator_id,
        "pending_action": dict(row.pending_action or {}),
        "disclosed": list(row.disclosed or []),
        "waiting_on": row.waiting_on,
        **({"note": row.note} if row.note else {}),
        "origin_request_id": row.origin_request_id,
        "last_request_id": row.last_request_id,
    }


def thread_event(row: InteractionThread) -> dict:
    """interaction_updated 事件 payload（audience 由调用方按 owner 组装）。"""
    return public_projection(row)


def thread_event_with_audience(row: InteractionThread) -> tuple[str, dict, dict]:
    """interaction_updated 的三元组（type, payload, audience）：owner 定向，
    主持按 audience 规则总能看到；其他玩家收不到他人的交互细节。"""
    return (
        "interaction_updated",
        thread_event(row),
        {"kind": "investigators", "investigator_ids": [row.investigator_id]},
    )


def _event_for(row: InteractionThread) -> tuple[str, dict, dict]:
    return thread_event_with_audience(row)


def _open(
    session,
    world_id: str,
    *,
    investigator_id: str,
    pending_action: dict,
    disclosed: list[str],
    waiting_on: str,
    note: str,
    request_id: str,
    revision: int,
) -> InteractionThread:
    row = InteractionThread(
        id=new_row_id("thr_row"),
        world_id=world_id,
        thread_id=new_stable_id("thr"),
        investigator_id=investigator_id,
        status=OPEN,
        pending_action=pending_action,
        disclosed=disclosed,
        waiting_on=waiting_on,
        note=note[:500],
        origin_request_id=request_id,
        last_request_id=request_id,
        request_ids=[request_id] if request_id else [],
        created_revision=revision,
        updated_revision=revision,
        created_sequence=_next_sequence(session, world_id),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _touch_continue(
    row: InteractionThread,
    *,
    request_id: str,
    pending_action: dict | None,
    disclosed_add: list[str],
    waiting_on: str | None,
    note: str | None,
    revision: int,
) -> None:
    if request_id:
        row.last_request_id = request_id
        ids = list(row.request_ids or [])
        if request_id not in ids:
            ids.append(request_id)
        row.request_ids = ids[-_MAX_REQUEST_IDS:]
    if pending_action is not None:
        row.pending_action = pending_action
    if disclosed_add:
        merged = list(row.disclosed or [])
        for item in disclosed_add:
            if item not in merged:
                merged.append(item)
        row.disclosed = merged[:8]
    if waiting_on is not None:
        row.waiting_on = waiting_on
    if note:
        row.note = note[:500]
    row.updated_revision = revision
    row.updated_at = utcnow()


def close_thread(row: InteractionThread, *, status: str, note: str | None, revision: int) -> None:
    if status not in TERMINAL:
        raise StructuredError("invalid_action", f"线程终态不合法：{status}")
    row.status = status
    if note:
        row.note = note[:500]
    row.updated_revision = revision
    row.updated_at = utcnow()


def _require_open(session, world_id: str, thread_id: str) -> InteractionThread:
    if not thread_id:
        raise StructuredError("invalid_action", "继续/收尾线程必须给出 thread_id。")
    row = get_thread(session, world_id, thread_id)
    if row is None:
        raise StructuredError("thread_not_found", f"交互线程不存在：{thread_id}")
    if row.status != OPEN:
        raise StructuredError("invalid_action", f"交互线程已终态：{row.status}")
    return row


def apply_thread_action(
    session,
    world_id: str,
    *,
    request_row,
    thread_spec: dict,
    resolution: str,
    revision: int,
) -> tuple[list[tuple[str, dict, dict]], InteractionThread]:
    """resolve_intent 的显式线程操作。

    返回 (待追加的 interaction_updated 事件列表, 主线程)：open/replace 是
    新线程，continue/close 是被操作的既有线程。
    """
    if not isinstance(thread_spec, dict):
        raise StructuredError("invalid_action", "thread 必须是对象。")
    action = str(thread_spec.get("action") or "")
    if action not in {"open", "continue", "close", "replace"}:
        raise StructuredError(
            "invalid_action", "thread.action 只支持 open/continue/close/replace。"
        )
    request_id = str(getattr(request_row, "request_id", "") or "")
    investigator_id = str(getattr(request_row, "investigator_id", "") or "")
    note = str(thread_spec.get("note") or "")[:500]
    events: list[tuple[str, dict, dict]] = []

    if action in {"continue", "close", "replace"}:
        row = _require_open(session, world_id, str(thread_spec.get("thread_id") or ""))
    if action == "close":
        status = "completed" if resolution == "completed" else "cancelled"
        close_thread(row, status=status, note=note or None, revision=revision)
        events.append(_event_for(row))
        return events, row
    if action == "continue":
        pending = (
            normalize_pending_action(thread_spec.get("pending_action"))
            if thread_spec.get("pending_action") is not None
            else None
        )
        waiting = (
            _normalize_waiting_on(thread_spec.get("waiting_on"))
            if thread_spec.get("waiting_on") is not None
            else None
        )
        _touch_continue(
            row,
            request_id=request_id,
            pending_action=pending,
            disclosed_add=normalize_disclosed(thread_spec.get("disclosed")),
            waiting_on=waiting,
            note=note or None,
            revision=revision,
        )
        events.append(_event_for(row))
        return events, row
    if action == "replace":
        close_thread(row, status="superseded", note=note or None, revision=revision)
        events.append(_event_for(row))
    # open / replace 都创建新线程
    pending = normalize_pending_action(thread_spec.get("pending_action"))
    new_row = _open(
        session,
        world_id,
        investigator_id=investigator_id,
        pending_action=pending,
        disclosed=normalize_disclosed(thread_spec.get("disclosed")),
        waiting_on=_normalize_waiting_on(thread_spec.get("waiting_on")),
        note=note,
        request_id=request_id,
        revision=revision,
    )
    events.append(_event_for(new_row))
    return events, new_row


def auto_thread_for_awaiting(
    session,
    world_id: str,
    *,
    request_row,
    awaiting: dict,
    revision: int,
) -> InteractionThread:
    """awaiting_player 自动落线程：已有关联线程则延续，否则新开。

    这是过渡回合的默认记录路径：等待被挂起时，「尚未执行什么/已告知什么/
    在等谁」必须跨请求存活（请求日后终态也不能丢）。
    """
    payload = request_row.payload or {}
    linked = str((payload.get("awaiting") or {}).get("thread_id") or payload.get("thread_id") or "")
    pending = normalize_pending_action(awaiting.get("pending_action"))
    disclosed = normalize_disclosed(awaiting.get("disclosed"))
    note = str(awaiting.get("note") or "")[:500]
    if linked:
        row = get_thread(session, world_id, linked)
        if row is not None and row.status == OPEN:
            _touch_continue(
                row,
                request_id=str(request_row.request_id),
                pending_action=pending,
                disclosed_add=disclosed,
                waiting_on=str(request_row.investigator_id or ""),
                note=note or None,
                revision=revision,
            )
            return row
    return _open(
        session,
        world_id,
        investigator_id=str(request_row.investigator_id or ""),
        pending_action=pending,
        disclosed=disclosed,
        waiting_on=str(request_row.investigator_id or ""),
        note=note,
        request_id=str(request_row.request_id),
        revision=revision,
    )


def auto_complete_move_threads(
    session, world_id: str, *, destination_scene_id: str, revision: int
) -> list[tuple[str, dict, dict]]:
    """移动已提交后，把目标一致的开放移动线程收尾为 completed。

    这只是记录收尾（事实已经发生，线程标记不再悬着），不是反过来用线程
    触发移动——方向永远是「命令改变世界，线程只记录」。

    同时同步**关联请求**的待办投影（失效待办不能继续显示「尚未出发」）：
    - 纯移动请求（action.kind=move 且目的地一致、同调查员、由该线程发起）：
      请求置 completed+success，清除 awaiting 明细；
    - 更宽意图（freeform「前往并调查」等）：请求保持 awaiting_player，
      但已落实的移动部分从 pending_action 移除，换成「已抵达」的中性记录——
      抵达不代表调查完成，剩余事项由主持继续。
    关联性核对：只处理 payload.thread_id 指向**本次被收尾线程**的请求，
    碰巧处于同一目的地或同一场景的他人/他事一律不动。
    """
    from src.storage.database import PlayerRequest

    events: list[tuple[str, dict, dict]] = []
    for row in list_open_threads(session, world_id):
        pending = row.pending_action or {}
        if (
            str(pending.get("kind") or "") != "move"
            or str(pending.get("destination_scene_id") or "") != destination_scene_id
        ):
            continue
        close_thread(row, status="completed", note="已抵达目的地。", revision=revision)
        events.append(_event_for(row))
        linked_requests = (
            session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == world_id,
                    PlayerRequest.status == "awaiting_player",
                    PlayerRequest.investigator_id == row.investigator_id,
                )
            )
            .scalars()
            .all()
        )
        for request in linked_requests:
            payload = dict(request.payload or {})
            if str(payload.get("thread_id") or "") != row.thread_id:
                continue  # 只认线程级关联，不按目的地碰巧匹配
            awaiting = dict(payload.get("awaiting") or {})
            pending_action = awaiting.get("pending_action") or {}
            # 「尚未执行的就是这次移动」= 主持把待办结构化为 move 且目的地一致；
            # 更宽的意图（前往并调查等）应被主持结构化为非 move 的剩余事项，
            # 平台不解析自由文本、不替主持拆分意图。
            settles = (
                str(pending_action.get("kind") or "") == "move"
                and str(pending_action.get("destination_scene_id") or "") == destination_scene_id
            )
            if settles:
                request.status = "completed"
                request.outcome = "success"
                request.detail = "已抵达目的地。"
                payload.pop("awaiting", None)
                request.payload = payload
                request.updated_at = utcnow()
                events.append(
                    (
                        "action_status",
                        {
                            "request_id": request.request_id,
                            "status": "completed",
                            "outcome": "success",
                            "detail": "已抵达目的地。",
                        },
                        {"kind": "public"},
                    )
                )
            # 其余情况：请求保持 awaiting_player，由主持在下一轮更新剩余事项。
            # 其 pending_action 若不含「尚未出发」措辞，投影不产生自相矛盾。
    return events


def cancel_threads_for_request(
    session, world_id: str, *, request_id: str, revision: int
) -> list[tuple[str, dict, dict]]:
    """玩家取消自己的请求时，**该请求所发起**的开放线程一并取消（状态联动）。

    只匹配 origin_request_id：取消一条追问（last_request_id）不等于放弃整个
    交互——「回答/撤回一次追问不结束原行动」是线程生命周期的基本边界。
    """
    events: list[tuple[str, dict, dict]] = []
    for row in list_open_threads(session, world_id):
        if row.origin_request_id == request_id:
            close_thread(row, status="cancelled", note="玩家取消了相关请求。", revision=revision)
            events.append(_event_for(row))
    return events
