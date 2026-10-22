"""Restore local legacy saves without starting a model-generated turn."""

from __future__ import annotations

import asyncio
import json
import math
import re
from time import perf_counter

from src.ai.context.context_checkpoint import ContextCheckpoint
from src.ai.context.history_compactor import AUTHORITY_MARKER
from src.ai.context.narrative_history import enrich_public_history
from src.ai.tools.tool_protocol import strip_tool_protocol
from src.app.logger import get as get_logger
from src.gameplay.choices import extract_action_choices
from src.gameplay.speaker_parser import parse_segments
from src.storage.database import SaveSlot, Snapshot, World, session_scope
from src.storage.database_store import StaleRevisionError
from src.storage.turn_journal import TurnJournalError
from src.web.asset_payload import SpeakerPayloadResolver, enrich_narrative_segments


def register_save_load_handler(
    router, *, engine, outbound, ws, auth_required, reserve_turn, release_turn,
    send_character_state, resume_game,
) -> None:
    pending_timings: set[str] = set()

    @router.handler("save_load")
    async def handle_save_load(data: dict) -> None:
        if not auth_required() and getattr(ws, "room", None) is None:
            request_id = timing_request_id(data.get("request_id"))
            if request_id:
                # Bound outstanding acknowledgements on this connection.
                pending_timings.clear()
                pending_timings.add(request_id)
            await restore_local_save(
                engine, outbound, str(data.get("slot_id") or ""),
                reserve_turn=reserve_turn, release_turn=release_turn,
                send_character_state=send_character_state,
                request_id=request_id,
            )
            return
        await resume_game(
            str(data.get("slot_id") or ""), announce_loaded=True,
            investigator_id=data.get("_room_investigator_id"),
            target_user_id=data.get("_room_actor_user_id"),
        )

    @router.handler("save_load_timing")
    async def handle_save_load_timing(data: dict) -> None:
        if auth_required() or getattr(ws, "room", None) is not None:
            return
        request_id = timing_request_id(data.get("request_id"))
        if not request_id or request_id not in pending_timings:
            return
        pending_timings.discard(request_id)
        metrics = {}
        for key in ("total_ms", "response_ms", "presentation_ms", "server_ms"):
            value = data.get(key)
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86_400_000:
                metrics[key] = round(value)
        get_logger().info("SAVE_LOAD client_ready %s", json.dumps({"request_id": request_id, **metrics}))


def timing_request_id(value) -> str:
    """Correlation labels only; never accept arbitrary log text from a client."""
    return value if isinstance(value, str) and re.fullmatch(r"save-[a-zA-Z0-9_-]{1,64}", value) else ""


def saved_public_history(engine, slot_id: str) -> list[dict]:
    return _saved_history_projection(engine, slot_id)[0]


