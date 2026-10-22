"""Player action projections and engine-owned provenance for committed visits."""

from __future__ import annotations

from src.gameplay.scene_projection import player_scene_view
from src.gameplay.skill_view import display_name, effective_skills


def record_scene_visits(state: dict, *scene_ids: str) -> None:
    """Called only after a trusted move; participates in its surrounding transaction."""
    recorded = state.get("scene_visits")
    visited = {key for key in recorded if isinstance(key, str)} if isinstance(recorded, list) else set()
    catalog = state.get("scene_catalog") or {}
    visited.update(key for key in scene_ids if key in catalog)
    state["scene_visits"] = sorted(visited)


def player_skill_options(pc: dict) -> list[dict]:
    explicit = pc.get("skills") if isinstance(pc.get("skills"), dict) else {}
    return [
        {"id": key, "name": display_name(key), "value": value, "trained": key in explicit}
        for key, value in effective_skills(pc, include_attributes=False).items()
        if 0 < value <= 100
    ]


def visited_scene_map(state: dict) -> dict:
    """Only committed visits/current location, even when an unvisited exit is known.

    No hidden nodes, placeholder counts, author coordinates or descriptions cross
    the boundary. Positions are laid out from the visible graph by the client.
    """
    catalog = state.get("scene_catalog")
    catalog = catalog if isinstance(catalog, dict) else {}
    current = state.get("current_scene") or {}
    current = current if isinstance(current, dict) else {}
    current_id = str(current.get("id") or "")
    history = state.get("encounter_history")
    visited = set(history) if isinstance(history, dict) else set()
    recorded = state.get("scene_visits")
    if isinstance(recorded, list):
        visited.update(key for key in recorded if isinstance(key, str))
    if current_id:
        visited.add(current_id)
    nodes = []
    for scene_id in sorted(key for key in visited if isinstance(key, str)):
        scene = current if scene_id == current_id else catalog.get(scene_id)
        if not isinstance(scene, dict):
            continue
        view = player_scene_view({"current_scene": {**scene, "id": scene_id},
                                  "scene_catalog": catalog})
        if view:
            nodes.append({"id": scene_id, "name": view["name"],
                          "current": scene_id == current_id})
    allowed = {node["id"] for node in nodes}
    edges = set()
    for node in nodes:
        scene_id = node["id"]
        scene = current if node["current"] else catalog.get(scene_id, {})
        exits = scene.get("exits")
        if not isinstance(exits, list):
            continue
        for target in exits:
            if isinstance(target, str) and target in allowed and target != scene_id:
                edges.add(tuple(sorted((scene_id, target))))
    return {"nodes": nodes, "edges": [{"from": a, "to": b} for a, b in sorted(edges)]}
