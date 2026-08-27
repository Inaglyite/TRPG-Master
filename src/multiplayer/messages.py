"""Validated room-message dispatch for the authoritative multiplayer driver."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from src.ai.model.model_settings import ModelSettings
from src.auth.service import authorize_world, websocket_user
from src.gameplay.characters import list_character_options
from src.gameplay.combat import CombatError, assign_combat_actor
from src.gameplay.investigators import investigator_entity, visible_clues_for_investigator
from src.multiplayer.guards import (
    GUARDED_TURN_TYPES,
    USER_TURN_GUARD,
    check_action_guards,
)
from src.multiplayer.recovery import turn_recovery_payload
from src.multiplayer.room_runtime import ActionReservationError, GameRoom
from src.multiplayer.service import (
    MultiplayerError,
    claim_investigator,
    reserve_room_action,
    room_members,
)
from src.multiplayer.solo_timeline_ws import (
    SOLO_TIMELINE_MESSAGE_TYPES,
    handle_solo_timeline_message,
)
from src.storage.player_notes import PlayerNotesConflict, PlayerNotesStore
from src.web.asset_payload import enrich_pc_for_frontend

logger = logging.getLogger("trpg.multiplayer_messages")

UNSUPPORTED_ROOM_TYPES = frozenset(
    {
        "load",
        "quit",
        "world_switch",
        "switch_module",
        "turn_branch_create",
        "model_settings_update",
    }
)
OWNER_CONTROL_TYPES = frozenset(
    {"save", "save_delete", "save_create", "save_rename", "settle_case"}
)
MUTATING_TURN_TYPES = frozenset({"start", "continue", "save_load", "action", "turn_rewrite"})
OWNER_TURN_TYPES = frozenset({"start", "save_load", "turn_rewrite"})


def owner_turn_required(message_type: str, data: dict) -> bool:
    """Return whether this particular turn command requires room ownership."""
    return message_type in OWNER_TURN_TYPES or (
        message_type == "continue" and bool(str(data.get("slot_id") or "").strip())
    )


async def _reject(ws: Any, code: str, message: str) -> None:
    await ws.send_json(
        {
            "type": "room_action_rejected",
            "code": code,
            "message": message,
        }
    )


async def _protocol_error(ws: Any, code: str, **extra: Any) -> None:
    await ws.send_json({"type": "protocol_error", "code": code, **extra})


def _event_cursor(data: dict, key: str) -> int | None:
    try:
        value = int(data.get(key, 0))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def safe_multiplayer_diagnostics(report: Any) -> dict | None:
    """Keep operational timings while removing keeper-only IDs, text, and tool args."""
    if not isinstance(report, dict):
        return None

    def numeric_tree(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): sanitized
                for key, item in value.items()
                if (sanitized := numeric_tree(item)) is not None
            }
        if isinstance(value, list):
            sanitized = [numeric_tree(item) for item in value]
            return [item for item in sanitized if item is not None]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return None

    model_calls = []
    for call in report.get("model_calls") or []:
        if not isinstance(call, dict):
            continue
        model_calls.append(
            {
                key: call.get(key)
                for key in (
                    "model",
                    "role",
                    "status",
                    "elapsed_ms",
                    "first_token_ms",
                    "finish_reason",
                    "tool_count",
                    "message_count",
                    "prompt_profile",
                    "thinking_mode",
                    "error_type",
                )
                if call.get(key) is not None
            }
            | {
                key: numeric_tree(call.get(key))
                for key in ("context_sections", "usage")
                if numeric_tree(call.get(key)) is not None
            }
        )

    lore = report.get("lorebook") if isinstance(report.get("lorebook"), dict) else {}
    mutations = [item for item in (report.get("mutations") or []) if isinstance(item, dict)]
    safe = {
        key: report.get(key)
        for key in (
            "turn_id",
            "kind",
            "completed_at",
            "duration_ms",
            "world_revision",
            "message_count",
        )
        if report.get(key) is not None
    }
    safe.update(
        {
            "model_calls": model_calls,
            "lorebook": {
                "sequence": lore.get("sequence"),
                "token_estimate": lore.get("token_estimate"),
                "reason_counts": numeric_tree(lore.get("reason_counts") or {}),
                "selected_count": len(lore.get("selected") or []),
                "trace_count": len(lore.get("trace") or []),
            },
            "performance": numeric_tree(report.get("performance") or {}),
            "mutation_summary": {
                "total": len(mutations),
                "successful": sum(item.get("success") is True for item in mutations),
                "failed": sum(item.get("success") is False for item in mutations),
            },
            "tool_count": len(report.get("tool_names") or []),
            "event_counts": numeric_tree(report.get("event_counts") or {}),
        }
    )
    return safe


async def run_room_message_loop(
    controller: Any,
    ws: Any,
    room: GameRoom,
    user: Any,
    world_id: str,
    connection_id: str,
    initial_role: str,
) -> None:
    """Receive, authorize, validate, and forward one member connection."""
    role = initial_role
    while True:
        raw = await ws.receive_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await _protocol_error(ws, "invalid_json")
            continue
        if not isinstance(data, dict):
            await _protocol_error(ws, "invalid_message")
            continue
        if websocket_user(ws, controller.deps.database_url()) is None:
            await ws.close(code=4401, reason="登录会话已过期")
            return
        try:
            role = authorize_world(
                controller.deps.database_url(),
                user.id,
                world_id,
                "read",
            )
        except Exception:
            await ws.close(code=4403, reason="房间成员权限已被移除")
            return
        await room.hub.update_user_role(user.id, role)
        message_type = str(data.get("type") or "")

        if message_type == "ping":
            await ws.send_json({"type": "pong"})
            continue
        if message_type == "room_ack":
            cursor = _event_cursor(data, "event_id")
            accepted = cursor is not None and await room.hub.acknowledge(connection_id, cursor)
            if not accepted:
                await _protocol_error(ws, "invalid_room_ack")
            continue
        if message_type == "room_sync":
            cursor = _event_cursor(data, "after_event_id")
            if cursor is None:
                await _protocol_error(ws, "invalid_room_sync")
                continue
            replay = await room.hub.replay_to_connection(connection_id, cursor)
            if replay["gap"]:
                await controller.send_room_full_recovery(
                    ws,
                    room,
                    user.id,
                    role=role,
                    connection_id=connection_id,
                )
            elif not replay["delivered"]:
                raise RuntimeError("room replay delivery failed")
            continue
        if message_type == "room_ready":
            if role == "viewer":
                await _reject(ws, "player_required", "旁观者不能准备游戏")
                continue
            room.set_ready(user.id, bool(data.get("ready", True)))
            await controller.broadcast_room_state(room)
            continue
        if message_type == "actor_assign":
            if role != "owner":
                await _reject(ws, "owner_required", "只有房主可以指定行动者")
                continue
            if controller.room_control_change_blocked(room):
                await _reject(
                    ws,
                    "room_turn_in_progress",
                    "当前回合或确认请求结束前不能更换行动者",
                )
                continue
            target_user_id = str(data.get("user_id") or "")
            member_state = room_members(
                controller.deps.database_url(),
                world_id,
                user.id,
            )
            eligible = {
                member["user_id"]
                for member in member_state["members"]
                if member["role"] in {"owner", "player"}
            }
            if target_user_id not in eligible:
                await _reject(ws, "invalid_actor", "目标成员不能成为行动者")
                continue
            if target_user_id not in room.connected_users:
                await _reject(ws, "actor_offline", "目标成员当前不在线")
                continue
            combat_assignment: dict | None = None
            world_state = room.engine.context.world_store.load()
            combat = world_state.get("combat_state")
            if isinstance(combat, dict) and combat.get("active"):
                roster, _playable_members = controller.room_roster(world_id)
                target_claim = next(
                    (
                        claim
                        for claim in roster
                        if str(claim.get("user_id") or "") == target_user_id
                    ),
                    None,
                )
                if target_claim is None:
                    await _reject(
                        ws,
                        "investigator_required",
                        "目标成员需要先选择调查员",
                    )
                    continue
                target_investigator_id = str(target_claim["investigator_id"])
                try:
                    def assign_in_state(
                        state: dict,
                        investigator_id: str = target_investigator_id,
                    ) -> None:
                        nonlocal combat_assignment
                        combat_assignment = assign_combat_actor(
                            state,
                            investigator_id,
                        )

                    room.engine.context.world_store.update(assign_in_state)
                except (CombatError, KeyError, RuntimeError, ValueError) as exc:
                    await _reject(ws, "invalid_combat_actor", str(exc))
                    continue
            room.assign_actor(target_user_id)
            controller.persist_room_control(room)
            if combat_assignment is not None:
                await room.hub.broadcast(
                    {
                        "type": "combat_actor_changed",
                        "user_id": target_user_id,
                        "investigator_id": combat_assignment["actor_id"],
                        "skipped_actor_ids": combat_assignment["skipped_actor_ids"],
                        "round": combat_assignment["round"],
                    }
                )
            await room.hub.broadcast({"type": "actor_changed", "user_id": target_user_id})
            await controller.broadcast_room_state(room)
            continue
        if message_type in {"player_notes_get", "player_notes_update"}:
            notes_store = PlayerNotesStore(
                room.engine.context.world_dir,
                user_id=user.id,
            )
            broadcast = False
            try:
                if message_type == "player_notes_get":
                    payload = {"type": "player_notes", **notes_store.load()}
                else:
                    saved = notes_store.save(
                        data.get("text", ""),
                        expected_revision=(
                            int(data["revision"]) if data.get("revision") is not None else None
                        ),
                    )
                    payload = {"type": "player_notes", "saved": True, **saved}
                    broadcast = True
            except PlayerNotesConflict as exc:
                payload = {
                    "type": "player_notes_conflict",
                    "message": str(exc),
                    **notes_store.load(),
                }
            except (OSError, TypeError, ValueError, RuntimeError) as exc:
                print(
                    f"[room] 玩家笔记访问失败: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                payload = {
                    "type": "player_notes_error",
                    "message": "玩家笔记暂时不可用，请稍后重试",
                }
            if broadcast:
                await room.hub.broadcast(payload, visibility=f"user:{user.id}")
            else:
                await ws.send_json(payload)
            continue
        if message_type == "state":
            try:
                world_state = room.engine.context.world_store.load()
                investigator_id = controller.authoritative_investigator_id(
                    world_id,
                    user.id,
                    role,
                )
                own_pc = (
                    investigator_entity(world_state, investigator_id)
                    if investigator_id
                    else {}
                )
                pc_data = enrich_pc_for_frontend(
                    own_pc if isinstance(own_pc, dict) else {},
                    room.engine.context,
                )
                clues_data = controller.deps.enrich_clues(
                    visible_clues_for_investigator(
                        world_state.get("clues_found", {}),
                        investigator_id,
                    ),
                    world_state,
                    room.engine.context,
                )
            except Exception:
                pc_data, clues_data = {}, {}
            await ws.send_json(
                {
                    "type": "state_data",
                    "data": json.dumps(pc_data, ensure_ascii=False),
                    "clues": json.dumps(clues_data, ensure_ascii=False),
                }
            )
            continue
        if message_type == "turn_recovery_get":
            await ws.send_json(
                turn_recovery_payload(room.engine, data.get("turn_id"))
            )
            continue
        if message_type == "module_list":
            await ws.send_json(
                {
                    "type": "module_list",
                    "modules": controller.deps.list_modules(),
                    "active": room.engine.context.module_name,
                }
            )
            continue
        if message_type == "character_list":
            await ws.send_json(
                {
                    "type": "character_list",
                    **list_character_options(
                        room.engine.context.module_name,
                        context=room.engine.context,
                        include_personal=False,
                    ),
                }
            )
            continue
        if message_type == "save_list":
            await ws.send_json({"type": "save_list", "saves": room.engine.list_saves()})
            continue
        if message_type == "model_settings_get":
            await ws.send_json(
                controller.deps.model_settings_payload(
                    ModelSettings.validated(
                        room.engine.narrative_model,
                        room.engine.judgement_model,
                    )
                )
            )
            continue
        if message_type == "turn_diagnostics_get":
            if role != "owner":
                await _reject(
                    ws,
                    "owner_required",
                    "只有房主可以查看回合诊断",
                )
            else:
                await ws.send_json(
                    {
                        "type": "turn_diagnostics",
                        "diagnostics": safe_multiplayer_diagnostics(
                            room.engine.turn_diagnostics(data.get("turn_id"))
                        ),
                    }
                )
            continue
        if message_type in {"suggest_reply", "decision_reply"}:
            reply_kind = "suggest" if message_type == "suggest_reply" else "decision"
            expected_user_id = (
                room.current_actor_user_id
                if reply_kind == "suggest"
                else room.pending_reply_user_id
            )
            if expected_user_id != user.id:
                await _reject(
                    ws,
                    "not_current_actor",
                    "只有这项确认所对应的调查员可以作出决定",
                )
                continue
            if not room.accept_pending_reply(
                reply_kind,
                user.id,
                request_id=data.get("decision_id"),
            ):
                await _reject(
                    ws,
                    "stale_reply",
                    "该确认请求已经失效或尚未发起",
                )
                continue
        if message_type in SOLO_TIMELINE_MESSAGE_TYPES:
            # 云端单人房间的专用时间线协议；多人房间在 _gate 内继续被拒绝。
            # 切换/建分支成功后旧房间被拆除，本连接随断开流程退出。
            outcome = await handle_solo_timeline_message(
                controller,
                ws,
                room,
                user,
                role,
                data,
            )
            if outcome == "close":
                return
            continue
        if message_type in UNSUPPORTED_ROOM_TYPES:
            await _reject(
                ws,
                "unsupported_in_room",
                "该操作不能在共享房间中执行",
            )
            continue
        if message_type in OWNER_CONTROL_TYPES and role != "owner":
            await _reject(ws, "owner_required", "只有房主可以执行房间管理操作")
            continue
        if message_type in OWNER_CONTROL_TYPES:
            if message_type == "settle_case" and room.status != "playing":
                await _reject(
                    ws,
                    "room_not_playing",
                    "房间尚未进入游戏，不能结算案件",
                )
                continue
            action_id = str(data.get("action_id") or "")
            try:
                await room.reserve_control(user.id, action_id)
            except ActionReservationError as exc:
                await _reject(ws, exc.code, str(exc))
                continue
            try:
                reserve_room_action(
                    controller.deps.database_url(),
                    world_id,
                    action_id,
                    user.id,
                    message_type,
                    required_permission="manage",
                )
            except MultiplayerError as exc:
                room.release_action()
                await _reject(ws, exc.code, str(exc))
                continue
            except Exception:
                room.release_action()
                await _reject(
                    ws,
                    "reservation_unavailable",
                    "行动暂时无法登记，请使用新的行动 ID 后重试",
                )
                continue
            data["_room_reserved_action"] = True
        if message_type in MUTATING_TURN_TYPES:
            requires_owner = owner_turn_required(message_type, data)
            if requires_owner and role != "owner":
                await _reject(
                    ws,
                    "owner_required",
                    "只有房主可以执行该房间控制操作",
                )
                continue
            if message_type != "start" and room.status != "playing":
                await _reject(
                    ws,
                    "room_not_playing",
                    "房间尚未完成开场，当前不能推进或恢复回合",
                )
                continue
            action_id = str(data.get("action_id") or "")
            try:
                await room.reserve_action(
                    user.id,
                    action_id,
                    require_current_actor=not requires_owner,
                )
            except ActionReservationError as exc:
                await _reject(ws, exc.code, str(exc))
                continue
            try:
                roster, playable_members = controller.room_roster(world_id)
                claims_by_user = {
                    str(item["user_id"]): item for item in roster if item.get("user_id")
                }
                actor_id = room.current_actor_user_id or user.id
                actor_claim = claims_by_user.get(actor_id)
            except Exception:
                room.release_action()
                raise
            solo_start = message_type == "start" and room.play_mode == "solo"
            if solo_start:
                # 私密单人世界：行动者固定为房主本人。
                actor_id = room.owner_user_id
                actor_claim = claims_by_user.get(actor_id)
            if actor_claim is None and solo_start and user.id == room.owner_user_id:
                # 无认领调查员时自动认领第一个可用的默认/模组调查员；
                # include_personal=False 沿用 personal_character_unavailable
                # 规则，排除 profile/custom 来源。
                try:
                    options = list_character_options(
                        room.engine.context.module_name,
                        context=room.engine.context,
                        include_personal=False,
                    )
                    selected = next(
                        (
                            character
                            for group in options.get("groups", [])
                            for character in group.get("characters", [])
                            if isinstance(character.get("ref"), dict)
                        ),
                        None,
                    )
                    if selected is not None:
                        claim_investigator(
                            controller.deps.database_url(),
                            world_id,
                            str(selected.get("id") or ""),
                            user.id,
                            character_ref=selected["ref"],
                        )
                        roster, playable_members = controller.room_roster(world_id)
                        claims_by_user = {
                            str(item["user_id"]): item
                            for item in roster
                            if item.get("user_id")
                        }
                        actor_claim = claims_by_user.get(actor_id)
                except Exception:
                    room.release_action()
                    raise
            if actor_claim is None:
                room.release_action()
                await _reject(
                    ws,
                    "investigator_required",
                    "当前行动者需要先选择调查员",
                )
                continue
            if message_type == "start":
                if room.status != "lobby":
                    room.release_action()
                    await _reject(
                        ws,
                        "room_already_started",
                        "房间已经开始游戏",
                    )
                    continue
                if solo_start:
                    # 私密单人世界跳过 ready/claims/online 全量门禁，
                    # 仅要求房主本人已连接（发起者即房主，此为防御性检查）。
                    if room.owner_user_id not in room.connected_users:
                        room.release_action()
                        await _reject(
                            ws,
                            "owner_offline",
                            "房主不在线，无法开始游戏",
                        )
                        continue
                else:
                    missing_claims = sorted(playable_members - claims_by_user.keys())
                    missing_ready = sorted(playable_members - room.ready_users)
                    missing_online = sorted(playable_members - room.connected_users.keys())
                    if missing_claims or missing_ready or missing_online:
                        room.release_action()
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "room_not_ready",
                                "message": "所有玩家选择调查员并准备后才能开始",
                                "missing_claim_user_ids": missing_claims,
                                "missing_ready_user_ids": missing_ready,
                                "missing_online_user_ids": missing_online,
                            }
                        )
                        continue
                    if any(
                        str((item.get("character_ref") or {}).get("source") or "")
                        in {"profile", "custom"}
                        for item in roster
                    ):
                        room.release_action()
                        await _reject(
                            ws,
                            "personal_character_unavailable",
                            "联机模式暂不读取服务器上的本地长期或自定义角色；请改选默认或模组调查员",
                        )
                        continue
            if message_type in GUARDED_TURN_TYPES:
                try:
                    check_action_guards(user.id, world_id, action_id)
                except MultiplayerError as exc:
                    room.release_action()
                    await _reject(ws, exc.code, str(exc))
                    continue
            try:
                reserve_room_action(
                    controller.deps.database_url(),
                    world_id,
                    action_id,
                    user.id,
                    message_type,
                    required_permission="manage" if requires_owner else "play",
                )
            except MultiplayerError as exc:
                room.release_action()
                if message_type in GUARDED_TURN_TYPES:
                    USER_TURN_GUARD.release(user.id, world_id, action_id)
                await _reject(ws, exc.code, str(exc))
                continue
            except Exception:
                room.release_action()
                if message_type in GUARDED_TURN_TYPES:
                    USER_TURN_GUARD.release(user.id, world_id, action_id)
                await _reject(
                    ws,
                    "reservation_unavailable",
                    "行动暂时无法登记，请使用新的行动 ID 后重试",
                )
                continue
            data["_room_reserved_action"] = True
            if message_type in GUARDED_TURN_TYPES:
                logger.info(
                    "房间生成行动已登记 user_id=%s world_id=%s action_id=%s type=%s",
                    user.id,
                    world_id,
                    action_id,
                    message_type,
                )
            if message_type == "start":
                controller.set_room_status(room, "starting")
                room.control_action_active = True
                await controller.broadcast_room_state(room)
                data["character_ref"] = actor_claim["character_ref"]
                data["_room_roster"] = roster
            data["_room_investigator_id"] = actor_claim["investigator_id"]
            data["_room_actor_user_id"] = actor_id
        passthrough_types = (
            MUTATING_TURN_TYPES | OWNER_CONTROL_TYPES | {"suggest_reply", "decision_reply"}
        )
        if message_type not in passthrough_types:
            await _protocol_error(
                ws,
                "unsupported_room_message",
                message_type=message_type,
            )
            continue
        data["_room_user_id"] = user.id
        data["_room_connection_id"] = connection_id
        try:
            if room.driver_transport is None:
                raise RuntimeError("room driver is unavailable")
            await room.driver_transport.submit(json.dumps(data, ensure_ascii=False))
        except Exception:
            if message_type == "start" and room.status == "starting":
                controller.set_room_status(room, "lobby")
                await controller.broadcast_room_state(room)
            room.release_action(terminal_status="failed")
            raise
