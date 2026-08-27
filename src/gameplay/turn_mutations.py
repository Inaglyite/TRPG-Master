"""Structured record of authoritative mutations completed during one turn."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

AUTHORITATIVE_TOOLS = frozenset({
    "state_set", "state_add_clue", "state_add_item", "state_remove_item",
    "npc_reveal", "use_item", "sanity_event", "sanity_loss", "sanity_restore",
    "apply_damage", "apply_heal", "combat_start", "combat_action", "combat_end",
    "set_psychological_trait", "end_game",
})


@dataclass
class TurnMutationLedger:
    entries: list[dict] = field(default_factory=list)

    def record_tool(self, name: str, args: dict, output: str) -> None:
        if name not in AUTHORITATIVE_TOOLS:
            return
        success = not str(output).startswith(("[错误]", "[异常]", "[超时]"))
        try:
            decoded = json.loads(output)
            if isinstance(decoded, dict) and decoded.get("ok") is False:
                success = False
        except (json.JSONDecodeError, TypeError):
            pass
        self.entries.append({"source": "tool", "name": name, "args": dict(args),
                             "success": success})

    def record_domain(self, name: str, details: dict | None = None) -> None:
        self.entries.append({"source": "domain", "name": name,
                             "details": dict(details or {}), "success": True})

    @property
    def has_authoritative_mutation(self) -> bool:
        return any(entry.get("success") for entry in self.entries)

    def snapshot(self) -> list[dict]:
        return [dict(entry) for entry in self.entries]
