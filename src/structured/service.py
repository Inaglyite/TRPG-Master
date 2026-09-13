"""结构化命令服务：每命令一个短事务，提交成功后才返回待发布事件（协议 §8）。

与旧路径的边界：本服务不使用 GameEngine，也不进入整轮 ``turn_cache``；
直接在 ``world_states`` 行锁 + revision CAS 上做逐命令提交，命令记录、
状态变更与 outbox 事件在同一事务落库。模型/主持侧的失败只影响未提交的
后续命令，已提交命令不回滚、不重掷、不重扣、不重移动。
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.storage.database import (
    CheckRequest,
    EventOutbox,
    GameCommand,
    PlayerRequest,
    World,
    WorldState,
    session_scope,
    utcnow,
)
from src.storage.database_store import migrate_world_state
from src.storage.world_migrations import CURRENT_WORLD_SCHEMA_VERSION

from .checks import (
    cmd_request_check as _cmd_request_check,
)
from .checks import (
    cmd_resolve_check as _cmd_resolve_check,
)
from .checks import (
    decline_pending_check,
    resolve_pending_check,
    roll_free,
)
from .domains import COMMAND_HANDLERS, CommandContext, CommandResult, EventSpec
from .errors import StructuredError
from .ids import canonical_digest, new_row_id
from .principal import Principal, check_command_authority
from .registries import ensure_clue_registry, ensure_item_registry

_KIND_HANDLERS = {
    **COMMAND_HANDLERS,
    "request_check": _cmd_request_check,
    "resolve_check": _cmd_resolve_check,
}

_ACTION_KINDS = {"present_clue", "use_item", "move", "freeform"}

# 权限矩阵（schemas/structured-play/v1/permission-matrix.json）中玩家可直接调用
# 的命令：只能以自己控制的调查员身份发言，其余命令一律需要 keeper/agent 控制权。
_PLAYER_COMMANDS = {"publish_message"}


class StructuredPlayService:
    """structured_v1 世界的请求受理 + 命令执行 + 事件 outbox。"""

    def __init__(self, database_url: str, *, rng: Callable[[int], int] | None = None):
        self.database_url = database_url
        import secrets

        self._rng = rng or secrets.randbelow

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _locked_world(self, session: Session, world_id: str) -> tuple[World, WorldState, dict]:
        world = session.get(World, world_id)
        if world is None:
            raise StructuredError("unknown_world", f"世界不存在：{world_id}")
        profile = (world.metadata_json or {}).get("execution_profile", "legacy")
        if profile != "structured_v1":
            raise StructuredError(
                "profile_mismatch", "该世界是 legacy 模式，不走结构化协议。", retryable=False
            )
        row = session.get(WorldState, world_id, with_for_update=True)
        if row is None:
            raise StructuredError("unknown_world", f"世界状态缺失：{world_id}")
        state, _ = migrate_world_state(copy.deepcopy(row.state))
        return world, row, state

    def _next_sequence(self, session: Session, world_id: str) -> int:
        current = session.execute(
            select(func.max(EventOutbox.sequence)).where(EventOutbox.world_id == world_id)
        ).scalar_one()
        return int(current or 0) + 1

    def _append_events(
        self,
        session: Session,
        world_id: str,
        revision: int,
        cause_request_id: str,
        specs: list[EventSpec],
    ) -> list[dict]:
        envelopes: list[dict] = []
        sequence = self._next_sequence(session, world_id)
        for spec in specs:
            row = EventOutbox(
                world_id=world_id,
                sequence=sequence,
                revision=revision,
                event_type=spec.type,
                payload=spec.payload,
                audience=spec.audience,
                cause_request_id=cause_request_id or "",
            )
            session.add(row)
            session.flush()  # 取得全局 event_id；提交失败则整批随事务回滚
            envelopes.append(
                {
                    "protocol_version": 1,
                    "event_id": int(row.id),
                    "world_id": world_id,
                    "sequence": sequence,
                    "revision": revision,
                    "type": spec.type,
                    "cause_request_id": cause_request_id or None,
                    "payload": copy.deepcopy(spec.payload),
                    "audience": copy.deepcopy(spec.audience),
                }
            )
            sequence += 1
        return envelopes

    @staticmethod
    def _write_state(row: WorldState, state: dict, *, bump: bool) -> int:
        """写回状态并保持 state["revision"] 与行级 revision 列一致。

        DatabaseWorldStore.snapshot() 以 JSON 内的 revision 为准（迁移写回约定），
        两边不同步会让独立连接的读取者看到过期的世界版本。
        """
        if bump:
            row.revision = int(row.revision) + 1
        state["revision"] = int(row.revision)
        state["schema_version"] = CURRENT_WORLD_SCHEMA_VERSION
        row.state = state
        row.schema_version = CURRENT_WORLD_SCHEMA_VERSION
        row.updated_at = utcnow()
        return int(row.revision)

    @staticmethod
    def _check_revision(expected: int | None, current: int) -> None:
        if expected is None:
            return
        if int(expected) != int(current):
            raise StructuredError(
                "revision_conflict",
                f"世界版本已从 {expected} 变为 {current}，请刷新候选后重新提交。",
                retryable=True,
            )

    # ------------------------------------------------------------------
    # 主持命令
    # ------------------------------------------------------------------

    def execute_command(
        self,
        *,
        world_id: str,
        principal: Principal,
        kind: str,
        payload: dict,
        command_id: str,
        expected_revision: int | None,
        cause_id: str = "",
    ) -> dict:
        """执行一个主持命令：短事务提交状态+命令行+事件，提交后返回事件。"""
        handler = _KIND_HANDLERS.get(kind)
        if handler is None:
            raise StructuredError("invalid_action", f"未知命令：{kind}")
        digest = canonical_digest({"kind": kind, "payload": payload, "cause_id": cause_id})
        with session_scope(self.database_url) as session:
            _world, row, state = self._locked_world(session, world_id)
            # 稳定 ID 注册表在首个命令/请求时一次性迁移并随状态原子持久化
            # （注册表 ID 随机生成，若只在内存迁移而不落库，下次迁移会产生
            # 另一套 ID，导致请求与命令引用不同对象）。
            ensure_item_registry(state)
            ensure_clue_registry(state)
            existing = session.execute(
                select(GameCommand).where(
                    GameCommand.world_id == world_id,
                    GameCommand.command_id == command_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.payload_digest == digest:
                    return {
                        "status": existing.status,
                        "command_id": command_id,
                        "revision": int(existing.revision),
                        "result": copy.deepcopy(existing.result),
                        "events": [],
                        "deduplicated": True,
                    }
                raise StructuredError(
                    "duplicate_request_conflict",
                    "同一 command_id 提交了不同内容，已拒绝。",
                )
            self._check_revision(expected_revision, int(row.revision))
            if principal.kind == "player" and kind in _PLAYER_COMMANDS:
                epoch = 0  # 玩家直接命令不持有主持控制权；载荷级授权在下面复核
            else:
                epoch = check_command_authority(session, world_id, principal)
            self._authorize_payload(principal, kind, payload)
            ctx = CommandContext(
                world_id=world_id,
                principal=principal,
                cause_id=cause_id,
                session=session,
                rng=self._rng,
            )
            outcome = handler(state, payload, ctx)
            if not isinstance(outcome, CommandResult):
                raise StructuredError("internal_error", f"命令 {kind} 返回了非法结果。")
            # 命令卡的收尾事件：以 command_id 为键，让发起方的“主持操作”卡
            # 离开等待态（协议 §3.3：committed 附带领域结果）。domain_outcome
            # 只接受 success/failure/not_executed，非法值降级为 success。
            command_outcome = str(outcome.result.get("status") or "success")
            if command_outcome not in {"success", "failure", "not_executed"}:
                command_outcome = "success"
            ack_audience = (
                {"kind": "keeper"}
                if principal.kind in {"keeper", "agent"}
                else {
                    "kind": "investigators",
                    "investigator_ids": list(principal.investigator_ids),
                }
            )
            outcome.events.append(
                EventSpec(
                    "action_status",
                    {
                        "request_id": command_id,
                        "status": "completed",
                        "outcome": command_outcome,
                    },
                    ack_audience,
                )
            )
            self._write_state(row, state, bump=outcome.bump_revision)
            session.add(
                GameCommand(
                    id=new_row_id("cmdrow"),
                    world_id=world_id,
                    command_id=command_id,
                    kind=kind,
                    payload=copy.deepcopy(payload),
                    payload_digest=digest,
                    principal=principal.as_dict(),
                    controller_epoch=epoch,
                    status="committed",
                    result=copy.deepcopy(outcome.result),
                    cause_id=cause_id or "",
                    revision=int(row.revision),
                    created_at=utcnow(),
                )
            )
            # 事件因果标签：有上游请求归上游（玩家请求卡随之更新），否则归
            # 命令自身（让网关与客户端能按 command_id 回收命令的投递与重试）。
            effective_cause = cause_id or command_id
            envelopes = self._append_events(
                session, world_id, int(row.revision), effective_cause, outcome.events
            )
            revision_after = int(row.revision)
        # 事务在此已提交（session_scope 退出即 commit）；事件在提交成功后才返回发布。
        return {
            "status": "committed",
            "command_id": command_id,
            "revision": revision_after,
            "result": outcome.result,
            "events": envelopes,
        }

    def _authorize_payload(self, principal: Principal, kind: str, payload: dict) -> None:
        """载荷级授权：发言身份与调查员控制权在每次提交时复核。"""
        if kind == "publish_message":
            speaker = payload.get("speaker") or {}
            speaker_kind = speaker.get("kind")
            if speaker_kind == "investigator":
                speaker_id = str(speaker.get("id") or "")
                if speaker_id not in principal.investigator_ids and principal.kind != "keeper":
                    raise StructuredError(
                        "not_investigator_controller",
                        "不能以未获控制权的调查员身份发言。",
                    )
            elif principal.kind not in {"keeper", "agent"}:
                raise StructuredError("not_authorized", "玩家只能以自己控制的调查员身份发言。")

    # ------------------------------------------------------------------
    # 玩家请求
    # ------------------------------------------------------------------

    def submit_action_request(
        self,
        *,
        world_id: str,
        principal: Principal,
        request: dict,
    ) -> dict:
        """受理玩家结构化行动请求：持久化为 queued，进入主持待办。"""
        action = request.get("action") or {}
        kind = str(action.get("kind") or "")
        if kind not in _ACTION_KINDS:
            raise StructuredError("invalid_action", f"未知行动类型：{kind}")
        request_id = str(request.get("request_id") or "")
        investigator_id = str(request.get("investigator_id") or "")
        if not request_id or not investigator_id:
            raise StructuredError("invalid_action", "缺少 request_id / investigator_id。")
        if investigator_id not in principal.investigator_ids:
            raise StructuredError(
                "not_investigator_controller", "你不能控制该调查员。", retryable=False
            )
        digest = canonical_digest(request)
        with session_scope(self.database_url) as session:
            _world, row, state = self._locked_world(session, world_id)
            existing = self._find_request(session, world_id, request_id)
            if existing is not None:
                if existing.payload_digest != digest:
                    raise StructuredError(
                        "duplicate_request_conflict", "同一请求 ID 提交了不同内容，已拒绝。"
                    )
                if existing.status == "failed":
                    # §3.1：failed 可恢复失败；客户端用同一 request_id 同载荷
                    # 重发 ⇒ 回到 queued 重新进入待办（重新做事实检查）。
                    self._check_revision(request.get("expected_revision"), int(row.revision))
                    ensure_item_registry(state)
                    ensure_clue_registry(state)
                    self._fact_check_action(state, action, investigator_id)
                    self._write_state(row, state, bump=False)
                    existing.status = "queued"
                    existing.updated_at = utcnow()
                    envelopes = self._append_events(
                        session,
                        world_id,
                        int(row.revision),
                        request_id,
                        [
                            EventSpec("action_ack", {"request_id": request_id, "status": "queued"}),
                            EventSpec(
                                "intent_pending",
                                {
                                    "request_id": request_id,
                                    "investigator_id": investigator_id,
                                    "summary": self._request_summary(action),
                                },
                                {"kind": "keeper"},
                            ),
                        ],
                    )
                    return {
                        "request_id": request_id,
                        "status": "queued",
                        "events": envelopes,
                    }
                return {
                    "request_id": request_id,
                    "status": existing.status,
                    "events": [],
                    "deduplicated": True,
                }
            self._check_revision(request.get("expected_revision"), int(row.revision))
            # 事实检查用到的稳定 ID 注册表必须随状态持久化（不推进 revision），
            # 否则后续命令重新迁移会得到另一套随机 ID。
            ensure_item_registry(state)
            ensure_clue_registry(state)
            # 事实检查：失败即拒绝（不持久化、不进入待办）。
            self._fact_check_action(state, action, investigator_id)
            self._write_state(row, state, bump=False)  # 仅持久化注册表迁移，不推进版本
            session.add(
                PlayerRequest(
                    id=new_row_id("req"),
                    world_id=world_id,
                    request_id=request_id,
                    request_type="action_request",
                    investigator_id=investigator_id,
                    submitted_by=principal.user_id or None,
                    payload=copy.deepcopy(request),
                    payload_digest=digest,
                    status="queued",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            summary = self._request_summary(action)
            envelopes = self._append_events(
                session,
                world_id,
                int(row.revision),
                request_id,
                [
                    EventSpec("action_ack", {"request_id": request_id, "status": "queued"}),
                    EventSpec(
                        "intent_pending",
                        {
                            "request_id": request_id,
                            "investigator_id": investigator_id,
                            "summary": summary,
                        },
                        {"kind": "keeper"},
                    ),
                ],
            )
        return {"request_id": request_id, "status": "queued", "events": envelopes}

    def create_keeper_draft(
        self,
        *,
        world_id: str,
        summary: str,
        proposed_commands: list[dict],
        narration: str = "",
        related_request_id: str = "",
    ) -> dict:
        """assisted 草稿：只持久化 + 通知 keeper，不执行任何命令。

        草稿占用 player_requests（request_type=keeper_draft），批准/拒绝由
        resolve_draft 命令收尾；重复 draft_id 幂等返回。
        """
        from .ids import new_stable_id

        draft_id = new_stable_id("draft")
        digest = canonical_digest(
            {
                "summary": summary,
                "commands": proposed_commands,
                "narration": narration,
            }
        )
        with session_scope(self.database_url) as session:
            _world, row, _state = self._locked_world(session, world_id)
            session.add(
                PlayerRequest(
                    id=new_row_id("req"),
                    world_id=world_id,
                    request_id=draft_id,
                    request_type="keeper_draft",
                    investigator_id="",
                    submitted_by=None,
                    payload={
                        "draft": {
                            "summary": summary,
                            "commands": proposed_commands,
                            "narration": narration,
                        }
                    },
                    payload_digest=digest,
                    status="queued",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            envelopes = self._append_events(
                session,
                world_id,
                int(row.revision),
                draft_id,
                [
                    EventSpec(
                        "keeper_draft",
                        {
                            "draft_id": draft_id,
                            "kind": "commands" if proposed_commands else "narrative",
                            **({"request_id": related_request_id} if related_request_id else {}),
                            "summary": summary,
                            **(
                                {"proposed_commands": proposed_commands}
                                if proposed_commands
                                else {}
                            ),
                        },
                        {"kind": "keeper"},
                    )
                ],
            )
        return {"draft_id": draft_id, "status": "queued", "events": envelopes}

    def cancel_action_request(
        self,
        *,
        world_id: str,
        principal: Principal,
        request: dict,
    ) -> dict:
        """玩家取消自己 queued 的行动请求（§3.1）；已被主持接管的请求拒绝。"""
        request_id = str(request.get("request_id") or "")
        target_id = str(request.get("target_request_id") or "")
        if not request_id or not target_id:
            raise StructuredError("invalid_action", "缺少 request_id / target_request_id。")
        digest = canonical_digest(request)
        with session_scope(self.database_url) as session:
            _world, row, _state = self._locked_world(session, world_id)
            existing = self._find_request(session, world_id, request_id)
            if existing is not None:
                if existing.payload_digest == digest:
                    return {
                        "request_id": request_id,
                        "status": existing.status,
                        "events": [],
                        "deduplicated": True,
                    }
                raise StructuredError(
                    "duplicate_request_conflict", "同一请求 ID 提交了不同内容，已拒绝。"
                )
            self._check_revision(request.get("expected_revision"), int(row.revision))
            target = self._find_request(session, world_id, target_id)
            if target is None or target.request_type != "action_request":
                raise StructuredError("request_not_found", f"没有找到行动请求：{target_id}")
            if target.investigator_id not in principal.investigator_ids:
                raise StructuredError("not_investigator_controller", "只能取消自己调查员的请求。")
            if target.status != "queued":
                raise StructuredError(
                    "invalid_action",
                    f"请求已被主持接管（{target.status}），请等待处理或请守秘人收尾。",
                )
            target.status = "cancelled"
            target.detail = "玩家取消"
            target.updated_at = utcnow()
            session.add(
                PlayerRequest(
                    id=new_row_id("req"),
                    world_id=world_id,
                    request_id=request_id,
                    request_type="cancel_request",
                    investigator_id=target.investigator_id,
                    submitted_by=principal.user_id or None,
                    payload=copy.deepcopy(request),
                    payload_digest=digest,
                    status="completed",
                    outcome="not_executed",
                    detail=f"取消 {target_id}",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            envelopes = self._append_events(
                session,
                world_id,
                int(row.revision),
                request_id,
                [
                    EventSpec(
                        "action_status",
                        {
                            "request_id": target_id,
                            "status": "cancelled",
                            "outcome": "not_executed",
                            "detail": "玩家取消",
                        },
                    ),
                    EventSpec("action_ack", {"request_id": request_id, "status": "completed"}),
                ],
            )
        return {"request_id": request_id, "status": "completed", "events": envelopes}

    def submit_free_roll(
        self,
        *,
        world_id: str,
        principal: Principal,
        request: dict,
    ) -> dict:
        """普通掷骰：立即结算一次；不推进世界 revision，不触发剧情。"""
        request_id = str(request.get("request_id") or "")
        investigator_id = str(request.get("investigator_id") or "")
        spec = str(request.get("spec") or "")
        if not request_id or not investigator_id:
            raise StructuredError("invalid_action", "缺少 request_id / investigator_id。")
        if investigator_id not in principal.investigator_ids:
            raise StructuredError("not_investigator_controller", "你不能控制该调查员。")
        digest = canonical_digest(request)
        with session_scope(self.database_url) as session:
            _world, row, _state = self._locked_world(session, world_id)
            existing = self._find_request(session, world_id, request_id)
            if existing is not None:
                if existing.payload_digest != digest:
                    raise StructuredError(
                        "duplicate_request_conflict", "同一请求 ID 提交了不同内容，已拒绝。"
                    )
                # 同载荷重发：直接返回已保存的骰点，不再掷、不再产生权威事件。
                result = dict(existing.payload.get("roll_result") or {})
                return {
                    "request_id": request_id,
                    "status": "completed",
                    "result": result,
                    "events": [],
                    "deduplicated": True,
                }
            ctx = CommandContext(
                world_id=world_id, principal=principal, cause_id=request_id, rng=self._rng
            )
            result, event_specs = roll_free(ctx, spec)
            stored = copy.deepcopy(request)
            stored["roll_result"] = result
            session.add(
                PlayerRequest(
                    id=new_row_id("req"),
                    world_id=world_id,
                    request_id=request_id,
                    request_type="free_roll_request",
                    investigator_id=investigator_id,
                    submitted_by=principal.user_id or None,
                    payload=stored,
                    payload_digest=digest,
                    status="completed",
                    outcome="success",
                    detail="普通掷骰",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            envelopes = self._append_events(
                session,
                world_id,
                int(row.revision),
                request_id,
                [
                    *[
                        EventSpec(
                            spec_.type, {**spec_.payload, "request_id": request_id}, spec_.audience
                        )
                        for spec_ in event_specs
                    ],
                ],
            )
        return {
            "request_id": request_id,
            "status": "completed",
            "result": result,
            "events": envelopes,
        }

    def submit_check_response(
        self,
        *,
        world_id: str,
        principal: Principal,
        request: dict,
    ) -> dict:
        """玩家回应待检定卡：roll 由服务端恰好结算一次；decline 记为放弃。"""
        request_id = str(request.get("request_id") or "")
        check_request_id = str(request.get("check_request_id") or "")
        decision = str(request.get("decision") or "")
        if decision not in {"roll", "decline"}:
            raise StructuredError("invalid_action", "decision 只支持 roll/decline。")
        if not request_id or not check_request_id:
            raise StructuredError("invalid_action", "缺少 request_id / check_request_id。")
        digest = canonical_digest(request)
        with session_scope(self.database_url) as session:
            _world, row, state = self._locked_world(session, world_id)
            existing = self._find_request(session, world_id, request_id)
            if existing is not None:
                if existing.payload_digest == digest:
                    return {
                        "request_id": request_id,
                        "status": existing.status,
                        "events": [],
                        "deduplicated": True,
                    }
                raise StructuredError(
                    "duplicate_request_conflict", "同一请求 ID 提交了不同内容，已拒绝。"
                )
            check = session.execute(
                select(CheckRequest).where(
                    CheckRequest.world_id == world_id,
                    CheckRequest.check_request_id == check_request_id,
                )
            ).scalar_one_or_none()
            if check is None:
                raise StructuredError("request_not_found", f"检定请求不存在：{check_request_id}")
            if check.investigator_id not in principal.investigator_ids:
                raise StructuredError("not_investigator_controller", "这张检定卡指定给其他调查员。")
            ctx = CommandContext(
                world_id=world_id,
                principal=principal,
                cause_id=request_id,
                session=session,
                rng=self._rng,
            )
            if decision == "roll":
                _row_, outcome = resolve_pending_check(
                    session, state, world_id, check_request_id, ctx
                )
            else:
                outcome = decline_pending_check(
                    session, world_id, check_request_id, reason="玩家放弃"
                )
            # 检定结算可能改动状态（时间代价推进时钟）；按结果推进 revision。
            self._write_state(row, state, bump=outcome.bump_revision)
            session.add(
                PlayerRequest(
                    id=new_row_id("req"),
                    world_id=world_id,
                    request_id=request_id,
                    request_type="check_response",
                    investigator_id=check.investigator_id,
                    submitted_by=principal.user_id or None,
                    payload=copy.deepcopy(request),
                    payload_digest=digest,
                    status="completed",
                    outcome="success",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            envelopes = self._append_events(
                session, world_id, int(row.revision), request_id, outcome.events
            )
            result = outcome.result
        return {
            "request_id": request_id,
            "status": "completed",
            "result": result,
            "events": envelopes,
        }

    # ------------------------------------------------------------------
    # 查询：快照与事件补发（服务端过滤可见性）
    # ------------------------------------------------------------------

    def session_snapshot(self, *, world_id: str, principal: Principal) -> dict:
        is_keeper = principal.kind in {"keeper", "agent"}
        own = set(principal.investigator_ids)
        from .domains import _known_destinations, _public_targets

        with session_scope(self.database_url) as session:
            world = session.get(World, world_id)
            if world is None:
                raise StructuredError("unknown_world", f"世界不存在：{world_id}")
            row = session.get(WorldState, world_id)
            if row is None:
                raise StructuredError("unknown_world", f"世界状态缺失：{world_id}")
            state, _ = migrate_world_state(copy.deepcopy(row.state))
            meta = world.metadata_json or {}
            from src.storage.database import KeeperControl

            control = session.get(KeeperControl, world_id)
            requests = (
                session.execute(
                    select(PlayerRequest).where(
                        PlayerRequest.world_id == world_id,
                        PlayerRequest.status.in_(
                            ["queued", "processing", "awaiting_player", "paused", "failed"]
                        ),
                    )
                )
                .scalars()
                .all()
            )
            checks = (
                session.execute(
                    select(CheckRequest).where(
                        CheckRequest.world_id == world_id,
                        CheckRequest.status == "pending",
                    )
                )
                .scalars()
                .all()
            )
            cursor = self._next_sequence(session, world_id) - 1
            last_event_id = session.execute(
                select(func.max(EventOutbox.id)).where(EventOutbox.world_id == world_id)
            ).scalar_one()
            revision = int(row.revision)
            keeper_info = (
                {
                    "user_id": control.controller_id,
                    "mode": "agent" if control.controller_kind == "agent" else "human",
                }
                if control is not None and control.controller_kind in {"human", "agent"}
                else None
            )
            # 投影必须在会话内完成：会话提交后 ORM 属性即过期，惰性结果集
            # 在块外迭代会撞上失效的 identity map。
            pending_checks = [
                self._check_projection(check, is_keeper)
                for check in checks
                if is_keeper or check.visibility == "public" or check.investigator_id in own
            ]
            request_entries = [
                {
                    "request_id": req.request_id,
                    "status": req.status,
                    "summary": (
                        str((req.payload or {}).get("draft", {}).get("summary") or "")[:200]
                        if req.request_type == "keeper_draft"
                        else self._request_summary((req.payload or {}).get("action") or {})
                    ),
                }
                for req in requests
                if is_keeper or req.investigator_id in own
            ]

        clues = self._visible_clues(state, own, is_keeper)
        scene = state.get("current_scene") or {}
        scene_id = str(scene.get("id") or "")
        scene_name = str(scene.get("name") or scene_id) or None
        destinations = [
            {"id": scene_id_, "name": name}
            for scene_id_, name in sorted(_known_destinations(state).items())
        ]
        payload = {
            "revision": revision,
            "execution_profile": meta.get("execution_profile", "legacy"),
            "keeper_mode": meta.get("keeper_mode", "human"),
            "server_capabilities": self._capabilities(meta),
            "keeper": keeper_info,
            "scene": {"id": scene_id, "name": scene_name} if scene_id else None,
            "destinations": destinations,
            "investigator_id": sorted(own)[0] if own else None,
            "targets": _public_targets(state),
            "clues": clues,
            "items": self._visible_items(state, own, is_keeper),
            "requests": request_entries,
            "pending_checks": pending_checks,
            "cursor": {
                "event_id": int(last_event_id or 0),
                "revision": revision,
                "sequence": max(cursor, 0),
            },
        }
        return payload

    def replay_events(
        self, *, world_id: str, after_sequence: int, principal: Principal
    ) -> list[dict]:
        """断线按游标补发；每条事件按接收范围过滤后才投递。"""
        with session_scope(self.database_url) as session:
            rows = session.execute(
                select(EventOutbox)
                .where(
                    EventOutbox.world_id == world_id,
                    EventOutbox.sequence > int(after_sequence),
                )
                .order_by(EventOutbox.sequence)
            ).scalars()
            envelopes = []
            for row in rows:
                if not self._audience_visible(row.audience or {"kind": "public"}, principal):
                    continue
                envelopes.append(
                    {
                        "protocol_version": 1,
                        "event_id": int(row.id),
                        "world_id": world_id,
                        "sequence": int(row.sequence),
                        "revision": int(row.revision),
                        "type": row.event_type,
                        "cause_request_id": row.cause_request_id or None,
                        "payload": copy.deepcopy(row.payload),
                    }
                )
        return envelopes

    # ------------------------------------------------------------------
    # 事实检查与投影（内部）
    # ------------------------------------------------------------------

    def _fact_check_action(self, state: dict, action: dict, investigator_id: str) -> None:
        kind = action["kind"]
        if kind == "freeform":
            return
        if kind == "move":
            from .domains import _known_destinations

            destination = str(action.get("destination_scene_id") or "")
            known = _known_destinations(state)
            scenes = state.get("scene_catalog") or {}
            if destination not in scenes:
                raise StructuredError("unknown_target", f"目的地不存在：{destination}")
            if known and destination not in known:
                raise StructuredError("unknown_target", "该目的地对队伍未知；只能从已知出口选择。")
            return
        if kind == "present_clue":
            from .registries import ensure_clue_registry, find_item

            registry = ensure_clue_registry(state)
            clue_id = str(action.get("clue_id") or "")
            entry = registry["clues"].get(clue_id)
            if entry is None:
                raise StructuredError("object_not_found", "这条线索不在你的已知列表里。")
            if investigator_id not in entry.get("granted_to", []):
                raise StructuredError("not_authorized", "你并不知道这条线索。")
            presentation = action.get("presentation")
            if presentation == "original":
                item_id = action.get("physical_item_id")
                if not item_id:
                    raise StructuredError(
                        "presentation_requires_item", "展示原件必须选择实际持有的物品。"
                    )
                item = find_item(state, str(item_id))
                holder = (item or {}).get("holder") or {}
                if (
                    item is None
                    or holder.get("kind") != "investigator"
                    or str(holder.get("id")) != investigator_id
                    or int(item.get("quantity") or 0) <= 0
                ):
                    raise StructuredError("object_not_held", "你手上没有这件实物。")
            if presentation == "image":
                self._require_granted_clue_asset(state, clue_id, investigator_id)
            target = action.get("target") or {}
            self._fact_check_target(state, target)
            return
        if kind == "use_item":
            from .registries import find_item

            item = find_item(state, str(action.get("item_id") or ""))
            holder = (item or {}).get("holder") or {}
            if (
                item is None
                or holder.get("kind") != "investigator"
                or str(holder.get("id")) != investigator_id
            ):
                raise StructuredError("object_not_held", "该物品不在你身上。")
            quantity = action.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise StructuredError("invalid_action", "quantity 不合法。")
            if int(item.get("quantity") or 0) < quantity:
                raise StructuredError("object_not_held", "持有数量不足。")
            if (
                action.get("operation") == "custom"
                and not str(action.get("approach") or "").strip()
            ):
                raise StructuredError("invalid_action", "即兴用法必须写明做法。")
            target = action.get("target")
            if target:
                self._fact_check_target(state, target)
            return

    @staticmethod
    def _clue_asset_granted(state: dict, clue_id: str, investigator_id: str) -> bool:
        """线索关联图片素材是否已获准该调查员查看（legacy 揭示记录 + 命令授权）。"""
        from src.gameplay.handouts import resolve_handout_asset

        asset_id, asset = resolve_handout_asset(state, "clue", clue_id)
        if not asset_id or not isinstance(asset, dict) or not asset.get("file"):
            return False
        seen = state.get("seen_handout_assets") or {}
        seen_clues = seen.get("clues", []) if isinstance(seen, dict) else []
        if asset_id in seen_clues:
            return True
        for grant in state.get("asset_grants") or []:
            if (
                isinstance(grant, dict)
                and grant.get("asset_id") == asset_id
                and grant.get("investigator_id") == investigator_id
            ):
                return True
        return False

    def _require_granted_clue_asset(self, state: dict, clue_id: str, investigator_id: str) -> None:
        if not self._clue_asset_granted(state, clue_id, investigator_id):
            raise StructuredError(
                "presentation_requires_asset",
                "这条线索没有已获准你查看的图片素材；可改用说明方式出示。",
            )

    def _fact_check_target(self, state: dict, target: dict) -> None:
        kind = target.get("kind")
        if kind == "unresolved":
            return  # 未解析目标交给主持澄清，不直接执行
        if kind == "npc":
            present = (state.get("current_scene") or {}).get("npcs_present", [])
            if str(target.get("id")) not in {str(value) for value in present}:
                raise StructuredError(
                    "stale_target", "目标 NPC 不在当前场景，请刷新候选。", retryable=True
                )
            return
        if kind == "investigator":
            investigators = state.get("investigators") or {}
            if str(target.get("id")) not in investigators:
                raise StructuredError("unknown_target", "目标调查员不存在。")
            return
        if kind == "scene_object":
            return
        raise StructuredError("unknown_target", "目标类型不合法。")

    @staticmethod
    def _request_summary(action: dict) -> str:
        kind = action.get("kind")
        if kind == "present_clue":
            return f"出示线索 {action.get('clue_id')}（{action.get('presentation')}）"
        if kind == "use_item":
            return f"使用物品 {action.get('item_id')} ×{action.get('quantity')}"
        if kind == "move":
            return f"前往 {action.get('destination_scene_id')}"
        if kind == "freeform":
            return str(action.get("text") or "")[:80]
        return str(kind or "")

    def _visible_clues(self, state: dict, own: set[str], is_keeper: bool) -> list[dict]:
        from .registries import ensure_clue_registry

        registry = ensure_clue_registry(state)
        clues = []
        for entry in registry["clues"].values():
            granted = set(entry.get("granted_to") or [])
            if not is_keeper and granted and not (granted & own):
                continue
            category = str(entry.get("category") or "")
            if category not in {"investigation", "event", "task", "npc"}:
                category = "investigation"  # schema 固定四类；未知类别降级，不泄露原始键
            presentation = ["describe"]
            if is_keeper or any(
                self._clue_asset_granted(state, entry["clue_id"], own_id) for own_id in own
            ):
                presentation.append("image")
            clues.append(
                {
                    "id": entry["clue_id"],
                    "category": category,
                    "text": str(entry.get("text") or "")[:500] or "（内容待守秘人补充）",
                    "presentation": presentation,
                }
            )
        return sorted(clues, key=lambda clue: clue["id"])

    def _visible_items(self, state: dict, own: set[str], is_keeper: bool) -> list[dict]:
        from .domains import _inventory_projection

        items: list[dict] = []
        for investigator_id in sorted(own):
            for item in _inventory_projection(state, investigator_id):
                items.append({**item, "operations": []})
        return items

    def _check_projection(self, check: CheckRequest, is_keeper: bool) -> dict:
        push_for = str((check.conditions or {}).get("push_for") or "")
        payload = {
            "check_request_id": check.check_request_id,
            "investigator_id": check.investigator_id,
            "skill": check.skill,
            "difficulty": check.difficulty,
            "bonus_penalty": int(check.bonus_penalty),
            "attempt": check.attempt,
            "known_cost": check.known_cost,
            "visibility": check.visibility,
            **({"push_for": push_for} if push_for else {}),
        }
        return payload

    @staticmethod
    def _capabilities(meta: dict) -> dict:
        profile = meta.get("execution_profile", "legacy")
        structured = profile == "structured_v1"
        modes = meta.get("keeper_modes") or ["human", "assisted", "agent"]
        commands = [
            "move_party",
            "request_check",
            "resolve_check",
            "present_information",
            "grant_clue",
            "use_item",
            "transfer_item",
            "adjust_stat",
            "advance_time",
            "publish_message",
            "resolve_intent",
            "present_handout",
            "set_npc_presence",
            "record_fact",
        ]
        return {
            "protocol_version": 1,
            "structured_protocol": structured,
            "execution_profile": profile,
            "keeper_modes": modes if structured else [],
            "commands": commands if structured else [],
            "keeper_console": structured,
            "free_roll": structured,
            "assisted_draft": structured and "assisted" in modes,
            "agent_takeover": structured and "agent" in modes,
            "check_request": structured,
            "move_action": structured,
            "present_clue": structured,
            "use_item": structured,
        }

    @staticmethod
    def _audience_visible(audience: dict, principal: Principal) -> bool:
        kind = audience.get("kind")
        if kind == "public":
            return True
        if kind == "keeper":
            return principal.kind in {"keeper", "agent"}
        if kind == "investigators":
            allowed = {str(value) for value in audience.get("investigator_ids") or []}
            if principal.kind in {"keeper", "agent"}:
                return True
            return bool(allowed & set(principal.investigator_ids))
        return False

    @staticmethod
    def _find_request(session: Session, world_id: str, request_id: str) -> PlayerRequest | None:
        return session.execute(
            select(PlayerRequest).where(
                PlayerRequest.world_id == world_id,
                PlayerRequest.request_id == request_id,
            )
        ).scalar_one_or_none()


def audience_visible(audience: dict, principal: Principal) -> bool:
    """事件路由范围判定（投递前的服务端过滤，协议 §5.2）。"""
    return StructuredPlayService._audience_visible(audience, principal)


def wire_envelope(envelope: dict) -> dict:
    """上线信封：剥离路由 audience。

    audience 是服务端投递依据（含定向接收者列表），下发会泄露“谁收到了
    私密内容”，且不在事件 schema 的 envelope_base 里（unevaluatedProperties
    会判非法）。message 载荷内部的 audience 是消息自身属性，不受影响。
    """
    return {key: value for key, value in envelope.items() if key != "audience"}
