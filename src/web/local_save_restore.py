"""Restore local legacy saves without starting a model-generated turn."""

from __future__ import annotations

import asyncio

from src.ai.context.history_compactor import AUTHORITY_MARKER
from src.ai.context.narrative_history import enrich_public_history
from src.ai.tools.tool_protocol import strip_tool_protocol
from src.gameplay.choices import extract_action_choices
from src.gameplay.speaker_parser import parse_segments
from src.storage.database import SaveSlot, Snapshot, session_scope
from src.storage.database_store import StaleRevisionError
from src.storage.turn_journal import TurnJournalError
from src.web.asset_payload import SpeakerPayloadResolver, enrich_narrative_segments


def register_save_load_handler(
    router, *, engine, outbound, ws, auth_required, reserve_turn, release_turn,
    send_character_state, resume_game,
) -> None:
    @router.handler("save_load")
    async def handle_save_load(data: dict) -> None:
        if not auth_required() and getattr(ws, "room", None) is None:
            await restore_local_save(
                engine, outbound, str(data.get("slot_id") or ""),
                reserve_turn=reserve_turn, release_turn=release_turn,
                send_character_state=send_character_state,
            )
            return
        await resume_game(
            str(data.get("slot_id") or ""), announce_loaded=True,
            investigator_id=data.get("_room_investigator_id"),
            target_user_id=data.get("_room_actor_user_id"),
        )


def saved_public_history(engine, slot_id: str) -> list[dict]:
    """Project only public dialogue from the loaded slot, never future turns.

    Slots can predate the turn journal. Assistant prose is already public;
    system/control/tool messages and tool-call drafts must never reach the UI.
    These display IDs deliberately confer no rewrite/branch authority.
    """
    with session_scope(engine.context.database_url) as session:
        slot = session.query(SaveSlot).filter_by(
            world_id=engine.context.world_id, slot_key=slot_id,
        ).one_or_none()
        snapshot = session.get(Snapshot, slot.snapshot_id) if slot else None
        source_turn_id = snapshot.source_turn_id if snapshot else None
    if source_turn_id:
        try:
            return enrich_public_history(engine.turn_journal.public_history(source_turn_id), engine)
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
    return history


async def restore_local_save(
    engine, outbound, slot_id, *, reserve_turn, release_turn, send_character_state,
) -> None:
    if not await reserve_turn():
        return
    try:
        count = await asyncio.to_thread(engine.load, slot_id)
        if count is None:
            await outbound.send({
                "type": "error", "operation": "save_load",
                "message": "未找到存档。", "terminal": True,
            })
            return
        history = await asyncio.to_thread(saved_public_history, engine, slot_id)
        await send_character_state()
        await outbound.send({
            "type": "loaded", "ok": True, "slot_id": slot_id,
            "count": count, "restore_only": True,
            "world_id": engine.context.world_id,
            "module_name": engine.context.module_name, "history": history,
        })
    except StaleRevisionError as exc:
        await outbound.send({
            "type": "error", "operation": "save_load",
            "message": str(exc), "terminal": True,
        })
    finally:
        release_turn()
