"""Private reasoning from non-streaming keeper calls, outside the event outbox."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.ai.model.reasoning_display import ReasoningDisplay


async def publish_reasoning(
    world_id, model, text, deliver=None, *, trigger_request_id="", status="completed"
):
    if not isinstance(text, str) or not text:
        return
    events = []
    host = SimpleNamespace(
        context=SimpleNamespace(world_id=world_id),
        trigger_request_id=trigger_request_id,
        cb=SimpleNamespace(on_private_event=events.append),
    )
    display = ReasoningDisplay(host, f"agent:{uuid4().hex}", model, "keeper_agent")
    display.feed_text(text)
    display.finish(status)
    if deliver is not None:
        for event in events:
            try:
                await deliver(event)
            except Exception:
                # A display connection cannot invalidate an already received model result.
                break