def _saved_history_projection(engine, slot_id: str) -> tuple[list[dict], str]:
    """Project only public dialogue from the loaded slot, never future turns.

    Slots can predate the turn journal. Assistant prose is already public;
    system/control/tool messages and tool-call drafts must never reach the UI.
    Fallback text-only display IDs confer no rewrite/branch authority.
    """
    with session_scope(engine.context.database_url) as session:
        slot = session.query(SaveSlot).filter_by(
            world_id=engine.context.world_id, slot_key=slot_id,
        ).one_or_none()
        snapshot = session.get(Snapshot, slot.snapshot_id) if slot else None
        source_turn_id = snapshot.source_turn_id if snapshot else None
        if not source_turn_id and slot and (slot.metadata_json or {}).get("context"):
            try:
                source_turn_id = ContextCheckpoint.from_mapping(slot.metadata_json["context"]).source_turn_id
            except (TypeError, ValueError):
                pass  # Invalid private metadata cannot grant branch authority.
        branch_source = None
        if not source_turn_id and slot:
            world = session.get(World, engine.context.world_id)
            branch_source = ((world.metadata_json or {}).get("branch") or {}).get("source_turn_id") if world else None
            saved_messages = slot.messages or []
    if not source_turn_id and branch_source:
        try:
            # Older branch auto-saves predate snapshot source pointers. Its
            # branch root is usable only if the saved message history matches
            # that exact committed turn; never substitute the latest turn.
            branch_messages, _ = engine.turn_journal.load_artifacts(branch_source)
            if saved_messages[1:] == branch_messages[1:]:
                source_turn_id = branch_source
        except TurnJournalError:
            pass
    if source_turn_id:
        try:
            return (
                enrich_public_history(engine.turn_journal.public_history(source_turn_id), engine),
                "turn_journal",
            )
        except TurnJournalError:
            pass  # Pre-journal/imported slots can only restore their saved prose.
    history = []
    player_input = None
    resolve_speaker = SpeakerPayloadResolver(engine)
    for index, message in enumerate(engine.messages):
        content = str(message.get("content") or "")
        if message.get("role") == "user" and content.startswith("[玩家行动]"):
            player_input = content.removeprefix("[玩家行动]").split(AUTHORITY_MARKER)[0].strip()
        if message.get("role") != "assistant" or message.get("tool_calls"):
            continue
        content = strip_tool_protocol(content)
        if not content.strip():
            continue
        segments, text = parse_segments(content, is_valid_npc=engine.is_valid_npc_id)
        history.append({
            "turn_id": f"saved-message-{index}",
            "player_input": player_input,
            "narrative": text,
            "narrative_segments": enrich_narrative_segments(
                [segment.to_dict() for segment in segments], resolve_speaker
            ),
            "choices": extract_action_choices(text),
        })
        player_input = None
    return history, "saved_messages"


async def restore_local_save(
    engine, outbound, slot_id, *, reserve_turn, release_turn, send_character_state,
    request_id="",
) -> None:
    started = perf_counter()
    timings = {}

    async def progress(phase):
        if request_id:
            await outbound.send({"type": "save_load_progress", "request_id": request_id, "phase": phase})

    get_logger().info("SAVE_LOAD started request_id=%s", request_id or "legacy")
    if not await reserve_turn():
        return
    try:
        await progress("restoring")
        phase_start = perf_counter()
        count = await asyncio.to_thread(engine.load, slot_id)
        timings["restore_ms"] = round((perf_counter() - phase_start) * 1000)
        if count is None:
            await outbound.send({
                "type": "error", "operation": "save_load",
                "message": "未找到存档。", "terminal": True,
            })
            return
        await progress("history")
        phase_start = perf_counter()
        history, history_source = await asyncio.to_thread(_saved_history_projection, engine, slot_id)
        if history:
            from src.ai.context.narrative_history import current_history_choices

            history[-1]["current_choices"] = current_history_choices(history, engine.context.world_store.load())
        timings["history_ms"] = round((perf_counter() - phase_start) * 1000)
        await progress("character")
        phase_start = perf_counter()
        await send_character_state()
        timings["character_ms"] = round((perf_counter() - phase_start) * 1000)
        timings["server_ms"] = round((perf_counter() - started) * 1000)
        await outbound.send({
            "type": "loaded", "ok": True, "slot_id": slot_id,
            "count": count, "restore_only": True,
            "world_id": engine.context.world_id,
            "module_name": engine.context.module_name, "history": history,
            "history_source": history_source,
            "request_id": request_id, "timings": timings,
        })
        get_logger().info("SAVE_LOAD restored %s", json.dumps({"request_id": request_id, **timings}))
    except StaleRevisionError as exc:
        await outbound.send({
            "type": "error", "operation": "save_load",
            "message": str(exc), "terminal": True,
        })
    except Exception as exc:
        get_logger().error("SAVE_LOAD failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        await outbound.send({
            "type": "error", "operation": "save_load",
            "message": "读取存档失败，请重试。", "terminal": True,
        })
    finally:
        release_turn()
