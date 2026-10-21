"""临时展示身份（guest speakers）：伙计、路人等未登记人物的公开发言身份。

与权威 NPC 表严格分开：guest 只承载玩家可见称呼，没有秘密、技能、位置、
线索权限，不进入 npcs 表、不参与线索/揭示结算。模型在 dialogue_v1 输出的
guest_speakers 字段声明 {speaker_id, display_name}，speaker_id 必须以
guest_ 开头；平台校验后把身份登记进世界状态的 guest_speakers 表
（presentation-only），随存档/分支按世界状态一起持久化与复制。

生命周期：
- 声明当回合即可发言；登记后同一世界后续回合可直接复用该 id，无需重复声明。
- 身份绑定登记时的场景 id；玩家离开该场景后该 id 不再可发言（不在场）。
- 同名不串人：display_name 允许重复，身份以稳定 id 区分。
- 读档/分支不共享任何进程内缓存，一切以世界状态为准。
"""

from __future__ import annotations

from typing import Any

GUEST_ID_PREFIX = "guest_"
MAX_GUESTS_PER_TURN = 4
MAX_GUESTS_PER_WORLD = 40


def guest_registry(world: dict) -> dict[str, dict]:
    registry = world.get("guest_speakers")
    if not isinstance(registry, dict):
        return {}
    return {
        str(guest_id): dict(entry)
        for guest_id, entry in registry.items()
        if isinstance(entry, dict) and str(entry.get("display_name") or "").strip()
    }


def guest_allowed(world: dict, guest_id: str, scene_id: str | None) -> bool:
    """登记过且仍在其登记场景内才可发言。"""
    entry = guest_registry(world).get(guest_id)
    if entry is None:
        return False
    bound = str(entry.get("scene_id") or "")
    return not bound or not scene_id or bound == scene_id


def registered_guest_ids(world: dict, scene_id: str | None) -> set[str]:
    return {
        guest_id
        for guest_id in guest_registry(world)
        if guest_allowed(world, guest_id, scene_id)
    }


def register_guest_declarations(
    engine: Any,
    declarations: list[dict],
    *,
    world: dict,
) -> list[str]:
    """校验并落账本轮声明的临时身份（就地更新 world，供 world_store.update 用）。

    返回实际登记的 id 列表；每条拒绝只记诊断，不中断回合。调用方负责在同一
    次世界事务里提交（turn commit / rewrite variant）。
    """
    if not declarations:
        return []
    registry = guest_registry(world)
    real_npc_ids = {
        str(npc.get("id") or "")
        for npc in world.get("npcs", []) or []
        if isinstance(npc, dict) and npc.get("id")
    }
    real_npc_ids |= set(((world.get("asset_map") or {}).get("npcs") or {}).keys())
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    accepted: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for declaration in declarations[:MAX_GUESTS_PER_TURN]:
        guest_id = str(declaration.get("speaker_id") or "")
        display_name = str(declaration.get("display_name") or "").strip()
        if not guest_id or not guest_id.startswith(GUEST_ID_PREFIX):
            rejected.append(f"{guest_id or '?'}(id 必须以 {GUEST_ID_PREFIX} 开头)")
            continue
        if guest_id in seen:
            continue  # 同回合重复声明：幂等
        seen.add(guest_id)
        if not display_name or "【" in display_name or "】" in display_name:
            rejected.append(f"{guest_id}(公开称呼无效)")
            continue
        if guest_id in real_npc_ids:
            rejected.append(f"{guest_id}(与正式 NPC 撞 id)")
            continue
        existing = registry.get(guest_id)
        if existing is not None:
            if existing.get("display_name") == display_name:
                continue  # 幂等重复声明
            rejected.append(f"{guest_id}(身份已登记，不得改名)")
            continue
        if len(registry) + len(accepted) >= MAX_GUESTS_PER_WORLD:
            rejected.append(f"{guest_id}(临时身份数量超限)")
            continue
        registry[guest_id] = {"display_name": display_name, "scene_id": scene_id}
        accepted.append(guest_id)
    if accepted or rejected:
        from src.app.logger import game_event as log_game

        parts = []
        if accepted:
            parts.append("登记 " + ", ".join(accepted))
        if rejected:
            parts.append("拒绝 " + ", ".join(rejected))
        log_game("临时人物身份 | " + "；".join(parts))
    if accepted:
        world["guest_speakers"] = registry
    return accepted


def commit_guest_declarations(engine: Any, declarations: list[dict]) -> list[str]:
    """把本轮声明落进世界状态（world_store.update 事务，mutator 返回 None）。"""
    if not declarations:
        return []
    accepted: list[str] = []

    def _apply(world: dict) -> None:
        nonlocal accepted
        accepted = register_guest_declarations(engine, declarations, world=world)

    engine.context.world_store.update(_apply)
    return accepted
