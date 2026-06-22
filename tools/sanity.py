#!/usr/bin/env python3
"""TRPG 理智值专用工具 (COC 向)"""

import json
import sys
import os
import subprocess
import random

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SM = os.path.join(PROJECT_ROOT, "tools", "state_manager.py")


def _run_state_manager(*args):
    cmd = ["python3", SM] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: state_manager 调用失败: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return result.stdout.strip()


def _roll_loss(formula):
    """解析理智损失骰子公式并掷骰"""
    mod = 0
    f = formula
    if "+" in f:
        f, mod_str = f.split("+", 1)
        mod = int(mod_str)
    count_str, sides_str = f.split("d")
    count = int(count_str)
    sides = int(sides_str)
    total = sum(random.randint(1, sides) for _ in range(count)) + mod
    return total


SANITY_LOSS_TABLE = {
    "minor": "1d4",
    "moderate": "1d6+1",
    "major": "2d6+2",
    "catastrophic": "3d10"
}


def apply_sanity_loss(severity="moderate"):
    """对 PC 施加理智损失"""
    formula = SANITY_LOSS_TABLE.get(severity, "1d6+1")
    loss = _roll_loss(formula)
    current_san = _run_state_manager("get", "pc.san")
    new_san = max(0, current_san - loss)
    _run_state_manager("set", "pc.san", str(new_san))

    result = {
        "target": "pc",
        "severity": severity,
        "loss_roll": formula,
        "loss_amount": loss,
        "san_before": current_san,
        "san_after": new_san
    }
    print(json.dumps(result, ensure_ascii=False))

    if new_san <= 0:
        print("!!! PC 理智值归零，角色陷入永久疯狂！", file=sys.stderr)
    elif new_san <= current_san * 0.5:
        print("!!! PC 损失超过一半当前理智值，可能触发临时疯狂！", file=sys.stderr)


def apply_sanity_restore(amount):
    """恢复理智值（如通过休息或成功克服恐惧）"""
    current_san = _run_state_manager("get", "pc.san")
    max_san = _run_state_manager("get", "pc.max_san")
    new_san = min(max_san, current_san + amount)
    _run_state_manager("set", "pc.san", str(new_san))

    result = {
        "target": "pc",
        "restore_amount": amount,
        "san_before": current_san,
        "san_after": new_san
    }
    print(json.dumps(result, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python sanity.py loss <severity>    理智损失 (minor/moderate/major/catastrophic)")
        print("  python sanity.py restore <amount>   恢复理智")
        print("  python sanity.py check              查看当前理智状态")
        sys.exit(1)

    action = sys.argv[1]

    if action == "loss":
        severity = sys.argv[2] if len(sys.argv) > 2 else "moderate"
        if severity not in SANITY_LOSS_TABLE:
            print(f"ERROR: 无效严重度 '{severity}'，可选: {list(SANITY_LOSS_TABLE.keys())}", file=sys.stderr)
            sys.exit(1)
        apply_sanity_loss(severity)

    elif action == "restore":
        if len(sys.argv) < 3:
            print("ERROR: restore 需要 <amount>", file=sys.stderr)
            sys.exit(1)
        apply_sanity_restore(int(sys.argv[2]))

    elif action == "check":
        san = _run_state_manager("get", "pc.san")
        max_san = _run_state_manager("get", "pc.max_san")
        result = {"san": san, "max_san": max_san, "ratio": round(san / max_san, 2)}
        print(json.dumps(result, ensure_ascii=False))

    else:
        print(f"ERROR: 未知动作 '{action}'，可用: loss, restore, check", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
