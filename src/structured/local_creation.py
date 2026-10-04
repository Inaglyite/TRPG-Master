"""Explicit local new-game creation; never an AI opening disguised as human play."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from src.app.runtime import RuntimeContext
from src.gameplay.characters import character_to_pc, resolve_character
from src.storage.database import World, WorldState, session_scope

from .bootstrap import apply_profile_metadata, ensure_local_operator
from .errors import StructuredError
from .registries import register_investigator_inventory


@dataclass(frozen=True)
class LocalCreation:
    context: RuntimeContext
    replayed: bool
    profile: str


def can_resume_structured(context: RuntimeContext) -> bool:
    """Structured sessions resume committed state, not a legacy AI autosave."""
    with session_scope(context.database_url) as session:
        world = session.get(World, context.world_id)
        return bool(
            world is not None
            and world.status == "active"
            and (world.metadata_json or {}).get("execution_profile") == "structured_v1"
            and session.get(WorldState, context.world_id) is not None
        )


def create_local_world(source: RuntimeContext, payload: dict) -> LocalCreation:
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", request_id):
        raise StructuredError("invalid_action", "新建请求标识不合法。")
    profile = payload.get("execution_profile")
    mode = payload.get("keeper_mode")
    apply_profile_metadata({}, execution_profile=profile, keeper_mode=mode)
    source_id = payload.get("source_world_id")
    ref = payload.get("character_ref")
    if not isinstance(source_id, str) or not isinstance(ref, dict):
        raise StructuredError("invalid_action", "新建需要当前世界与调查员引用。")
    digest = hashlib.sha256(
        json.dumps(
            {
                key: payload.get(key)
                for key in ("source_world_id", "character_ref", "execution_profile", "keeper_mode")
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    world_id = "local-start-" + hashlib.sha256(request_id.encode()).hexdigest()[:32]
    with session_scope(source.database_url) as session:
        existing = session.get(World, world_id)
        if existing is not None:
            receipt = (existing.metadata_json or {}).get("local_creation") or {}
            if receipt.get("digest") != digest:
                raise StructuredError("duplicate_request_conflict", "同一新建请求不能更换内容。")
            if existing.status != "active" or not receipt.get("ready"):
                raise StructuredError("invalid_action", "新建世界尚未完成或已归档，请核对存档。")
            module = existing.module_name
        else:
            module = source.module_name
    if existing is not None:
        return LocalCreation(
            RuntimeContext.create(
                world_id, module, project_root=source.project_root, runtime_root=source.runtime_root
            ),
            True,
            profile,
        )
    if source_id != source.world_id:
        raise StructuredError("world_mismatch", "当前世界已变化，请刷新开局页后再试。")
    card, normalized_ref = resolve_character(ref, module, context=source)
    if card is None or normalized_ref is None:
        raise StructuredError("object_not_found", "无法读取所选调查员，请重新选择。")
    context = RuntimeContext.create(
        world_id, module, project_root=source.project_root, runtime_root=source.runtime_root
    )
    with session_scope(context.database_url) as session:
        world = session.scalar(select(World).where(World.id == world_id).with_for_update())
        receipt = (world.metadata_json or {}).get("local_creation") or {}
        if receipt:
            if receipt.get("digest") != digest:
                raise StructuredError("duplicate_request_conflict", "同一新建请求不能更换内容。")
            return LocalCreation(context, True, profile)
        row = session.get(WorldState, world_id)
        state = copy.deepcopy(row.state)
        state["pc"] = character_to_pc(card, normalized_ref, state.get("pc", {}), module_name=module)
        for item in state.get("module_starting_inventory", []):
            if item not in state["pc"].setdefault("inventory", []):
                state["pc"]["inventory"].append(item)
        # Single local investigator, not a stale template roster from another session.
        state.pop("investigators", None)
        state.pop("active_investigator_id", None)
        if profile == "structured_v1":
            register_investigator_inventory(state, {"pc": state["pc"]})
        row.state = state
        row.revision = int(row.revision or 0) + 1
        world.metadata_json = apply_profile_metadata(
            world.metadata_json or {}, execution_profile=profile, keeper_mode=mode
        ) | {"local_creation": {"request_id": request_id, "digest": digest, "ready": True}}
        ensure_local_operator(session, world_id)
    return LocalCreation(context, False, profile)


def register_local_start(
    router: Any,
    engine: Any,
    wire: Any,
    *,
    allow_local,
    reserve,
    release,
    activate,
    context_payload,
    list_payload,
    save_panels,
) -> None:
    @router.handler("local_start")
    async def handle(payload: dict) -> None:
        request_id = payload.get("request_id")
        reply_id = request_id if isinstance(request_id, str) and len(request_id) <= 96 else ""
        reserved = False
        try:
            if not allow_local():
                raise StructuredError("not_authorized", "此入口仅供本地无账号游戏使用。")
            if not await reserve():
                return
            reserved = True
            result = await asyncio.to_thread(create_local_world, engine.context, payload)
            engine.switch_context(result.context)
            activate(result.context)
            await wire.outbound.send(context_payload())
            await wire.outbound.send(list_payload())
            if result.profile == "legacy" and not result.replayed:
                # Reuse the old opening only for explicitly selected legacy play.
                release()
                reserved = False
                if engine.client is None:
                    from src.app.config import API_KEY, BASE_URL, model_timeout_seconds

                    from .engine_gate import build_engine_client

                    engine.client = build_engine_client(
                        result.context,
                        api_key=API_KEY,
                        base_url=BASE_URL,
                        timeout=model_timeout_seconds(),
                    )
                await router.dispatch({"type": "start", "character_ref": payload["character_ref"]})
            await wire.outbound.send(
                {
                    "type": "local_start_result",
                    "request_id": reply_id,
                    "ok": True,
                    "world_id": result.context.world_id,
                    "execution_profile": result.profile,
                }
            )
            await wire.send_snapshot()
            await save_panels()
        except StructuredError as exc:
            await wire.outbound.send(
                {
                    "type": "local_start_result",
                    "request_id": reply_id,
                    "ok": False,
                    "code": exc.code,
                    "message": exc.message,
                }
            )
        except Exception:
            await wire.outbound.send(
                {
                    "type": "local_start_result",
                    "request_id": reply_id,
                    "ok": False,
                    "code": "local_start_failed",
                    "message": "新建暂未完成，请核对存档后重试原请求。",
                }
            )
        finally:
            if reserved:
                release()
