"""Authoritative planning boundary for one player action.

Natural-language input may contain a destination, a longer-term intention, and
an eventual interaction in one sentence.  This module decides how much of that
sentence becomes authoritative in the current turn.  Downstream systems must
consume this resolution instead of independently promoting intention to fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from src.gameplay.action_checks import infer_scene_transition
from src.gameplay.discovery import (
    DiscoveryMatch,
    infer_discovery_target_destination,
    match_discovery_rules,
    preferred_check_skill,
)

_NEGATED_ROUTE = re.compile(r"(?:不|别|不要|拒绝|暂时不)[^，。；、]{0,10}(?:联系|联络|打电话|通知)")
_DISCUSSED_ROUTE = re.compile(
    r"(?:想知道|请问|询问|追问|请教|(?:我)?问).{0,32}(?:联系|联络|打电话|通知)"
)


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
    transition_kind: str | None = None
    route_id: str | None = None
    departure_text: str = ""
    travel_text: str = ""
    entry_text: str = ""
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
            "transition_kind": self.transition_kind,
            "route_id": self.route_id,
            "permits_discovery_effects": self.permits_discovery_effects,
        }


def _flags_match(route: dict, world: dict) -> bool:
    flags = world.get("flags", {})
    if not isinstance(flags, dict):
        return False
    required = route.get("required_flags", {})
    forbidden = route.get("forbidden_flags", {})
    if not isinstance(required, dict) or not isinstance(forbidden, dict):
        return False
    return all(flags.get(str(key)) == value for key, value in required.items()) and all(
        flags.get(str(key)) != value for key, value in forbidden.items()
    )


def _authored_action_route(
    content: str,
    world: dict,
) -> tuple[str, str, str, str, str] | None:
    """Return a module-declared current-scene action edge, or fail closed.

    ``action_routes`` lives on the current runtime scene so a choice such as
    ``让法伦联系惠特克罗夫特`` has a real, author-controlled consequence instead
    of asking the narrative model to guess whether a phone call changes place.
    The same route is available to typed free-form input.
    """
    if _NEGATED_ROUTE.search(content) or _DISCUSSED_ROUTE.search(content):
        return None
    origin = str((world.get("current_scene") or {}).get("id") or "")
    scenes = world.get("scene_catalog", {})
    scene = scenes.get(origin) if isinstance(scenes, dict) else None
    routes = scene.get("action_routes", []) if isinstance(scene, dict) else []
    if not isinstance(routes, list):
        return None
    folded = "".join(str(content).casefold().split()).replace("的", "")
    matches: list[tuple[str, str, str, str, str]] = []
    for route in routes:
        if not isinstance(route, dict) or not _flags_match(route, world):
            continue
        destination = str(route.get("destination_scene_id") or "")
        aliases = route.get("aliases", [])
        if destination not in scenes or destination == origin or not isinstance(aliases, list):
            continue
        matched = any(
            len(alias_text) >= 2 and alias_text in folded
            for alias in aliases
            if (alias_text := "".join(str(alias).casefold().split()).replace("的", ""))
        )
        if matched:
            matches.append(
                (
                    destination,
                    str(route.get("id") or "")[:80],
                    str(route.get("departure_text") or "").strip()[:1200],
                    str(route.get("travel_text") or "").strip()[:1200],
                    str(route.get("entry_text") or "").strip()[:1200],
                )
            )
    destinations = {destination for destination, *_beats in matches}
    if len(destinations) != 1:
        return None
    # More than one alias may intentionally point to the same destination.  In
    # that case retain the first authored route deterministically.
    return next(match for match in matches if match[0] in destinations)


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
            transition_kind="explicit_move",
        )

    route = _authored_action_route(content, world)
    if route:
        destination, route_id, departure_text, travel_text, entry_text = route
        return ActionResolution(
            player_input=content,
            phase=ActionPhase.ARRIVAL,
            origin_scene_id=origin,
            destination_scene_id=destination,
            transition_kind="authored_route",
            route_id=route_id or None,
            departure_text=departure_text,
            travel_text=travel_text,
            entry_text=entry_text,
        )

    destination = infer_discovery_target_destination(content, world)
    if destination:
        return ActionResolution(
            player_input=content,
            phase=ActionPhase.ARRIVAL,
            origin_scene_id=origin,
            destination_scene_id=destination,
            transition_kind="discovery_target",
        )

    matches = tuple(match_discovery_rules(content, world))
    return ActionResolution(
        player_input=content,
        phase=ActionPhase.CONTACT if matches else ActionPhase.INTERACTION,
        origin_scene_id=origin,
        discovery_matches=matches,
        preferred_skill=preferred_check_skill(list(matches), world),
    )
