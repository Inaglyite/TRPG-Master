"""Serializable game time; real-world clock changes do not advance a game."""

from __future__ import annotations


def elapsed_minutes(world: dict) -> int:
    clock = world.get("world_clock") or {}
    return max(0, int(clock.get("elapsed_minutes", 0))) if isinstance(clock, dict) else 0


def game_day(world: dict) -> int:
    return elapsed_minutes(world) // 1440


def advance_time(world: dict, minutes: int) -> dict:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 10080:
        raise ValueError("单次行动时间必须在 0–10080 分钟之间")
    before = elapsed_minutes(world)
    world["world_clock"] = {"elapsed_minutes": before + minutes}
    return {"type": "time_advanced", "before": before, "after": before + minutes}
