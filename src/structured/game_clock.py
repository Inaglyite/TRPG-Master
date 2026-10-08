"""Public read-only elapsed time; never project authored NPC/case schedules."""

MAX_SAFE_MINUTES = 2**53 - 1


def clock_projection(state: dict) -> dict | None:
    # A world with no time advancement starts at zero under world_time rules.
    # Corrupt/legacy explicit values are unknown; publication must not heal them.
    clock = state.get("world_clock", {})
    if not isinstance(clock, dict):
        return None
    value = clock.get("elapsed_minutes", 0)
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MINUTES:
        return None
    return {"elapsed_minutes": value}
