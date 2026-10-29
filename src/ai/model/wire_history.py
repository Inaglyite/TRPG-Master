"""Bounded, client-local replay of provider metadata for active tool chains.

Reasoning/signatures stay in memory at the HTTP boundary. They never enter player
history, persistence, diagnostics, or logs. Matching includes tool arguments.
"""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict


def call_key(calls: list[dict]) -> str:
    values = []
    for call in calls:
        function = call.get("function") or {}
        arguments = function.get("arguments", "{}")
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (ValueError, TypeError):
            pass
        values.append([call.get("id"), function.get("name"), arguments])
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WireHistory:
    def __init__(self) -> None:
        self._entries: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()

    def remember(self, calls: list[dict], value: object) -> None:
        if not calls or not all(call.get("id") for call in calls):
            return
        if len(json.dumps(value, ensure_ascii=False)) > 512_000:
            return
        with self._lock:
            key = call_key(calls)
            self._entries[key] = copy.deepcopy(value)
            self._entries.move_to_end(key)
            while len(self._entries) > 32:
                self._entries.popitem(last=False)

    def recall(self, calls: list[dict]):
        with self._lock:
            return copy.deepcopy(self._entries.get(call_key(calls)))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
