#!/usr/bin/env python3
"""TRPG 统一骰子工具"""

import random
import sys


def roll_dice(sides, count=1, advantage=False, disadvantage=False):
    """掷骰子，返回 (results_list, total)"""
    results = [random.randint(1, sides) for _ in range(count)]
    if advantage:
        second = random.randint(1, sides)
        results.append(second)
        return results, max(results[0], second)
    if disadvantage:
        second = random.randint(1, sides)
        results.append(second)
        return results, min(results[0], second)
    return results, sum(results)


def main():
    if len(sys.argv) < 2:
        print("用法: python dice.py <骰子类型> [参数...]")
        print("  d20          — 掷一个 d20")
        print("  d20 adv      — d20 优势（取两骰中较高者）")
        print("  d20 dis      — d20 劣势（取两骰中较低者）")
        print("  d100         — 掷一个 d100")
        print("  2d6          — 掷两个 d6 并求和")
        print("  3d8+2        — 掷三个 d8 求和后加修正值")
        sys.exit(1)

    spec = sys.argv[1]
    advantage = False
    disadvantage = False

    if len(sys.argv) > 2:
        if sys.argv[2] == "adv":
            advantage = True
        elif sys.argv[2] == "dis":
            disadvantage = True

    # 解析格式: [count]d<sides>[+/-modifier]
    modifier = 0
    base = spec

    if "+" in spec:
        base, mod_str = spec.split("+", 1)
        modifier = int(mod_str)
    elif "-" in spec and not base.startswith("-"):
        # 找最后一个减号
        parts = spec.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            base = parts[0]
            modifier = -int(parts[1])

    if "d" in base:
        count_str, sides_str = base.split("d", 1)
        count = int(count_str) if count_str else 1
        sides = int(sides_str)
    else:
        print(f"ERROR: 无法解析骰子格式 '{spec}'，请使用如 d20, 2d6, 3d8+2", file=sys.stderr)
        sys.exit(1)

    results, total = roll_dice(sides, count, advantage, disadvantage)
    final = total + modifier

    # 输出 JSON 格式方便解析
    import json
    output = {
        "spec": spec,
        "sides": sides,
        "count": count,
        "modifier": modifier,
        "advantage": advantage,
        "disadvantage": disadvantage,
        "rolls": results,
        "total": final
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
