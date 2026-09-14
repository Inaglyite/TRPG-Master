"""structured_v1 世界的分支与存档恢复（M4 生命周期）。

与旧路径的边界：
- 结构化世界没有 Turn 记录，WorldBranchService.create 的"从已完成回合分叉"
  必然失败。这里提供"从当前已提交状态分叉"：分支点是 WorldState 行锁下的
  当前 committed state（含 revision），不依赖回合快照。
- 分支复制控制面（metadata 的 execution_profile/keeper_mode/play_mode、
  WorldMember 含 can_keeper、WorldInvestigator 认领行——复制而非搬走）、
  非终局待办（player_requests/check_requests）与幂等账本（game_commands）；
  不复制 event_outbox（新世界游标从 0 开始，客户端靠 session_snapshot 全量
  重同步）与 keeper_control（懒建新行，分支以无人掌控开始，旧 agent epoch
  不跨界）。
- 恢复（restore_structured_save）：CAS 回滚 WorldState 到存档快照，并在
  同一事务内 reconcile 结构化表——非终态请求置 failed、pending 检定做废、
  outbox 中 revision 晚于存档点的事件删除；keeper_control 保留（epoch
  机制天然使迟到调用失效）。旧引擎的 engine.load 不做这些，因此结构化世界
  的读档必须走这里，禁止静默回滚。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from sqlalchemy import select

from src.app.config import AUTO_SAVE_SLOT
from src.app.runtime import RuntimeContext
from src.storage.database import (
    CharacterMemory,
    CheckRequest,
    EventOutbox,
    GameCommand,
    InteractionThread,
    PlayerRequest,
    World,
    WorldInvestigator,
    WorldMember,
    WorldState,
    new_id,
    session_scope,
    utcnow,
)
from src.storage.persistence import load_game_artifacts, save_game
from src.storage.player_notes import PlayerNotesStore

from .errors import StructuredError
from .gateway import world_modes
from .ids import new_row_id

# 分支携带的控制面 metadata 键（缺了这些，分支会静默退化为 legacy 世界）。
_CONTROL_PLANE_KEYS = ("execution_profile", "keeper_mode", "play_mode", "max_players", "name")

# 非终态请求：分支复制这些，恢复时把这些置 failed。
_OPEN_REQUEST_STATUSES = ("queued", "processing", "awaiting_player", "paused", "failed")


@dataclass
class StructuredBranch:
    context: RuntimeContext
    label: str
    source_world_id: str


def _source_world(session, world_id: str) -> World:
    world = session.get(World, world_id)
    if world is None:
        raise StructuredError("unknown_world", f"世界不存在：{world_id}")
    return world


def create_structured_branch(
    source_context: RuntimeContext,
    *,
    project_root,
    runtime_root,
    label: object = "",
    user_id: str | None = None,
) -> StructuredBranch:
    """从源结构化世界的当前已提交状态创建分支。"""
    from src.ai.skills.skill_pins import inherit_pins_for_branch
    from src.storage.world_branches import WorldBranchService, _inherited_root

    service = WorldBranchService(project_root, runtime_root)
    database_url = source_context.database_url

    with session_scope(database_url) as session:
        source_world = _source_world(session, source_context.world_id)
        profile, _keeper_mode = world_modes(source_world.metadata_json)
        if profile != "structured_v1":
            raise StructuredError(
                "profile_mismatch", "只有 structured_v1 世界可以从当前进度创建分支。"
            )
        source_metadata = dict(source_world.metadata_json or {})
        source_created_by = source_world.created_by

    # 与旧分支同一纪律：先锁定源世界行并冻结记忆 cutoff，再复制状态。
    memory_cutoff_at = service._capture_memory_cutoff(source_context)

    with session_scope(database_url) as session:
        row = session.get(WorldState, source_context.world_id, with_for_update=True)
        if row is None:
            raise StructuredError("unknown_world", "世界状态缺失。")
        branch_state = copy.deepcopy(row.state or {})
        branch_revision = int(row.revision)

    scene = branch_state.get("current_scene") or {}
    scene_name = scene.get("name") if isinstance(scene, dict) else ""
    branch_label = WorldBranchService._clean_label(label, f"分支 · {scene_name or '新的时间线'}")
    world_id = service._new_world_id(source_context.world_id)
    target_context: RuntimeContext | None = None

    try:
        target_context = RuntimeContext.create(
            world_id,
            source_context.module_name,
            project_root=service.project_root,
            runtime_root=service.runtime_root,
        )
        target_context.world_store.seed_from_snapshot(
            branch_state,
            expected_revision=target_context.world_store.revision,
        )
        target_context.sync_module_metadata()
        inherit_pins_for_branch(source_context, target_context)
        source_notes = PlayerNotesStore(source_context.world_dir, user_id=user_id).load()
        if source_notes.get("text"):
            PlayerNotesStore(target_context.world_dir, user_id=user_id).save(source_notes["text"])

        with session_scope(database_url) as session:
            world = _source_world(session, world_id)
            metadata = dict(world.metadata_json or {})
            for key in _CONTROL_PLANE_KEYS:
                if key in source_metadata:
                    metadata[key] = copy.deepcopy(source_metadata[key])
            metadata["display_name"] = branch_label
            metadata["branch"] = {
                "parent_world_id": source_context.world_id,
                "source_turn_id": None,  # 结构化分支不基于回合
                "source_world_revision": branch_revision,
                "created_at": memory_cutoff_at,
                "memory_cutoff_at": memory_cutoff_at,
            }
            world.metadata_json = metadata
            world.root_world_id = _inherited_root(source_context)
            world.created_by = source_created_by
            world.updated_at = utcnow()

            # 权限与认领：复制（不是搬走），源世界成员不受影响。
            members = (
                session.execute(
                    select(WorldMember).where(WorldMember.world_id == source_context.world_id)
                )
                .scalars()
                .all()
            )
            for member in members:
                session.add(
                    WorldMember(
                        id=new_id("member"),
                        world_id=world_id,
                        user_id=member.user_id,
                        role=member.role,
                        can_keeper=bool(member.can_keeper),
                    )
                )
            claims = (
                session.execute(
                    select(WorldInvestigator).where(
                        WorldInvestigator.world_id == source_context.world_id
                    )
                )
                .scalars()
                .all()
            )
            for claim in claims:
                session.add(
                    WorldInvestigator(
                        id=new_id("wi"),
                        world_id=world_id,
                        character_key=claim.character_key,
                        character_ref=copy.deepcopy(claim.character_ref or {}),
                        controller_user_id=claim.controller_user_id,
                        status=claim.status,
                    )
                )

            # 待办恢复：只复制非终态请求与待结算检定；终态历史不跨界。
            requests = (
                session.execute(
                    select(PlayerRequest).where(
                        PlayerRequest.world_id == source_context.world_id,
                        PlayerRequest.status.in_(_OPEN_REQUEST_STATUSES),
                    )
                )
                .scalars()
                .all()
            )
            for request in requests:
                session.add(
                    PlayerRequest(
                        id=new_row_id("req"),
                        world_id=world_id,
                        request_id=request.request_id,
                        request_type=request.request_type,
                        investigator_id=request.investigator_id,
                        submitted_by=request.submitted_by,
                        payload=copy.deepcopy(request.payload or {}),
                        payload_digest=request.payload_digest,
                        status=request.status,
                        outcome=request.outcome,
                        detail=request.detail,
                    )
                )
            checks = (
                session.execute(
                    select(CheckRequest).where(
                        CheckRequest.world_id == source_context.world_id,
                        CheckRequest.status == "pending",
                    )
                )
                .scalars()
                .all()
            )
            for check in checks:
                session.add(
                    CheckRequest(
                        id=new_row_id("chk"),
                        world_id=world_id,
                        check_request_id=check.check_request_id,
                        investigator_id=check.investigator_id,
                        skill=check.skill,
                        difficulty=check.difficulty,
                        bonus_penalty=int(check.bonus_penalty),
                        attempt=check.attempt,
                        known_cost=check.known_cost,
                        visibility=check.visibility,
                        status="pending",
                        conditions=copy.deepcopy(check.conditions or {}),
                        result=copy.deepcopy(check.result or {}),
                        related_request_id=check.related_request_id,
                        rule_version=check.rule_version,
                        created_by=check.created_by,
                    )
                )
            # 幂等账本服随分支：分支内同 command_id 重发仍返回原结果，不重复结算。
            commands = (
                session.execute(
                    select(GameCommand).where(GameCommand.world_id == source_context.world_id)
                )
                .scalars()
                .all()
            )
            for command in commands:
                session.add(
                    GameCommand(
                        id=new_row_id("cmd"),
                        world_id=world_id,
                        command_id=command.command_id,
                        kind=command.kind,
                        payload=copy.deepcopy(command.payload or {}),
                        payload_digest=command.payload_digest,
                        principal=copy.deepcopy(command.principal or {}),
                        controller_epoch=int(command.controller_epoch),
                        status=command.status,
                        result=copy.deepcopy(command.result or {}),
                        error_code=command.error_code,
                        cause_id=command.cause_id,
                        revision=int(command.revision),
                    )
                )
            # 当前交互线程：只复制开放线程（终态已是历史，由记忆覆盖）。
            # 线程 ID 原样保留，分支内同一 thread_id 续接同一段交互。
            threads = (
                session.execute(
                    select(InteractionThread).where(
                        InteractionThread.world_id == source_context.world_id,
                        InteractionThread.status == "open",
                    )
                )
                .scalars()
                .all()
            )
            for thread in threads:
                session.add(
                    InteractionThread(
                        id=new_row_id("thr"),
                        world_id=world_id,
                        thread_id=thread.thread_id,
                        investigator_id=thread.investigator_id,
                        status=thread.status,
                        pending_action=copy.deepcopy(thread.pending_action or {}),
                        disclosed=copy.deepcopy(thread.disclosed or []),
                        waiting_on=thread.waiting_on,
                        note=thread.note,
                        origin_request_id=thread.origin_request_id,
                        last_request_id=thread.last_request_id,
                        request_ids=copy.deepcopy(thread.request_ids or []),
                        created_revision=int(thread.created_revision),
                        updated_revision=int(thread.updated_revision),
                        created_sequence=int(thread.created_sequence),
                    )
                )
            # 角色记忆：分叉点的全部记忆（含已被取代的历史链）复制到分支——
            # 分叉前的共同经历两边共享；分叉后各自新增按 world_id 隔离，
            # 分支永远检索不到原世界后来的记忆。derivation_key 随行保留，
            # 分支内补建/重跑派生不会重复写入。
            memories = (
                session.execute(
                    select(CharacterMemory).where(
                        CharacterMemory.world_id == source_context.world_id
                    )
                )
                .scalars()
                .all()
            )
            for memory in memories:
                session.add(
                    CharacterMemory(
                        id=new_row_id("mem"),
                        world_id=world_id,
                        memory_id=memory.memory_id,
                        character_id=memory.character_id,
                        character_kind=memory.character_kind,
                        knowledge_type=memory.knowledge_type,
                        content=memory.content,
                        scene_id=memory.scene_id,
                        subjects=copy.deepcopy(memory.subjects or []),
                        topics=copy.deepcopy(memory.topics or []),
                        derivation_key=memory.derivation_key,
                        source=copy.deepcopy(memory.source or {}),
                        status=memory.status,
                        superseded_by=memory.superseded_by,
                        created_revision=int(memory.created_revision),
                        updated_revision=int(memory.updated_revision),
                        created_sequence=int(memory.created_sequence),
                    )
                )
        # slot_000：让分支在列表中可见且可续（结构化"续团"= 重连取快照）。
        save_game([], AUTO_SAVE_SLOT, context=target_context)
        return StructuredBranch(target_context, branch_label, source_context.world_id)
    except Exception:
        if target_context is not None:
            with session_scope(database_url) as session:
                world = session.get(World, target_context.world_id)
                if world is not None:
                    session.delete(world)
        raise


def restore_structured_save(
    context: RuntimeContext,
    slot_id: str | None = None,
) -> dict:
    """结构化世界读档：CAS 回滚状态 + 同事务 reconcile 结构化表。

    返回 {"slot_id", "revision"}；存档不存在抛 SaveNotFoundError，
    revision 竞争抛 StaleRevisionError。
    """
    from src.app.game_application import SaveNotFoundError
    from src.storage.database_store import StaleRevisionError
    from src.storage.world_migrations import migrate_world_state

    _messages, snapshot, _metadata = load_game_artifacts(slot_id, context=context)
    if snapshot is None:
        raise SaveNotFoundError(slot_id or AUTO_SAVE_SLOT)
    snapshot, _ = migrate_world_state(copy.deepcopy(snapshot))
    restored_revision = max(0, int(snapshot.get("revision", 0)))
    expected_revision = context.world_store.revision

    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id, with_for_update=True)
        if row is None:
            raise StructuredError("unknown_world", "世界状态缺失。")
        if expected_revision is not None and int(row.revision) != int(expected_revision):
            raise StaleRevisionError(expected_revision, int(row.revision))
        snapshot["revision"] = restored_revision
        row.state = snapshot
        row.revision = restored_revision
        row.updated_at = utcnow()

        # 被回滚"未来"里的待办不能挂着：所有非终态请求置 failed（其引用的
        # 状态可能已被回滚，悬挂才是静默不一致；failed 可用同 request_id 同
        # 载荷重发回 queued，见 §3.1），pending 检定做废，晚于存档点的事件
        # 从 outbox 删除（断线重连按游标补发时不得重演已回滚的世界）。
        open_requests = (
            session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == context.world_id,
                    PlayerRequest.status.in_(("queued", "processing", "awaiting_player", "paused")),
                )
            )
            .scalars()
            .all()
        )
        now = utcnow()
        for request in open_requests:
            request.status = "failed"
            request.detail = (
                request.detail or ""
            ) or "读档回滚：请求所在进度已被覆盖，可重新提交。"
            request.updated_at = now
        pending_checks = (
            session.execute(
                select(CheckRequest).where(
                    CheckRequest.world_id == context.world_id,
                    CheckRequest.status == "pending",
                )
            )
            .scalars()
            .all()
        )
        for check in pending_checks:
            check.status = "cancelled"
            check.updated_at = now
        late_events = (
            session.execute(
                select(EventOutbox).where(
                    EventOutbox.world_id == context.world_id,
                    EventOutbox.revision > restored_revision,
                )
            )
            .scalars()
            .all()
        )
        for event in late_events:
            session.delete(event)
        # 交互线程与角色记忆按同一存档点 reconcile（fail-closed，不泄漏未来）：
        # - 存档点之后创建的线程/记忆直接删除（那是被回滚掉的未来）；
        # - 存档点之后被更新过的线程一律 cancel（无法还原中途状态，与请求同契约）；
        #   其余开放线程也一律 cancel（读档不是刷新，等待关系随存档结束）；
        # - 存档点之后被取代/更正的记忆还原为 active（更正发生在被回滚的未来）。
        threads = (
            session.execute(
                select(InteractionThread).where(InteractionThread.world_id == context.world_id)
            )
            .scalars()
            .all()
        )
        for thread in threads:
            if int(thread.created_revision) > restored_revision:
                session.delete(thread)
            elif int(thread.updated_revision) > restored_revision or thread.status == "open":
                thread.status = "cancelled"
                thread.note = (thread.note or "") or "读档回滚：交互线程已作废。"
                thread.updated_revision = restored_revision
                thread.updated_at = now
        memories = (
            session.execute(
                select(CharacterMemory).where(CharacterMemory.world_id == context.world_id)
            )
            .scalars()
            .all()
        )
        for memory in memories:
            if int(memory.created_revision) > restored_revision:
                session.delete(memory)
            elif int(memory.updated_revision) > restored_revision:
                memory.status = "active"
                memory.superseded_by = ""
                memory.updated_revision = restored_revision
                memory.updated_at = now
    # 让 store 缓存失效（恢复是带外写入），下次读取以行为准。
    invalidate = getattr(context.world_store, "invalidate_cache", None)
    if callable(invalidate):
        invalidate()
    return {"slot_id": slot_id or AUTO_SAVE_SLOT, "revision": restored_revision}
