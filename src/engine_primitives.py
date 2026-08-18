"""Small engine-facing types kept independent from GameEngine orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class TurnCancelledError(RuntimeError):
    """Raised when a disconnected client cancels an in-flight model turn."""


@dataclass
class EngineCallbacks:
    """Events emitted by the engine at stable presentation boundaries."""

    on_narrative: Callable[..., None] = lambda text, npc_id=None: None
    on_tension: Callable[[str, str], None] = lambda text, cat: None
    on_dice: Callable[[str, dict | None], None] = lambda summary, roll_data=None: None
    on_glm_summary: Callable[[str], None] = lambda text: None
    on_suggest: Callable[[dict], bool] = lambda info: False
    on_decision: Callable[[dict], str | None] = lambda info: info.get("default_option")
    on_phase: Callable[[str, str], None] = lambda phase, label: None
    on_choices: Callable[[list[dict]], None] = lambda choices: None
    on_done: Callable[[], None] = lambda: None
    on_game_over: Callable[[str, str, str], None] = lambda t, ti, s: None
    on_handout: Callable[[dict], None] = lambda info: None
    on_error: Callable[[str], None] = lambda msg: None
    on_speaker_segment: Callable[[str], None] = lambda npc_id: None
    on_narrative_segments: Callable[[list], None] = lambda segments: None
    on_performance: Callable[[dict], None] = lambda metrics: None
    on_private_event: Callable[[dict], None] = lambda info: None
