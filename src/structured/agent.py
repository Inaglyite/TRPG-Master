"""Keeper Agent 运行器（主规格 §7 / 背景 §10 P3）。

判断 → 命令/叙事 → 读已提交结果 → 继续或等待玩家。
- 模型只产出 JSON 决策（不暴露思维链）；命令经 StructuredPlayService 逐条
  短事务提交，失败作为工具结果回喂，不让半程 JSON 落账。
- 预算：模型调用数 / 命令数 / 估算 token。超限把触发请求置 paused，
  保留已提交结果与待办，不静默兜底执行猜测行动。
- 接管：命令受理时复核 controller_epoch；人类接管后下一条命令即抛
  controller_epoch_stale，本运行器立即停止（不重试、不写入）。
- assisted 模式：同一模型调用产出草稿（keeper_draft），不执行任何命令；
  人类批准后由同一命令入口执行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from openai import APITimeoutError
from sqlalchemy import select

from src.storage.database import PlayerRequest, session_scope

from .agent_prompts import build_system_prompt
from .errors import StructuredError
from .principal import Principal, bind_agent_control, current_control
from .service import StructuredPlayService
from .validation import validate_command

logger = logging.getLogger("trpg.structured_agent")

# async (system, user) -> JSON 文本
ModelCaller = Callable[[str, str], Awaitable[str]]
Deliver = Callable[[dict], Awaitable[None]]

MAX_PARSE_RETRIES = 2
# 连续多少步「什么都没提交」就停下：命令被反复拒绝/模型空转时，不必烧完步数预算。
MAX_EMPTY_STEPS = 2


class EmptyDecision(ValueError):
    """模型返回空内容（finish_reason=length 截断，或 stop 但内容为空）。

    与「JSON 写坏了」分开处理：重试同样预算通常只会再空一次。
    """


class EmptyModelOutput(RuntimeError):
    """caller（生产路由）在 content 为空时抛出，携带可记录的诊断字段。"""

    def __init__(
        self,
        *,
        finish_reason: str | None,
        max_tokens: int,
        usage: dict | None = None,
    ) -> None:
        self.finish_reason = finish_reason
        self.max_tokens = max_tokens
        self.usage = usage or {}
        super().__init__(
            f"模型输出为空 finish_reason={finish_reason} 生效预算max_tokens={max_tokens}"
        )

    @property
    def truncated(self) -> bool:
        """长度截断（预算被推理/长文本吃满）与「stop 但空」是两种问题。"""
        return self.finish_reason == "length"


@dataclass
class AgentBudget:
    max_model_calls: int = 6
    max_commands: int = 12
    max_est_tokens: int = 60000  # 粗略字符/3 估算；精确计费另由调用方记录
    max_narration_chars: int = 4000  # 单条叙述截断，防止失控输出刷屏
    # 记忆按需查询的预算：普通对话不能每轮无限查；达到上限后查询请求被拒绝
    # 并回喂给模型（停止条件显式，不是静默吞掉）。
    max_queries: int = 3
    query_result_chars: int = 800
    context_memory_chars: int = 800  # 自动注入上下文的记忆区块预算
    context_memory_limit: int = 8


@dataclass
class AgentRunResult:
    run_id: str
    status: str = "done"  # done | paused | blocked | takeover_stopped
    stop_reason: str = ""
    model_calls: int = 0
    commands_committed: int = 0
    awaiting_parked: bool = False  # 本次运行是否把触发请求挂起为 awaiting_player
    decisions: list[str] = field(default_factory=list)


def new_run_id() -> str:
    return f"run_{secrets.token_hex(8)}"


class KeeperAgentRunner:
    """一个世界一条运行实例；由传输层在玩家请求/检定结算后触发。"""

    def __init__(
        self,
        database_url: str,
        *,
        caller: ModelCaller,
        budget: AgentBudget | None = None,
    ):
        self.database_url = database_url
        self.service = StructuredPlayService(database_url)
        self.caller = caller
        self.budget = budget or AgentBudget()

    # ------------------------------------------------------------------
    # 控制权
    # ------------------------------------------------------------------

    def _acquire(self, world_id: str, run_id: str) -> bool:
        """取得 agent 控制权；人类在控时不得抢占（返回 False 让人类继续）。"""
        with session_scope(self.database_url) as session:
            control = current_control(session, world_id)
            if control.controller_kind == "human":
                return False
            bind_agent_control(session, world_id, run_id)
            return True

    # ------------------------------------------------------------------
    # 上下文与决策解析
    # ------------------------------------------------------------------

    def _build_context(self, world_id: str, trigger_request_id: str, run_log: list[str]) -> str:
        keeper = Principal(kind="agent", run_id="context-preview")
        snapshot = self.service.session_snapshot(world_id=world_id, principal=keeper)
        from . import interactions as _interactions
        from . import memories as _memories

        with session_scope(self.database_url) as session:
            rows = session.execute(
                select(PlayerRequest)
                .where(
                    PlayerRequest.world_id == world_id,
                    PlayerRequest.status.in_(["queued", "processing", "awaiting_player", "paused"]),
                )
                .order_by(PlayerRequest.created_at)
            ).scalars()
            pending = []
            trigger_investigator = ""
            for row in rows:
                if row.request_type != "action_request":
                    continue
                entry = {
                    "request_id": row.request_id,
                    "type": row.request_type,
                    "status": row.status,
                    "investigator_id": row.investigator_id,
                    "action": (row.payload or {}).get("action"),
                }
                thread_id = str((row.payload or {}).get("thread_id") or "")
                if thread_id:
                    entry["thread_id"] = thread_id
                deferred = (row.payload or {}).get("awaiting")
                if deferred:
                    # 过渡回合的待办：玩家尚未执行的意图 + 已告知条件。
                    # 字段名与布尔位都写明它**不是**执行授权——避免把待办读成
                    # 「已经批准的任务」，也避免为了清掉它而自动移动。
                    entry["deferred_player_intent"] = deferred
                    entry["deferred_player_intent_is_authorization"] = False
                pending.append(entry)
                if row.request_id == trigger_request_id:
                    trigger_investigator = str(row.investigator_id or "")
            # 第 2 层：当前交互线程（跨请求存活）。候选绑定是确定性的状态匹配
            # （同一调查员 / 等待对象命中），不做任何文本推断；没有可绑定时
            # candidate_thread_ids 为空数组——「无」也是显式信息。
            open_threads = []
            candidate_thread_ids: list[str] = []
            for thread in _interactions.list_open_threads(session, world_id):
                projection = _interactions.public_projection(thread)
                projection["linked_request_ids"] = list(thread.request_ids or [])[-3:]
                candidate = bool(
                    trigger_investigator
                    and (
                        thread.investigator_id == trigger_investigator
                        or thread.waiting_on in {trigger_investigator, "party"}
                    )
                )
                projection["candidate_for_trigger"] = candidate
                if candidate:
                    candidate_thread_ids.append(thread.thread_id)
                open_threads.append(projection)
            # 第 4 层：角色长期记忆，按需检索（当前场景 + 在场角色的自动小预算
            # 注入；更多由模型用 queries 主动查，见决策契约）。记忆是「某角色知道/
            # 相信什么」，不是权威事实；knowledge_type 必须随条目一起呈现。
            present_ids: list[str] = []
            for target in snapshot.get("targets") or []:
                if isinstance(target, dict) and target.get("id"):
                    present_ids.append(str(target["id"]))
            scene_id = str((snapshot.get("scene") or {}).get("id") or "")
            memory_entries = _memories.retrieve(
                session,
                world_id,
                character_ids=present_ids or None,
                scene_id=scene_id or None,
                limit=self.budget.context_memory_limit,
                char_budget=self.budget.context_memory_chars,
            )
        context = {
            "snapshot": snapshot,
            "open_threads": open_threads,
            "trigger_context": {
                "request_id": trigger_request_id or None,
                "investigator_id": trigger_investigator or None,
                "candidate_thread_ids": candidate_thread_ids,
                "has_open_thread": bool(open_threads),
            },
            "pending_requests": pending,
            "recent_public_messages": self._recent_transcript(world_id),
            "character_memories": memory_entries,
            "trigger_request_id": trigger_request_id or None,
            "run_log": run_log[-12:],  # 本运行的近期命令结果回喂
        }
        return json.dumps(context, ensure_ascii=False)

    def _recent_transcript(self, world_id: str, limit: int = 8) -> list[dict]:
        """最近公开对话（speaker + 文本）。

        上下文里没有历史消息时，主持无法承接「追问」——上一轮的过渡对白
        与玩家的话都必须能看到；只取公开/主持可见的消息，不注入思维链。
        """
        from src.storage.database import EventOutbox

        with session_scope(self.database_url) as session:
            rows = (
                session.execute(
                    select(EventOutbox)
                    .where(
                        EventOutbox.world_id == world_id,
                        EventOutbox.event_type == "message_completed",
                    )
                    .order_by(EventOutbox.sequence.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            messages = []
            for row in reversed(rows):
                payload = row.payload or {}
                audience = row.audience or {"kind": "public"}
                if not isinstance(audience, dict):
                    # 旧数据/异常写入可能把 audience 存成字符串：跳过而不是崩掉整轮运行。
                    continue
                if audience.get("kind") not in {"public", "keeper"}:
                    continue  # 定向私发不进公共上下文
                text = str(payload.get("text") or "")[:400]
                if not text:
                    continue
                messages.append({"speaker": payload.get("speaker") or {}, "text": text})
        return messages

    @staticmethod
    def _parse_decision(raw: str) -> dict:
        """严格 JSON 解析；不接受半截/散文包裹的输出。"""
        text = raw.strip()
        if not text:
            # 推理型模型把预算花在 reasoning 上时 content 为空（finish_reason=length）：
            # 这与「JSON 写坏了」是两回事，重试同样预算只会再空一次。
            raise EmptyDecision("模型本次没有输出任何决策内容（输出预算被推理耗尽）")
        if text.startswith("```"):
            # 去掉 ```json 围栏但不解析围栏外的散文
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("决策必须是 JSON 对象")
        commands = data.get("commands") or []
        if not isinstance(commands, list):
            raise ValueError("commands 必须是数组")
        return data

    # ------------------------------------------------------------------
    # 按需查询（记忆检索）：只读、有预算、结果回喂到下一步上下文
    # ------------------------------------------------------------------

    def _run_queries(
        self,
        *,
        world_id: str,
        queries: list,
        queries_run: int,
        run_log: list[str],
    ) -> int:
        """执行决策里的只读查询。返回新耗用的查询次数（预算外一律拒绝并回喂）。"""
        if not isinstance(queries, list):
            run_log.append("queries 必须是数组，已忽略。")
            return 0
        spent = 0
        keeper = Principal(kind="agent", run_id="memory-query")
        for query in queries:
            if queries_run + spent >= self.budget.max_queries:
                run_log.append(f"查询预算已用完（本轮最多 {self.budget.max_queries} 次）；请直接判断或结束本轮。")
                break
            if not isinstance(query, dict) or str(query.get("kind") or "") != "memory":
                run_log.append("未知查询类型（当前只支持 kind=memory），已跳过。")
                continue
            try:
                rows = self.service.query_memories(
                    world_id=world_id,
                    principal=keeper,
                    character_id=str(query.get("character_id") or ""),
                    scene_id=str(query.get("scene_id") or ""),
                    topics=query.get("topics") if isinstance(query.get("topics"), list) else None,
                    text=str(query.get("text") or ""),
                    limit=min(int(query.get("limit") or 5), 8),
                    char_budget=self.budget.query_result_chars,
                )
            except StructuredError as exc:
                run_log.append(f"memory 查询被拒绝：{exc.code} {exc.message}")
                spent += 1
                continue
            spent += 1
            if not rows:
                run_log.append("memory 查询结果：无匹配记忆（未注入不等于未发生，反之亦然）。")
                continue
            lines = []
            for row in rows:
                lines.append(
                    f"- [{row['character_id']}|{row['knowledge_type']}] {row['content']}"
                )
            run_log.append("memory 查询结果：\n" + "\n".join(lines))
        return spent

    # ------------------------------------------------------------------
    # 命令执行（逐条短事务；失败回喂；epoch 失效即停）
    # ------------------------------------------------------------------

    async def _execute_commands(
        self,
        *,
        world_id: str,
        principal: Principal,
        commands: list[dict],
        run_id: str,
        step: int,
        trigger_request_id: str,
        result: AgentRunResult,
        deliver: Deliver | None,
        broadcast: Deliver | None,
        spent: list[str],
    ) -> tuple[str | None, int]:
        """返回 (停止信号, 本步被拒命令数)；“takeover” 表示控制权已移交，立即停止。"""
        rejected = 0
        for index, command in enumerate(commands):
            if result.commands_committed >= self.budget.max_commands:
                spent.append("命令预算已用完")
                return "budget", rejected
            kind = str(command.get("kind") or "")
            payload = command.get("payload")
            if not kind or not isinstance(payload, dict):
                spent.append(f"命令 {index} 缺少 kind/payload，已跳过")
                continue
            # 兜底 id 必须与**尝试次数**绑定：原先用 commands_committed（已提交数）
            # 拼接，某一步全部被拒时下一步就会重用同一 id，撞上幂等键被判
            # duplicate_request_conflict（真实模型验收实测），随后整轮空转。
            command_id = (
                str(command.get("command_id") or "") or f"{run_id}-s{step}-c{index}"
            )
            try:
                # Agent 生成的命令与客户端帧共用同一份冻结 schema：payload 字段与
                # 枚举在这里就拦住，不让「错误类型」进入执行层再靠各领域函数兜。
                validate_command(kind, payload)
            except StructuredError as exc:
                rejected += 1
                spent.append(f"{kind} 未过命令 schema：{exc.message}")
                continue
            try:
                outcome = await asyncio.to_thread(
                    self.service.execute_command,
                    world_id=world_id,
                    principal=principal,
                    kind=kind,
                    payload=payload,
                    command_id=command_id,
                    expected_revision=None,
                    cause_id=trigger_request_id or command_id,
                )
            except StructuredError as exc:
                if exc.code == "controller_epoch_stale":
                    return "takeover", rejected
                rejected += 1
                spent.append(f"{kind} 被拒绝：{exc.code} {exc.message}")
                continue
            except Exception as exc:  # 未知错误：停在本命令，已提交不回滚
                logger.exception("agent 命令异常 world=%s kind=%s", world_id, kind)
                rejected += 1
                spent.append(f"{kind} 内部错误：{type(exc).__name__}")
                continue
            result.commands_committed += 1
            if (
                kind == "resolve_intent"
                and str(payload.get("resolution") or "") == "awaiting_player"
            ):
                # 主持已用正式命令挂起：本步随后不得再执行任何命令。
                result.awaiting_parked = True
                spent.append(f"{kind} committed: awaiting_player")
                await self._publish_events(world_id, outcome, deliver=deliver, broadcast=broadcast)
                return "await", rejected
            spent.append(
                f"{kind} committed: {json.dumps(outcome['result'], ensure_ascii=False)[:200]}"
            )
            await self._publish_events(world_id, outcome, deliver=deliver, broadcast=broadcast)
        return None, rejected

    @staticmethod
    def _order_commands(commands: list[dict], narration: str) -> list[dict]:
        """规范化一步之内的命令顺序。

        - 含 `awaiting_player` 收尾时，它是**屏障**：模型排在其后的命令一律丢弃
          （「等待玩家」与「继续执行」不能在同一个决策里同时成立），叙述插在屏障之前——
          玩家先看到过渡对白，再看到「等你回应」。
        - 其余情况：工具 → 叙述 → resolve_intent。收尾排在最后，客户端才会先看到
          权威事件（移动/线索/检定），卡片再收尾。
        """
        for index, command in enumerate(commands):
            payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
            if (
                str(command.get("kind") or "") == "resolve_intent"
                and str(payload.get("resolution") or "") == "awaiting_player"
            ):
                kept = list(commands[:index])
                if narration:
                    kept.append(
                        {
                            "kind": "publish_message",
                            "payload": {
                                "speaker": {"kind": "keeper"},
                                "audience": {"kind": "public"},
                                "text": narration,
                            },
                        }
                    )
                return [*kept, command]
        tools: list[dict] = []
        resolves: list[dict] = []
        for command in commands:
            if str(command.get("kind") or "") == "resolve_intent":
                resolves.append(command)
            else:
                tools.append(command)
        if narration:
            tools.append(
                {
                    "kind": "publish_message",
                    "payload": {
                        "speaker": {"kind": "keeper"},
                        "audience": {"kind": "public"},
                        "text": narration,
                    },
                }
            )
        return [*tools, *resolves]

    # ------------------------------------------------------------------
    # 等待玩家自由回应（过渡回合的落点）
    # ------------------------------------------------------------------

    def _awaiting_payload(
        self,
        world_id: str,
        trigger_request_id: str,
        awaiting: dict | None,
        fallback_note: str,
    ) -> dict | None:
        """组装 awaiting_player 的公开待办；已终态请求返回 None（不挂起）。"""
        provided = awaiting if isinstance(awaiting, dict) else {}
        pending = provided.get("pending_action")
        with session_scope(self.database_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == world_id,
                    PlayerRequest.request_id == trigger_request_id,
                )
            ).scalar_one_or_none()
            if row is not None and row.status in {"completed", "declined", "cancelled"}:
                return None
            action = dict((row.payload or {}).get("action") or {}) if row is not None else {}
        if not isinstance(pending, dict) or not pending.get("kind"):
            pending = self._pending_from_action(action, fallback_note)
        record: dict = {"pending_action": pending}
        disclosed = provided.get("disclosed")
        if isinstance(disclosed, list) and disclosed:
            record["disclosed"] = disclosed
        note = str(provided.get("note") or fallback_note or "")[:500]
        if note:
            record["note"] = note
        return record

    @staticmethod
    def _pending_from_action(action: dict, fallback_note: str) -> dict:
        """从玩家原请求推导「尚未执行」的行动；只作记录，不是执行授权。"""
        kind = str(action.get("kind") or "")
        if kind == "move":
            return {
                "kind": "move",
                "destination_scene_id": str(action.get("destination_scene_id") or "")[:160],
                "note": fallback_note[:300] or "尚未出发",
            }
        if kind in {"present_clue", "use_item"}:
            target = action.get("target") or {}
            return {
                "kind": kind,
                "target": str(target.get("id") or target.get("text") or "")[:160],
                "note": fallback_note[:300],
            }
        if kind == "freeform":
            return {"kind": "freeform", "note": str(action.get("text") or "")[:300]}
        return {"kind": "other", "note": fallback_note[:300] or "等待玩家回应"}

    def _resolve_trigger(
        self,
        world_id: str,
        principal: Principal,
        trigger_request_id: str,
        *,
        resolution: str,
        note: str,
    ) -> dict | None:
        """把仍是未决状态的触发请求收尾；已终态则不动（返回 outcome 供投递）。"""
        if not trigger_request_id:
            return None
        with session_scope(self.database_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == world_id,
                    PlayerRequest.request_id == trigger_request_id,
                )
            ).scalar_one_or_none()
            if row is None or row.status in {"completed", "declined", "cancelled"}:
                return None
        try:
            return self.service.execute_command(
                world_id=world_id,
                principal=principal,
                kind="resolve_intent",
                payload={"request_id": trigger_request_id, "resolution": resolution, "note": note},
                command_id=f"{resolution}-{trigger_request_id}",
                expected_revision=None,
            )
        except StructuredError:
            return None

    def _await_trigger(
        self,
        world_id: str,
        principal: Principal,
        trigger_request_id: str,
        *,
        awaiting: dict | None,
        fallback_note: str,
    ) -> dict | None:
        """把触发请求挂起为 awaiting_player：只记录待办，不执行任何命令。

        幂等：同一触发请求固定 command_id，重复运行不会重复挂起。
        """
        if not trigger_request_id:
            return None
        record = self._awaiting_payload(world_id, trigger_request_id, awaiting, fallback_note)
        if record is None:
            return None
        try:
            return self.service.execute_command(
                world_id=world_id,
                principal=principal,
                kind="resolve_intent",
                payload={
                    "request_id": trigger_request_id,
                    "resolution": "awaiting_player",
                    **record,
                },
                command_id=f"await-{trigger_request_id}",
                expected_revision=None,
            )
        except StructuredError:
            return None  # 已终态/不存在/被接管：等待只是记录，不强制

    async def _publish_events(
        self,
        world_id: str,
        outcome: dict,
        *,
        deliver: Deliver | None,
        broadcast: Deliver | None,
    ) -> None:
        from .service import wire_envelope

        for envelope in outcome.get("events") or []:
            # Agent 没有发起连接：本地单连接 deliver 全量；房间由 broadcast
            # 按各连接 principal 过滤（不剥离前的内部信封含 audience）。
            if deliver is not None:
                await deliver(wire_envelope(envelope))
            if broadcast is not None:
                await broadcast(envelope)

    def _pause_trigger(
        self, world_id: str, principal: Principal, trigger_request_id: str, note: str
    ) -> None:
        if not trigger_request_id:
            return
        try:
            self.service.execute_command(
                world_id=world_id,
                principal=principal,
                kind="resolve_intent",
                payload={
                    "request_id": trigger_request_id,
                    "resolution": "paused",
                    "note": note[:500],
                },
                command_id=f"pause-{trigger_request_id}-{secrets.token_hex(3)}",
                expected_revision=None,
            )
        except StructuredError:
            pass  # 请求可能已被处理/接管；暂停只是提示，不强制

    # ------------------------------------------------------------------
    # assisted 模式：产出草稿，不执行命令
    # ------------------------------------------------------------------

    async def run_assisted(
        self,
        *,
        world_id: str,
        trigger_request_id: str = "",
        deliver: Deliver | None = None,
        broadcast: Deliver | None = None,
    ) -> AgentRunResult:
        """一次模型调用产出 keeper_draft 草稿；批准/拒绝由 resolve_draft 收尾。"""
        run_id = new_run_id()
        result = AgentRunResult(run_id=run_id)
        result.model_calls = 1
        context_text = self._build_context(world_id, trigger_request_id, [])
        try:
            raw = await self.caller(build_system_prompt(), context_text)
            decision = self._parse_decision(raw)
        except EmptyModelOutput as exc:
            result.status = "paused"
            result.stop_reason = (
                "draft_unavailable:model_output_truncated"
                if exc.truncated
                else "draft_unavailable:model_output_empty"
            )
            return result
        except Exception as exc:
            result.status = "paused"
            result.stop_reason = f"draft_unavailable:{type(exc).__name__}"
            return result
        commands = []
        for command in decision.get("commands") or []:
            kind = str(command.get("kind") or "")
            payload = command.get("payload")
            if kind and isinstance(payload, dict):
                commands.append({"kind": kind, "payload": payload})
        narration = str(decision.get("narration") or "")[: self.budget.max_narration_chars]
        summary = str(decision.get("assessment") or "")[:200] or "守秘人助手建议"
        draft = self.service.create_keeper_draft(
            world_id=world_id,
            summary=summary,
            proposed_commands=commands,
            narration=narration,
            related_request_id=trigger_request_id,
        )
        result.decisions.append(summary)
        result.stop_reason = "draft_ready"
        from .service import wire_envelope

        for envelope in draft["events"]:
            # 草稿只给 keeper（事件 audience 已是 keeper）；本地单连接全量。
            if deliver is not None:
                await deliver(wire_envelope(envelope))
            if broadcast is not None:
                await broadcast(envelope)
        return result

    # ------------------------------------------------------------------
    # 主循环（agent 模式）
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        world_id: str,
        run_id: str | None = None,
        trigger_request_id: str = "",
        deliver: Deliver | None = None,
        broadcast: Deliver | None = None,
    ) -> AgentRunResult:
        run_id = run_id or new_run_id()
        principal = Principal(kind="agent", run_id=run_id)
        result = AgentRunResult(run_id=run_id)
        if not self._acquire(world_id, run_id):
            result.status = "blocked"
            result.stop_reason = "human_in_control"
            return result
        run_log: list[str] = []
        parse_failures = 0
        empty_steps = 0
        est_tokens = 0
        queries_run = 0
        try:
            for _step in range(self.budget.max_model_calls):
                result.model_calls += 1
                context_text = self._build_context(world_id, trigger_request_id, run_log)
                try:
                    raw = await self.caller(build_system_prompt(), context_text)
                except EmptyModelOutput as exc:
                    # 空输出：分「截断」与「非截断空响应」，并把实际生效预算与
                    # usage 写进暂停说明与日志——不能一律称作推理预算问题。
                    result.status = "paused"
                    result.stop_reason = (
                        "model_output_truncated" if exc.truncated else "model_output_empty"
                    )
                    logger.warning(
                        "agent 空输出 world=%s finish_reason=%s max_tokens=%s usage=%s",
                        world_id,
                        exc.finish_reason,
                        exc.max_tokens,
                        exc.usage,
                    )
                    self._pause_trigger(
                        world_id,
                        principal,
                        trigger_request_id,
                        (
                            f"守秘人助手本次没有产出内容（finish_reason={exc.finish_reason}，"
                            f"生效 max_tokens={exc.max_tokens}，usage={exc.usage}）。"
                            "已提交结果保留；请调整该服务的最大输出长度或改用非推理模型后接管继续。"
                        ),
                    )
                    return result
                except APITimeoutError as exc:
                    result.status = "paused"
                    result.stop_reason = "model_timeout"
                    logger.warning("agent 模型调用超时 world=%s: %s", world_id, exc)
                    self._pause_trigger(
                        world_id,
                        principal,
                        trigger_request_id,
                        "守秘人助手模型调用超时，已提交结果保留，可接管继续或稍后重试。",
                    )
                    return result
                except Exception as exc:
                    result.status = "paused"
                    result.stop_reason = f"model_error:{type(exc).__name__}"
                    self._pause_trigger(
                        world_id,
                        principal,
                        trigger_request_id,
                        "守秘人助手暂时不可用（模型调用失败），已提交结果保留，可接管继续。",
                    )
                    return result
                est_tokens += (len(build_system_prompt()) + len(context_text) + len(raw)) // 3
                try:
                    decision = self._parse_decision(raw)
                    parse_failures = 0
                except EmptyDecision:
                    # caller 直接返回空串（例如自定义 caller）：同样不重试。
                    result.status = "paused"
                    result.stop_reason = "model_output_empty"
                    self._pause_trigger(
                        world_id,
                        principal,
                        trigger_request_id,
                        "守秘人助手本次没有产出决策（输出预算被推理耗尽）。"
                        "已提交结果保留；请提高该服务的最大输出长度或改用非推理模型后接管继续。",
                    )
                    return result
                except (ValueError, json.JSONDecodeError) as exc:
                    parse_failures += 1
                    run_log.append(f"决策解析失败：{exc}")
                    if parse_failures >= MAX_PARSE_RETRIES:
                        result.status = "paused"
                        result.stop_reason = "unparseable_decision"
                        self._pause_trigger(
                            world_id,
                            principal,
                            trigger_request_id,
                            "守秘人助手输出了无法解析的决策，已暂停，等待主持接管。",
                        )
                        return result
                    continue
                assessment = str(decision.get("assessment") or "")[:200]
                if assessment:
                    result.decisions.append(assessment)
                # 只读查询先于命令执行：结果进 run_log，下一步上下文可见。
                # 查询算进展（不是空转），但占用独立预算，防止每轮无限查。
                queries_this_step = self._run_queries(
                    world_id=world_id,
                    queries=decision.get("queries") or [],
                    queries_run=queries_run,
                    run_log=run_log,
                )
                queries_run += queries_this_step
                committed_before = result.commands_committed
                narration = str(decision.get("narration") or "")[: self.budget.max_narration_chars]
                commands = self._order_commands(decision.get("commands") or [], narration)
                stop, step_rejected = await self._execute_commands(
                    world_id=world_id,
                    principal=principal,
                    commands=commands,
                    run_id=run_id,
                    step=_step,
                    trigger_request_id=trigger_request_id,
                    result=result,
                    deliver=deliver,
                    broadcast=broadcast,
                    spent=run_log,
                )
                if stop == "takeover":
                    result.status = "takeover_stopped"
                    result.stop_reason = "controller_epoch_stale"
                    return result
                if stop == "budget":
                    break
                if est_tokens > self.budget.max_est_tokens:
                    result.stop_reason = "token_budget"
                    break
                stop_reason = str(decision.get("stop_reason") or "")
                waits_for_player = bool(decision.get("wait_for_player")) or stop_reason in {
                    "wait_player",
                    "clarify",
                }
                if (
                    (waits_for_player or stop_reason == "done")
                    and step_rejected
                    and result.commands_committed == 0
                ):
                    # 本步命令全被拒（例如把自由文本写进了 outcome）：模型还没
                    # 看到驳回理由就挂起，等于玩家什么都看不到。先带驳回喂回去
                    # 让它纠正一次（受模型调用预算约束），再决定是否等待。
                    run_log.append("本次命令全部被拒：请按上面的原因修正 payload 后重发同一意图。")
                    continue
                if waits_for_player or stop_reason == "done":
                    result.stop_reason = stop_reason or "wait_player"
                    if trigger_request_id and not result.awaiting_parked:
                        if waits_for_player:
                            # 决策层声明等待 → 落成持久待办（写清尚未执行的行动）。
                            # 之后本运行立即结束：不执行剩余命令，也不开后台续跑。
                            await_outcome = self._await_trigger(
                                world_id,
                                principal,
                                trigger_request_id,
                                awaiting=decision.get("awaiting")
                                if isinstance(decision.get("awaiting"), dict)
                                else None,
                                fallback_note=narration or str(decision.get("assessment") or ""),
                            )
                            result.awaiting_parked = await_outcome is not None
                            if await_outcome is not None:
                                await self._publish_events(
                                    world_id,
                                    await_outcome,
                                    deliver=deliver,
                                    broadcast=broadcast,
                                )
                        else:
                            # done：本轮处理完毕。未显式 resolve_intent 时补一条收尾，
                            # 避免请求永远停在 queued 让玩家以为还在处理。
                            done_outcome = self._resolve_trigger(
                                world_id,
                                principal,
                                trigger_request_id,
                                resolution="completed",
                                note=narration[:200] or "守秘人本轮处理完毕",
                            )
                            if done_outcome is not None:
                                await self._publish_events(
                                    world_id,
                                    done_outcome,
                                    deliver=deliver,
                                    broadcast=broadcast,
                                )
                    return result
                if result.commands_committed == committed_before and not queries_this_step:
                    # 本步零提交且零查询：命令被拒或模型空转。连续多步如此就停下，
                    # 不要用同样的失败烧完整个步数预算（真实模型验收实测：
                    # 一条意愿请求被反复拒绝，6 次调用后才发现预算耗尽）。
                    empty_steps += 1
                    if empty_steps >= MAX_EMPTY_STEPS:
                        result.status = "paused"
                        result.stop_reason = "repeated_rejections"
                        self._pause_trigger(
                            world_id,
                            principal,
                            trigger_request_id,
                            "守秘人助手连续多步没有提交任何命令（命令被拒或空转），"
                            "已暂停；原因见最近一次驳回信息，可接管继续。",
                        )
                        return result
                else:
                    empty_steps = 0
                if not commands:
                    # 无进展保护：没有命令也没有叙述。停在明确状态上，不能让请求
                    # 永远停在 queued（真实模型实测：模型返回空决策时玩家什么都看不到）。
                    result.status = "paused"
                    result.stop_reason = "no_progress"
                    self._pause_trigger(
                        world_id,
                        principal,
                        trigger_request_id,
                        "守秘人助手本轮没有产出可执行的决策（无命令也无叙述），已暂停，可接管继续。",
                    )
                    return result
            # 预算/步数耗尽
            result.status = "paused"
            result.stop_reason = result.stop_reason or "budget_exceeded"
            self._pause_trigger(
                world_id,
                principal,
                trigger_request_id,
                "守秘人助手超出本次预算/步数，已提交结果保留，可接管或稍后继续。",
            )
            return result
        except asyncio.CancelledError:
            # 取消信号：已提交命令不回滚，直接离场。
            result.status = "paused"
            result.stop_reason = "cancelled"
            raise
