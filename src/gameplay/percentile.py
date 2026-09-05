"""Shared CoC percentile arithmetic; callers own authorization and persistence."""

from __future__ import annotations


def choose_percentile(tens: list[int], units: int, *, penalty: bool = False) -> tuple[int, list[int]]:
    """Compare complete d100 values, including 00=100, before choosing a die."""
    if not tens or not 0 <= units <= 9 or any(not 0 <= ten <= 9 for ten in tens):
        raise ValueError("百分骰的每一位必须在 0–9 之间")
    candidates = [100 if ten == 0 and units == 0 else ten * 10 + units for ten in tens]
    return (max(candidates) if penalty else min(candidates)), candidates


def success_level(roll: int, value: int) -> tuple[int, str]:
    """Return a rank independent of the task's required success level."""
    if not 1 <= roll <= 100 or value < 0:
        raise ValueError("非法的百分骰或技能值")
    if roll == 1:
        return 4, "critical"
    if roll >= (96 if value < 50 else 100):
        return -1, "fumble"
    if roll <= value // 5:
        return 3, "extreme"
    if roll <= value // 2:
        return 2, "hard"
    if roll <= value:
        return 1, "regular"
    return 0, "failure"


REQUIRED_RANKS = {"regular": 1, "hard": 2, "extreme": 3}
