"""Bounded, immutable combat results for player/keeper reading, never raw skills."""

import copy


def record_roll_result(
    state: dict, pending: dict, response: str, result: dict, *, round_number: int
) -> dict:
    combat = state.get("combat_state") or {}
    rolls = []
    for key, role in (("attack_roll", "attack"), ("defense_roll", "defense")):
        raw = result.get(key)
        if isinstance(raw, dict) and type(raw.get("roll")) is int:
            rolls.append(
                {
                    "actor_id": str(raw["actor"]),
                    "role": role,
                    "roll": raw["roll"],
                    "level": str(raw.get("level") or ""),
                }
            )
    raw_damage = result.get("damage")
    damage = None
    if isinstance(raw_damage, dict):
        damage = {
            "target_id": str(raw_damage["target"]),
            "amount": raw_damage["amount"],
            "hp_before": raw_damage["hp_before"],
            "hp_after": raw_damage["hp_after"],
        }
    receipt = {
        "roll_id": pending["roll_id"],
        "investigator_id": pending["investigator_id"],
        "encounter_id": str(combat["encounter_id"]),
        "round": round_number,
        "actor_id": pending["actor_id"],
        "target_id": pending.get("target_id"),
        "action_type": pending["action_type"],
        "response": response,
        "outcome": str(result.get("outcome") or "resolved"),
        "rolls": rolls,
        "damage": damage,
    }
    history = state.setdefault("combat_results", [])
    history[:] = [r for r in history if r.get("roll_id") != receipt["roll_id"]]
    history.append(copy.deepcopy(receipt))
    del history[:-20]
    return receipt


def visible_results(state: dict, own: set[str], is_keeper: bool) -> list[dict]:
    return [
        copy.deepcopy(r)
        for r in state.get("combat_results", [])
        if is_keeper or r.get("investigator_id") in own
    ]
