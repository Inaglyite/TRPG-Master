"""玩家可见的“当前所在场景”投影。

队伍位置只有一份权威来源：世界状态里的 ``current_scene``，由引擎在移动被
提交时写入（``GameEngine._resolve_scene_transition``，以及模组初始状态）。
本模块只从这份状态**派生**顶栏需要的那一小块信息，不写入、不缓存、也不
另建第二份可写的位置状态。

派生规则刻意保持窄：

- 只有已结算的 ``current_scene`` 才算位置。叙事正文、玩家提到某个地名、
  点击“前往”选项都不会进入这里——不做正文解析，也不做关键词匹配。
- 只输出玩家允许知道的地点名称。场景 id、出口、描述、在场人物一律不出境。
- 展示名优先取模组作者声明的玩家可见名（场景 ``public_name``，通常通过
  ``extensions`` 写入），没有才退回场景自身的 ``name``。
- 场景名等于场景 id 时视为“没有可用名称”：那串 id 本身就是内部键，
  显示出来等于泄露，此时返回 ``None``，由前端显示“位置未知”。
"""

from __future__ import annotations

from typing import Any

# 顶栏只有一行小字，超长名称截断由前端省略号与 title 负责，
# 这里只兜住病态长字符串，避免把整段文本塞进每条状态消息。
_MAX_NAME_LENGTH = 120


def player_scene_view(world_state: Any) -> dict | None:
    """返回 ``{"name": <玩家可见地点名>}``；无法确定位置时返回 ``None``。"""
    if not isinstance(world_state, dict):
        return None
    scene = world_state.get("current_scene")
    if not isinstance(scene, dict):
        return None

    scene_id = _field(scene, "id")
    catalog = world_state.get("scene_catalog")
    definition = catalog.get(scene_id) if isinstance(catalog, dict) else None

    # 玩家可见名优先于场景名，且世界内取值优先于模组目录：
    # 目录里的 public_name 让旧存档在模组刷新后也能立刻拿到正确的玩家名，
    # 而世界内同名字段仍可覆盖它。
    label = _field(scene, "public_name") or _field(definition, "public_name")
    if not label:
        label = _field(scene, "name") or _field(definition, "name")
    if not label or label == scene_id:
        return None
    return {"name": label[:_MAX_NAME_LENGTH]}


def player_scene_view_for(context: Any) -> dict | None:
    """``player_scene_view`` 的便捷包装：从运行时上下文读取权威世界状态。

    读取失败（世界尚未初始化、存储暂时不可用）时返回 ``None``，
    让前端落到“位置未知”，而不是猜测一个位置。
    """
    try:
        world_state = context.world_store.load()
    except Exception:
        return None
    return player_scene_view(world_state)


def _field(source: Any, key: str) -> str:
    if not isinstance(source, dict):
        return ""
    value = source.get(key)
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())
