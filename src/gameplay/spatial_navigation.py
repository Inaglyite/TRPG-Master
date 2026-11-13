"""Classic travel preflight, before encounters/narrative can run."""

from src.gameplay.action_checks import infer_scene_transition
from src.gameplay.spatial import authorize_transition, enabled


def infer_guarded_transition(content, world, destination=None):
    destination = destination or infer_scene_transition(content, world)
    if destination and enabled(world):
        return authorize_transition(world, destination)[0]
    return destination
