"""引擎模型客户端门禁：structured_v1 + human 世界不建模型客户端。

主规格：human keeper 模式不用 Key、不建模型会话。引擎构造是每条 WS 连接
（含房间共享驱动）的必经路径，若在此强制实例化 OpenAI 客户端，无 Key 的
人类主持世界连开局都做不到。返回 None 后所有模型调用路径在结构化世界
已被回合门禁拦截，不会被解引用。
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from src.storage.database import World, session_scope

from .gateway import world_modes


def build_engine_client(context: Any, *, api_key: str, base_url: str, timeout: float) -> Any | None:
    """legacy/assisted/agent 世界照常实例化；structured_v1+human 返回 None。

    凭据/地址/超时由调用方（engine.py 的模块全局）传入，保留既有测试对
    ``src.app.engine.API_KEY`` 的 patch 语义。
    """
    profile, keeper_mode = "legacy", "human"
    try:
        with session_scope(context.database_url) as session:
            world = session.get(World, context.world_id)
            if world is not None:
                profile, keeper_mode = world_modes(world.metadata_json)
    except Exception:
        # 元数据读取失败时保持旧行为（建客户端），不阻断 legacy 世界。
        pass
    if profile == "structured_v1" and keeper_mode == "human":
        return None
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
