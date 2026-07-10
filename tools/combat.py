#!/usr/bin/env python3
# ruff: noqa: E402
"""CLI adapter for the persistent combat state machine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import config as cfg
from src.combat import (
    CombatError,
    combat_action,
    combat_decide,
    combat_status,
    end_combat,
    start_combat,
)


def _load_world() -> dict:
    return json.loads(cfg.STATE_FILE.read_text(encoding="utf-8"))


def _save_world(world: dict) -> None:
    cfg.STATE_FILE.write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")


def _args() -> dict:
    if len(sys.argv) < 3:
        return {}
    value = json.loads(sys.argv[2])
    if not isinstance(value, dict):
        raise CombatError("工具参数必须是 JSON 对象")
    return value


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "缺少战斗命令"}, ensure_ascii=False))
        return 2

    command = sys.argv[1]
    world = _load_world()
    params = _args()

    try:
        if command == "start":
            result = start_combat(world, params.get("participants", []), params.get("reason", ""))
        elif command == "status":
            result = combat_status(world)
        elif command == "action":
            result = combat_action(world, **params)
        elif command == "decide":
            result = combat_decide(
                world,
                str(params.get("decision_id", "")),
                str(params.get("option_id", "")),
            )
        elif command == "end":
            result = end_combat(world, params.get("reason", ""))
        else:
            raise CombatError(f"未知战斗命令: {command}")
    except (CombatError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 0

    if command != "status":
        _save_world(world)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
