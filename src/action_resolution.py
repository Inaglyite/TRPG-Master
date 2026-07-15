"""Authoritative planning boundary for one player action.

Natural-language input may contain a destination, a longer-term intention, and
an eventual interaction in one sentence.  This module decides how much of that
sentence becomes authoritative in the current turn.  Downstream systems must
consume this resolution instead of independently promoting intention to fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .action_checks import infer_scene_transition
from .discovery import DiscoveryMatch, match_discovery_rules, preferred_check_skill


class ActionPhase(StrEnum):
    ARRIVAL = "arrival"
    INTERACTION = "interaction"
    CONTACT = "contact"


@dataclass(frozen=True)
class ActionResolution:
    """The authoritative extent of a player action for this turn."""

    player_input: str
    phase: ActionPhase
    origin_scene_id: str
    destination_scene_id: str | None = None
    discovery_matches: tuple[DiscoveryMatch, ...] = ()
    preferred_skill: str | None = None

    @property
    def is_arrival(self) -> bool:
        return self.phase is ActionPhase.ARRIVAL

    @property
    def permits_discovery_effects(self) -> bool:
        return self.phase is ActionPhase.CONTACT

    def public_contract(self) -> dict:
        return {
            "phase": self.phase.value,
            "origin_scene_id": self.origin_scene_id,
            "destination_scene_id": self.destination_scene_id,
            "permits_discovery_effects": self.permits_discovery_effects,
        }


def plan_player_action(content: str, world: dict) -> ActionResolution:
    """Plan one action without mutating the world.

    A scene crossing always ends at ARRIVAL.  Clauses describing what the
    player hopes to do after arriving remain intention until a later turn.
    Within the current scene, a declared module discovery becomes CONTACT;
    everything else remains ordinary INTERACTION.
    """
    origin = str((world.get("current_scene") or {}).get("id") or "")
    destination = infer_scene_transition(content, world)
    if destination:
        return ActionResolution(
            player_input=content,
            phase=ActionPhase.ARRIVAL,
            origin_scene_id=origin,
            destination_scene_id=destination,
        )

    matches = tuple(match_discovery_rules(content, world))
    return ActionResolution(
        player_input=content,
        phase=ActionPhase.CONTACT if matches else ActionPhase.INTERACTION,
        origin_scene_id=origin,
        discovery_matches=matches,
        preferred_skill=preferred_check_skill(list(matches), world),
    )
