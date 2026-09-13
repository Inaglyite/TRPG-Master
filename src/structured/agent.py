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

from sqlalchemy import select

from src.storage.database import PlayerRequest, session_scope

from .agent_prompts import build_system_prompt
from .errors import StructuredError
from .principal import Principal, bind_agent_control, current_control
from .service import StructuredPlayService

logger = logging.getLogger("trpg.structured_agent")

# async (system, user) -> JSON 文本
ModelCaller = Callable[[str, str], Awaitable[str]]
Deliver = Callable[[dict], Awaitable[None]]

MAX_PARSE_RETRIES = 2


@dataclass
class AgentBudget:
    max_model_calls: int = 6
    max_commands: int = 12
    max_est_tokens: int = 60000  # 粗略字符/3 估算；精确计费另由调用方记录
    max_narration_chars: int = 4000  # 单条叙述截断，防止失控输出刷屏


@dataclass
class AgentRunResult:
    run_id: str
    status: str = "done"  # done | paused | blocked | takeover_stopped
    stop_reason: str = ""
    model_calls: int = 0
    commands_committed: int = 0
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
        with session_scope(self.database_url) as session:
            rows = session.execute(
                select(PlayerRequest)
                .where(
                    PlayerRequest.world_id == world_id,
                    PlayerRequest.status.in_(["queued", "processing", "awaiting_player", "paused"]),
                )
                .order_by(PlayerRequest.created_at)
            ).scalars()
            pending = [
                {
                    "request_id": row.request_id,
                    "type": row.request_type,
                    "status": row.status,
                    "investigator_id": row.investigator_id,
                    "action": (row.payload or {}).get("action"),
                }
                for row in rows
                if row.request_type == "action_request"
            ]
        context = {
            "snapshot": snapshot,
            "pending_requests": pending,
            "trigger_request_id": trigger_request_id or None,
            "run_log": run_log[-12:],  # 本运行的近期命令结果回喂
        }
        return json.dumps(context, ensure_ascii=False)

    @staticmethod
    def _parse_decision(raw: str) -> dict:
        """严格 JSON 解析；不接受半截/散文包裹的输出。"""
        text = raw.strip()
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
    # 命令执行（逐条短事务；失败回喂；epoch 失效即停）
    # ------------------------------------------------------------------

    async def _execute_commands(
        self,
        *,
        world_id: str,
        principal: Principal,
        commands: list[dict],
        run_id: str,
        trigger_request_id: str,
        result: AgentRunResult,
        deliver: Deliver | None,
        broadcast: Deliver | None,
        spent: list[str],
    ) -> str | None:
        """返回 None 继续；"takeover" 表示控制权已移交，立即停止。"""
        for index, command in enumerate(commands):
            if result.commands_committed >= self.budget.max_commands:
                spent.append("命令预算已用完")
                return "budget"
            kind = str(command.get("kind") or "")
            payload = command.get("payload")
            if not kind or not isinstance(payload, dict):
                spent.append(f"命令 {index} 缺少 kind/payload，已跳过")
                continue
            command_id = (
                str(command.get("command_id") or "") or f"{run_id}-cmd-{result.commands_committed}"
            )
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
                    return "takeover"
                spent.append(f"{kind} 被拒绝：{exc.code} {exc.message}")
                continue
            except Exception as exc:  # 未知错误：停在本命令，已提交不回滚
                logger.exception("agent 命令异常 world=%s kind=%s", world_id, kind)
                spent.append(f"{kind} 内部错误：{type(exc).__name__}")
                continue
            result.commands_committed += 1
            spent.append(
                f"{kind} committed: {json.dumps(outcome['result'], ensure_ascii=False)[:200]}"
            )
            await self._publish_events(world_id, outcome, deliver=deliver, broadcast=broadcast)
        return None

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
        est_tokens = 0
        try:
            for _step in range(self.budget.max_model_calls):
                result.model_calls += 1
                context_text = self._build_context(world_id, trigger_request_id, run_log)
                try:
                    raw = await self.caller(build_system_prompt(), context_text)
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
                narration = str(decision.get("narration") or "")[: self.budget.max_narration_chars]
                commands = decision.get("commands") or []
                if narration:
                    commands = [
                        *commands,
                        {
                            "kind": "publish_message",
                            "payload": {
                                "speaker": {"kind": "keeper"},
                                "audience": {"kind": "public"},
                                "text": narration,
                            },
                        },
                    ]
                stop = await self._execute_commands(
                    world_id=world_id,
                    principal=principal,
                    commands=commands,
                    run_id=run_id,
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
                if decision.get("wait_for_player") or decision.get("stop_reason") in {
                    "wait_player",
                    "clarify",
                    "done",
                }:
                    result.stop_reason = str(decision.get("stop_reason") or "wait_player")
                    return result
                if not commands:
                    # 无进展保护：没有命令也没有叙述，停止空转。
                    result.stop_reason = "no_progress"
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
