"""Serializable game time; real-world clock changes do not advance a game."""

from __future__ import annotations

from .case_clock_time import settle_time_clocks


def elapsed_minutes(world: dict) -> int:
    clock = world.get("world_clock") or {}
    return max(0, int(clock.get("elapsed_minutes", 0))) if isinstance(clock, dict) else 0


def game_day(world: dict) -> int:
    return elapsed_minutes(world) // 1440


def advance_time(world: dict, minutes: int, *, activity: str = "") -> dict:
    """推进游戏时间，并按模组声明的时间规则结算时间型案件时钟。

    返回值是 time_advanced 事件；若结算推进了时钟，事件附 clock_events
    列表（调用方按需并入结果事件流）。``activity`` 是本回合的时间结算类型，
    供时钟规则的适用条件过滤（见 case_clock_time）。
    """
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 10080:
        raise ValueError("单次行动时间必须在 0–10080 分钟之间")
    before = elapsed_minutes(world)
    world["world_clock"] = {"elapsed_minutes": before + minutes}
    event = {"type": "time_advanced", "before": before, "after": before + minutes}
    clock_events = settle_time_clocks(world, activity=activity, new_time_start=before)
    if clock_events:
        event["clock_events"] = clock_events
    return event
