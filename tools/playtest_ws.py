#!/usr/bin/env python3
"""Interactive WebSocket client for end-to-end playtesting."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import websockets


def _clue_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        return {"count": 0, "latest": []}
    items = []
    for category, clues in payload.items():
        if not isinstance(clues, list):
            continue
        for clue in clues:
            if isinstance(clue, dict):
                items.append({
                    "id": clue.get("id", ""),
                    "category": category,
                    "text": clue.get("text", ""),
                    "asset_id": (clue.get("asset") or {}).get("id", ""),
                })
    return {"count": len(items), "latest": items[-5:]}


class PlaytestClient:
    def __init__(self, url: str, log_path: Path) -> None:
        self.url = url
        self.log_path = log_path
        self.websocket: Any = None
        self.send_lock = asyncio.Lock()
        self.characters: list[dict] = []
        self.narrative_chunks: list[str] = []
        self.turn_started_at = 0.0
        self.turn_number = 0
        self.next_decision: str | None = None

    def log(self, direction: str, payload: Any) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logged_payload = payload
        if isinstance(payload, dict) and payload.get("asset_data_uri"):
            logged_payload = {**payload, "asset_data_uri": "<data-uri>"}
        entry = {
            "timestamp": time.time(),
            "direction": direction,
            "payload": logged_payload,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            self.log("out", payload)
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def handle_event(self, payload: dict) -> None:
        self.log("in", payload)
        event_type = payload.get("type", "")
        if event_type == "module_list":
            modules = [item.get("title") or item.get("name") for item in payload.get("modules", [])]
            print(f"[modules] active={payload.get('active')} available={modules}", flush=True)
        elif event_type == "character_list":
            self.characters = [
                character
                for group in payload.get("groups", [])
                for character in group.get("characters", [])
            ]
            print(
                "[characters] " + ", ".join(
                    f"{item.get('name')}({item.get('occupation')})"
                    for item in self.characters
                ),
                flush=True,
            )
        elif event_type == "save_list":
            saves = [f"{item.get('id')}:{item.get('scene_name')}" for item in payload.get("saves", [])]
            print(f"[saves] {saves}", flush=True)
        elif event_type == "gm_turn_start":
            self.turn_number += 1
            self.turn_started_at = time.monotonic()
            self.narrative_chunks = []
            print(f"\n[turn {self.turn_number} started]", flush=True)
        elif event_type == "narrative_chunk":
            self.narrative_chunks.append(str(payload.get("text") or ""))
        elif event_type == "tension":
            print(f"[tension:{payload.get('category')}] {payload.get('text')}", flush=True)
        elif event_type == "dice_result":
            print(
                f"[dice] {payload.get('summary')} | {payload.get('roll_data')}",
                flush=True,
            )
        elif event_type == "glm_summary":
            print(f"[tool-summary] {payload.get('text')}", flush=True)
        elif event_type == "handout":
            print(
                f"[handout] {payload.get('entity_type')}:{payload.get('entity_id')} "
                f"asset={payload.get('asset_id')} label={payload.get('label')}",
                flush=True,
            )
        elif event_type == "suggest_check":
            print(
                f"[check] {payload.get('description')} skill={payload.get('skill')} "
                f"dc={payload.get('dc')} -> accept",
                flush=True,
            )
            await self.send({"type": "suggest_reply", "confirmed": True})
        elif event_type == "decision_request":
            options = payload.get("options", [])
            valid_ids = {item.get("id") for item in options if isinstance(item, dict)}
            selected = self.next_decision if self.next_decision in valid_ids else None
            selected = selected or payload.get("default_option")
            self.next_decision = None
            print(
                f"[decision] {payload.get('prompt')} options={sorted(valid_ids)} -> {selected}",
                flush=True,
            )
            await self.send({
                "type": "decision_reply",
                "decision_id": payload.get("id"),
                "option_id": selected,
            })
        elif event_type == "character_state":
            raw_pc = payload.get("data", {})
            pc = json.loads(raw_pc) if isinstance(raw_pc, str) else raw_pc
            print(
                f"[character] {pc.get('name')} {pc.get('occupation')} "
                f"HP={pc.get('hp')}/{pc.get('max_hp')} SAN={pc.get('san')}/{pc.get('max_san')}",
                flush=True,
            )
        elif event_type == "state_data":
            raw_pc = payload.get("data", {})
            pc = json.loads(raw_pc) if isinstance(raw_pc, str) else raw_pc
            print(
                "[state] " + json.dumps({
                    "name": pc.get("name"),
                    "hp": pc.get("hp"),
                    "san": pc.get("san"),
                    "inventory": pc.get("inventory", []),
                    "clues": _clue_summary(payload.get("clues")),
                }, ensure_ascii=False),
                flush=True,
            )
        elif event_type == "done":
            elapsed = time.monotonic() - self.turn_started_at if self.turn_started_at else 0
            narrative = "".join(self.narrative_chunks).strip()
            print(f"[narrative]\n{narrative}", flush=True)
            print(f"[turn {self.turn_number} done in {elapsed:.2f}s]", flush=True)
            await self.send({"type": "state"})
        elif event_type == "game_over":
            print(
                f"[game-over] {payload.get('ending_type')} {payload.get('title')}\n"
                f"{payload.get('summary')}",
                flush=True,
            )
        elif event_type in {"error", "loaded", "saved", "save_available"}:
            print(f"[{event_type}] {json.dumps(payload, ensure_ascii=False)}", flush=True)

    async def reader(self) -> None:
        async for raw in self.websocket:
            payload = json.loads(raw)
            try:
                await self.handle_event(payload)
            except Exception as exc:
                print(f"[client-event-error] {type(exc).__name__}: {exc}", flush=True)

    def character_ref(self, name: str) -> dict | None:
        for character in self.characters:
            if character.get("name") == name:
                return character.get("ref")
        return None

    async def command_loop(self) -> None:
        while True:
            raw = await asyncio.to_thread(sys.stdin.readline)
            if not raw:
                return
            command = raw.strip()
            if not command:
                continue
            if command.startswith("/start"):
                name = command.removeprefix("/start").strip() or "黄千陆"
                ref = self.character_ref(name)
                if not ref:
                    print(f"[client-error] character not found: {name}", flush=True)
                    continue
                await self.send({"type": "start", "character_ref": ref})
            elif command.startswith("/continue"):
                slot_id = command.removeprefix("/continue").strip() or "slot_000"
                await self.send({"type": "continue", "slot_id": slot_id})
            elif command.startswith("/choose"):
                self.next_decision = command.removeprefix("/choose").strip() or None
                print(f"[next-decision] {self.next_decision}", flush=True)
            elif command == "/state":
                await self.send({"type": "state"})
            elif command == "/saves":
                await self.send({"type": "save_list"})
            elif command == "/save":
                await self.send({"type": "save", "manual": False})
            elif command == "/manual-save":
                await self.send({"type": "save_create"})
            elif command in {"/exit", "/quit"}:
                return
            else:
                print(f"[action] {command}", flush=True)
                await self.send({"type": "action", "content": command})

    async def run(self) -> None:
        async with websockets.connect(self.url, max_size=16 * 1024 * 1024) as websocket:
            self.websocket = websocket
            reader_task = asyncio.create_task(self.reader())
            try:
                await self.command_loop()
            finally:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(PlaytestClient(args.url, args.log).run())


if __name__ == "__main__":
    main()
