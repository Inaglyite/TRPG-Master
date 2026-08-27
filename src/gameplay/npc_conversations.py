"""Deterministic bounded memory derived from finalized narrative segments."""

from __future__ import annotations

import hashlib
from typing import Any


def commit_npc_conversations(engine: Any, segments: list[dict]) -> int:
    """Persist spoken NPC facts without granting clues or another model pass."""
    candidates: list[tuple[str, str, str]] = []
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("kind") != "speech":
            continue
        npc_id = str(segment.get("npc_id") or "")
        text = " ".join(str(segment.get("text") or "").split())
        if not npc_id or len(text) < 12:
            continue
        digest = hashlib.sha256(f"{npc_id}\0{text}".encode()).hexdigest()[:16]
        candidates.append((npc_id, digest, text[:600]))
    if not candidates:
        return 0
    added = 0
    with engine.context.world_store.transaction() as world:
        memory = world.setdefault("npc_conversations", {})
        for npc_id, digest, text in candidates:
            entries = memory.setdefault(npc_id, [])
            if any(
                isinstance(entry, dict) and entry.get("id") == digest
                for entry in entries
            ):
                continue
            entries.append({"id": digest, "text": text})
            del entries[:-20]
            added += 1
    return added
