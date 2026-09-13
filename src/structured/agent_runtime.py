"""Keeper Agent 的运行时胶水：BYOK 模型 caller、触发调度、assisted 草稿。

- 模型凭据只走房间 BYOK 路由；未配置/被阻断时绝不回落平台 Key，
  触发请求置 paused 并发 keeper_unavailable 可见错误，人类可接管。
- 每个世界同一时刻至多一个活动运行；运行中到达的新触发由下一轮
  上下文重建自然吸收（run_log 回喂），不并发重入。
"""

from __future__ import annotations

import asyncio
import logging

from src.storage.database import World, session_scope

from .agent import AgentBudget, KeeperAgentRunner, new_run_id
from .errors import StructuredError
from .gateway import StructuredGateway, world_modes
from .principal import Principal

logger = logging.getLogger("trpg.structured_agent")

_active_runs: dict[str, asyncio.Task] = {}


def build_byok_caller(database_url: str, world_id: str):
    """按房间 BYOK 路由构造模型 caller；未配置时抛错（调用方负责降级为暂停）。"""

    async def caller(system: str, user: str) -> str:
        from src.ai.model.route_service import resolve_routes
        from src.storage.model_config_store import room_route_resolver

        settings = await asyncio.to_thread(room_route_resolver(database_url, world_id))
        routes = resolve_routes(settings)  # BYOK 缺绑定在此 fail-closed
        role = routes.narrative

        def _call() -> str:
            response = role.client.chat.completions.create(
                model=role.model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.4,
                max_tokens=min(role.max_output_tokens or 4000, 4000),
            )
            return str(response.choices[0].message.content or "")

        return await asyncio.to_thread(_call)

    return caller


def keeper_agent_needed(database_url: str, world_id: str) -> str | None:
    """返回需要 Agent 的 keeper_mode（assisted/agent），否则 None。"""
    with session_scope(database_url) as session:
        world = session.get(World, world_id)
        if world is None:
            return None
        profile, keeper_mode = world_modes(world.metadata_json)
        if profile != "structured_v1":
            return None
        return keeper_mode if keeper_mode in {"assisted", "agent"} else None


async def _run_keeper_agent(
    *,
    database_url: str,
    world_id: str,
    keeper_mode: str,
    trigger_request_id: str,
    deliver,
    broadcast,
) -> None:
    gateway = StructuredGateway(database_url)
    try:
        caller = build_byok_caller(database_url, world_id)
        # 先探测一次路由可用性（不消耗模型）：直接构造 caller 不解析路由，
        # 真正的 fail-closed 在第一次模型调用时发生；为避免浪费一次调用，
        # 这里先同步解析一次。
        from src.ai.model.route_service import resolve_routes
        from src.storage.model_config_store import room_route_resolver

        await asyncio.to_thread(
            lambda: resolve_routes(room_route_resolver(database_url, world_id)())
        )
    except Exception as exc:
        logger.warning("keeper agent 模型路由不可用 world=%s: %s", world_id, type(exc).__name__)
        if trigger_request_id:
            from .principal import bind_agent_control, current_control

            try:
                # 人类在控时请求由人类处理，不抢占；否则绑定本次运行的
                # epoch 再置 paused（resolve_intent 需要主持控制权）。
                with session_scope(database_url) as session:
                    if current_control(session, world_id).controller_kind == "human":
                        return
                run_id = new_run_id()
                with session_scope(database_url) as session:
                    bind_agent_control(session, world_id, run_id)
                gateway.service.execute_command(
                    world_id=world_id,
                    principal=Principal(kind="agent", run_id=run_id),
                    kind="resolve_intent",
                    payload={
                        "request_id": trigger_request_id,
                        "resolution": "paused",
                        "note": "守秘人助手未配置模型服务（BYOK），请求已暂停；请房主配置模型或改由人类主持。",
                    },
                    command_id=f"unavailable-{trigger_request_id}",
                    expected_revision=None,
                )
            except StructuredError:
                pass
        return
    runner = KeeperAgentRunner(database_url, caller=caller, budget=AgentBudget())
    if keeper_mode == "agent":
        result = await runner.run(
            world_id=world_id,
            trigger_request_id=trigger_request_id,
            deliver=deliver,
            broadcast=broadcast,
        )
    else:
        result = await runner.run_assisted(
            world_id=world_id,
            trigger_request_id=trigger_request_id,
            deliver=deliver,
            broadcast=broadcast,
        )
    logger.info(
        "keeper agent run 结束 world=%s mode=%s status=%s reason=%s commands=%d",
        world_id,
        keeper_mode,
        result.status,
        result.stop_reason,
        result.commands_committed,
    )


def maybe_schedule_keeper_agent(
    *,
    database_url: str,
    world_id: str,
    trigger_request_id: str = "",
    deliver=None,
    broadcast=None,
) -> bool:
    """keeper_mode 为 assisted/agent 时调度一次运行；同世界已有活动运行则跳过。"""
    keeper_mode = keeper_agent_needed(database_url, world_id)
    if keeper_mode is None:
        return False
    existing = _active_runs.get(world_id)
    if existing is not None and not existing.done():
        return False
    task = asyncio.create_task(
        _run_keeper_agent(
            database_url=database_url,
            world_id=world_id,
            keeper_mode=keeper_mode,
            trigger_request_id=trigger_request_id,
            deliver=deliver,
            broadcast=broadcast,
        )
    )
    _active_runs[world_id] = task

    def _done(completed: asyncio.Task) -> None:
        _active_runs.pop(world_id, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.exception("keeper agent 运行异常 world=%s: %s", world_id, error, exc_info=error)

    task.add_done_callback(_done)
    return True
