"""Local websocket notes endpoints, separate from gameplay state."""

from src.storage.notes_reply_context import notes_reply_context
from src.storage.player_notes import PlayerNotesConflict, PlayerNotesStore


def register_player_notes_handlers(router, engine, outbound, *, user_id=None) -> None:
    async def respond(data: dict, *, update: bool) -> None:
        context = engine.context
        reply_context = notes_reply_context(context.world_id, data)
        if "world_id" in data and data["world_id"] != context.world_id:
            await outbound.send(
                {
                    "type": "player_notes_error",
                    "message": "笔记所属世界已变化，未读取或保存。请重新打开当前世界的笔记。",
                    **reply_context,
                }
            )
            return
        try:
            store = PlayerNotesStore(context.world_dir, user_id=user_id)
            try:
                if update:
                    notes = store.save(
                        data.get("text", ""),
                        expected_revision=int(data["revision"])
                        if data.get("revision") is not None
                        else None,
                    )
                    payload = {"type": "player_notes", "saved": True, **notes}
                else:
                    payload = {"type": "player_notes", **store.load()}
            except PlayerNotesConflict as exc:
                payload = {"type": "player_notes_conflict", "message": str(exc), **store.load()}
        except (OSError, TypeError, ValueError, RuntimeError):
            payload = {"type": "player_notes_error", "message": "玩家笔记暂时不可用，请稍后重试"}
        await outbound.send({**payload, **reply_context})

    @router.handler("player_notes_get")
    async def get_notes(data: dict) -> None:
        await respond(data, update=False)

    @router.handler("player_notes_update")
    async def update_notes(data: dict) -> None:
        await respond(data, update=True)
