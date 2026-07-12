#!/usr/bin/env python3
"""TRPG 伤害/治疗计算工具"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import RuntimeContext  # noqa: E402
from tools.state_manager import _resolve_path, _set_path  # noqa: E402


CONTEXT = RuntimeContext.from_env()


def apply_damage(target_path, amount, damage_type="物理"):
    """对目标造成伤害"""
    result = None

    def mutate(world: dict) -> None:
        nonlocal result
        current_hp = _resolve_path(world, f"{target_path}.hp")
        new_hp = max(0, current_hp - amount)
        _set_path(world, f"{target_path}.hp", new_hp)
        result = {
            "target": target_path,
            "damage": amount,
            "damage_type": damage_type,
            "hp_before": current_hp,
            "hp_after": new_hp,
            "status": "alive" if new_hp > 0 else "dying",
        }

    CONTEXT.world_store.update(mutate)
    print(json.dumps(result, ensure_ascii=False))

    if result["hp_after"] <= 0:
        print(f"!!! {target_path} 生命值归零，进入濒死状态！", file=sys.stderr)


def apply_heal(target_path, amount):
    """治疗目标"""
    result = None

    def mutate(world: dict) -> None:
        nonlocal result
        current_hp = _resolve_path(world, f"{target_path}.hp")
        max_hp = _resolve_path(world, f"{target_path}.max_hp")
        new_hp = min(max_hp, current_hp + amount)
        _set_path(world, f"{target_path}.hp", new_hp)
        result = {
            "target": target_path,
            "heal_amount": amount,
            "actual_heal": new_hp - current_hp,
            "hp_before": current_hp,
            "hp_after": new_hp,
        }

    CONTEXT.world_store.update(mutate)
    print(json.dumps(result, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python damage.py damage <target_path> <amount> [damage_type]")
        print("    例: python damage.py damage pc 5 物理")
        print("    例: python damage.py damage npcs.2 8 精神")
        print("  python damage.py heal <target_path> <amount>")
        print("    例: python damage.py heal pc 3")
        sys.exit(1)

    action = sys.argv[1]

    if action == "damage":
        if len(sys.argv) < 4:
            print("ERROR: damage 需要 <target_path> 和 <amount>", file=sys.stderr)
            sys.exit(1)
        target = sys.argv[2]
        amount = int(sys.argv[3])
        dtype = sys.argv[4] if len(sys.argv) > 4 else "物理"
        apply_damage(target, amount, dtype)

    elif action == "heal":
        if len(sys.argv) < 4:
            print("ERROR: heal 需要 <target_path> 和 <amount>", file=sys.stderr)
            sys.exit(1)
        target = sys.argv[2]
        amount = int(sys.argv[3])
        apply_heal(target, amount)

    else:
        print(f"ERROR: 未知动作 '{action}'，可用: damage, heal", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
