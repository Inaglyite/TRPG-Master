#!/usr/bin/env python3
"""TRPG 理智值工具 —— COC 第七版三阶段疯狂系统"""

import json
import random
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SM = os.path.join(PROJECT_ROOT, "tools", "state_manager.py")
MODULE = os.environ.get("TRPG_MODULE", "mansion_of_madness")
STATE_PATH = PROJECT_ROOT / "mod" / MODULE / "world_state.json"


def _cli(*args):
    import subprocess
    cmd = ["python3", SM] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return result.stdout.strip()


def _load_state():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(data):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 疯狂发作表
BOUT_OF_MADNESS = [
    "失忆 — 最近发生的事情完全记不起来，持续1D10轮",
    "假性残疾 — 某个肢体或感官暂时失效（失明/失聪/瘫痪），持续1D10轮",
    "暴力倾向 — 对最近的威胁源进行无差别攻击，持续1D10轮",
    "偏执妄想 — 确信有人/物在监视和跟踪自己，持续1D10小时",
    "人际依赖 — 对最近接触的人产生极度依赖，持续1D10小时",
    "昏厥 — 当场晕倒，1D10轮后苏醒",
    "逃避行为 — 不顾一切逃跑，持续1D10轮",
    "歇斯底里 — 无法控制地大笑/哭泣/尖叫，持续1D10轮",
    "恐惧症 — 对触发来源产生永久恐惧，此后每次遇到需 SAN 检定",
    "躁狂症 — 情绪极度亢奋，可能做出危险行为，持续1D10小时"
]


def apply_sanity_loss(severity: str = "moderate", source: str = "未知恐怖",
                      silent: bool = False) -> dict:
    """施加理智损失。格式: 成功损失X / 失败损失Y。

    severity 可以是 "minor/moderate/major/catastrophic" 或自定义格式如 "1/1D6"
    """
    state = _load_state()
    pc = state["pc"]
    current_san = pc.get("san", 65)
    pow_val = pc.get("attributes", {}).get("POW", 65)

    # 解析 severity
    loss_formats = {
        "trivial": "0/1",
        "minor": "0/1D4",
        "moderate": "1/1D6+1",
        "major": "1D4/2D6+2",
        "catastrophic": "1D10/1D100"
    }
    loss_fmt = loss_formats.get(severity, severity)

    # 解析 X/Y 格式
    if "/" in loss_fmt:
        success_loss_str, failure_loss_str = loss_fmt.split("/", 1)
    else:
        success_loss_str, failure_loss_str = "0", loss_fmt

    def parse_loss(s: str) -> int:
        s = s.strip()
        if s == "0":
            return 0
        if "D" in s:
            if "+" in s:
                parts = s.split("+")
                d_part = parts[0]
                mod = int(parts[1])
            else:
                d_part = s
                mod = 0
            count, sides = d_part.split("D")
            return sum(random.randint(1, int(sides)) for _ in range(int(count))) + mod
        return int(s)

    success_loss = parse_loss(success_loss_str)
    failure_loss = parse_loss(failure_loss_str)

    # SAN 检定（d100 <= 当前SAN = 成功）
    san_roll = random.randint(1, 100)
    san_check_success = san_roll <= current_san

    if san_check_success:
        loss = success_loss
    else:
        loss = failure_loss

    new_san = max(0, current_san - loss)

    # 触发临时疯狂判断
    temp_insanity = False
    insanity_type = ""
    if loss >= 5:  # 单次损失 >= 5
        int_val = pc.get("attributes", {}).get("INT", 50)
        int_roll = random.randint(1, 100)
        if int_roll <= int_val:
            temp_insanity = True
            insanity_type = random.choice(BOUT_OF_MADNESS)

    # 触发不定疯狂判断
    indefinite_insanity = False
    one_day_loss = pc.get("_san_loss_today", 0) + loss
    pc["_san_loss_today"] = one_day_loss
    if one_day_loss >= current_san // 5:
        indefinite_insanity = True

    # 永久疯狂
    permanent = new_san <= 0

    pc["san"] = new_san
    _save_state(state)

    result = {
        "target": "pc",
        "severity": severity,
        "source": source,
        "loss_format": loss_fmt,
        "san_roll": san_roll,
        "san_check_success": san_check_success,
        "success_loss": success_loss,
        "failure_loss": failure_loss,
        "actual_loss": loss,
        "san_before": current_san,
        "san_after": new_san,
        "temporary_insanity": temp_insanity,
        "insanity_type": insanity_type,
        "indefinite_insanity": indefinite_insanity,
        "permanent_insanity": permanent,
    }

    print(json.dumps(result, ensure_ascii=False))

    if permanent:
        print("!!! SAN 降至 0 —— 永久疯狂！角色不再是可扮演角色。", file=sys.stderr)
    elif temp_insanity:
        print(f"!!! 临时疯狂：{insanity_type}", file=sys.stderr)
    elif indefinite_insanity:
        print("!!! 不定疯狂：累积损失触及阈值，症状将持续数周。", file=sys.stderr)

    return result


def sanity_restore(amount: int = 0):
    """恢复理智值"""
    state = _load_state()
    pc = state["pc"]
    current = pc.get("san", 65)
    max_san = 99 - pc.get("skills", {}).get("cthulhu_mythos", 0)
    new_san = min(max_san, current + amount)
    pc["san"] = new_san
    _save_state(state)
    print(json.dumps({"restore": amount, "san_before": current, "san_after": new_san, "max_san": max_san},
                     ensure_ascii=False))


def sanity_check():
    """查看当前理智状态"""
    state = _load_state()
    pc = state["pc"]
    san = pc.get("san", 65)
    max_san = 99 - pc.get("skills", {}).get("cthulhu_mythos", 0)
    result = {"san": san, "max_san": max_san, "ratio": round(san / max_san, 2)}
    print(json.dumps(result, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        print("用法: python sanity.py loss <severity> [--silent]")
        print("      python sanity.py restore <amount>")
        print("      python sanity.py check")
        print("")
        print("severity: trivial/minor/moderate/major/catastrophic")
        print("          或自定义 X/Y 格式如 '1/1D6'")
        sys.exit(1)

    action = sys.argv[1]

    if action == "loss":
        severity = sys.argv[2] if len(sys.argv) > 2 else "moderate"
        apply_sanity_loss(severity)
    elif action == "restore":
        amount = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        sanity_restore(amount)
    elif action == "check":
        sanity_check()
    else:
        print(f"未知动作: {action}")


if __name__ == "__main__":
    main()
