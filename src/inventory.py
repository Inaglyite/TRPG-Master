"""Deterministic inventory use and counted-resource updates."""

from __future__ import annotations

import re
from typing import Any


AMMO_RE = re.compile(r"(?P<open>[（(])(?P<before>\s*)(?P<count>\d+)(?P<after>\s*发\s*)(?P<close>[）)])")
STACK_RE = re.compile(
    r"(?P<open>[（(])(?P<before>\s*)(?P<count>\d+)"
    r"(?P<after>\s*(?P<unit>个|支|瓶|份|枚|包|颗|块|根|张|次)\s*)(?P<close>[）)])"
)
FIREARM_WORDS = ("枪", "左轮", "手枪", "步枪", "霰弹", "冲锋枪")
DURABLE_WORDS = FIREARM_WORDS + ("钥匙", "手电筒", "怀表", "笔记本", "钢笔", "算盘")
ITEM_USE_LOG_KEY = "item_use_log"


class InventoryError(ValueError):
    pass


def use_item(
    world: dict,
    *,
    item: str,
    operation: str,
    amount: int = 1,
    reason: str = "",
) -> dict:
    """Verify, consume, or discharge an item in ``world.pc.inventory``."""
    amount = _valid_amount(amount)
    operation = operation.strip().lower()
    if operation == "firearm_discharge":
        return consume_firearm_ammo(world, item, amount=amount, reason=reason)

    inventory = _inventory(world)
    match = _find_item(inventory, item)
    if match is None:
        raise InventoryError(f"物品栏中找不到: {item}")
    index, current = match

    if operation == "use":
        result = {
            "ok": True,
            "operation": operation,
            "item_before": current,
            "item_after": current,
            "consumed": 0,
            "reason": reason,
        }
        _append_item_event(world, result)
        return result

    if operation != "consume":
        raise InventoryError(f"不支持的物品操作: {operation}")
    if any(word in current for word in DURABLE_WORDS):
        raise InventoryError(f"{current} 是耐用品，不能用 consume；请改用 use")

    counted = STACK_RE.search(current)
    if counted:
        before = int(counted.group("count"))
        if before < amount:
            raise InventoryError(f"{current} 数量不足：需要 {amount}，当前 {before}")
        after = before - amount
        if after == 0:
            inventory.pop(index)
            updated = None
        else:
            updated = _replace_count(current, counted, after)
            inventory[index] = updated
    else:
        if amount != 1:
            raise InventoryError("未标注数量的物品一次只能消耗一个")
        inventory.pop(index)
        before = 1
        after = 0
        updated = None

    result = {
        "ok": True,
        "operation": operation,
        "item_before": current,
        "item_after": updated,
        "before": before,
        "after": after,
        "consumed": amount,
        "reason": reason,
    }
    _append_item_event(world, result)
    return result


def check_firearm_ammo(world: dict, weapon_hint: str | None, amount: int = 1) -> dict:
    """Return ammo status and reject a known empty/insufficient firearm."""
    amount = _valid_amount(amount)
    inventory = _inventory(world)
    tracker = _find_ammo_tracker(inventory, weapon_hint)
    if tracker:
        if tracker["count"] < amount:
            raise InventoryError(
                f"{tracker['item']} 弹药不足：需要 {amount} 发，当前 {tracker['count']} 发"
            )
        return {"tracked": True, **tracker, "requested": amount}

    held = _find_item(inventory, weapon_hint or "")
    if held and any(word in held[1] for word in FIREARM_WORDS):
        return {
            "tracked": False,
            "item": held[1],
            "requested": amount,
            "warning": "武器没有“(N发)”或“（N发）”标记，无法跟踪余弹",
        }
    raise InventoryError(f"物品栏中找不到可用枪械: {weapon_hint or '未指定武器'}")


def consume_firearm_ammo(
    world: dict,
    weapon_hint: str | None,
    *,
    amount: int = 1,
    reason: str = "",
) -> dict:
    """Spend ammunition for a non-combat or combat firearm discharge."""
    status = check_firearm_ammo(world, weapon_hint, amount)
    if not status["tracked"]:
        result = {
            "ok": True,
            "operation": "firearm_discharge",
            "tracked": False,
            "weapon": status["item"],
            "spent": amount,
            "reason": reason,
            "warning": status["warning"],
        }
        _append_item_event(world, result)
        return result

    inventory = _inventory(world)
    before = status["count"]
    after = before - amount
    current = status["item"]
    updated = _replace_count_at(current, status["count_start"], status["count_end"], after)
    inventory[status["index"]] = updated
    result = {
        "ok": True,
        "operation": "firearm_discharge",
        "tracked": True,
        "weapon": updated,
        "item_before": current,
        "item_after": updated,
        "before": before,
        "after": after,
        "spent": amount,
        "reason": reason,
    }
    _append_item_event(world, result)
    return result


def _inventory(world: dict) -> list:
    inventory = world.get("pc", {}).get("inventory")
    if not isinstance(inventory, list):
        raise InventoryError("world_state.pc.inventory 不是有效列表")
    return inventory


def _valid_amount(amount: Any) -> int:
    try:
        value = int(amount)
    except (TypeError, ValueError) as exc:
        raise InventoryError("使用数量必须是整数") from exc
    if not 1 <= value <= 20:
        raise InventoryError("使用数量必须在 1 到 20 之间")
    return value


def _normalized_item_name(value: str) -> str:
    value = AMMO_RE.sub("", value)
    value = STACK_RE.sub("", value)
    return re.sub(r"\s+", "", value).lower()


def _find_item(inventory: list, hint: str) -> tuple[int, str] | None:
    normalized_hint = _normalized_item_name(str(hint))
    candidates: list[tuple[tuple[int, int, int], tuple[int, str]]] = []
    for index, value in enumerate(inventory):
        if not isinstance(value, str):
            continue
        normalized_value = _normalized_item_name(value)
        exact = int(normalized_value == normalized_hint and bool(normalized_hint))
        partial = int(bool(normalized_hint and (
            normalized_hint in normalized_value or normalized_value in normalized_hint
        )))
        if exact or partial:
            candidates.append(((exact, partial, -index), (index, value)))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _find_ammo_tracker(inventory: list, weapon_hint: str | None) -> dict | None:
    normalized_hint = _normalized_item_name(weapon_hint or "")
    candidates: list[tuple[tuple[int, int, int, int], dict]] = []
    for index, item in enumerate(inventory):
        if not isinstance(item, str):
            continue
        match = AMMO_RE.search(item)
        if not match:
            continue
        count = int(match.group("count"))
        normalized_item = _normalized_item_name(item)
        hint_match = int(bool(normalized_hint and (
            normalized_hint in normalized_item or normalized_item in normalized_hint
        )))
        firearm_match = int(any(word in item for word in FIREARM_WORDS))
        score = (hint_match, int(count > 0), firearm_match, -index)
        candidates.append((score, {
            "index": index,
            "item": item,
            "count": count,
            "count_start": match.start("count"),
            "count_end": match.end("count"),
        }))
    if not candidates:
        return None
    if normalized_hint:
        matching = [candidate for candidate in candidates if candidate[0][0] == 1]
        if matching:
            candidates = matching
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _replace_count(item: str, match: re.Match, count: int) -> str:
    return _replace_count_at(item, match.start("count"), match.end("count"), count)


def _replace_count_at(item: str, start: int, end: int, count: int) -> str:
    return f"{item[:start]}{count}{item[end:]}"


def _append_item_event(world: dict, result: dict) -> None:
    log = world.setdefault(ITEM_USE_LOG_KEY, [])
    if not isinstance(log, list):
        log = []
        world[ITEM_USE_LOG_KEY] = log
    log.append({
        key: result.get(key)
        for key in ("operation", "item_before", "item_after", "weapon", "consumed", "spent", "reason")
        if result.get(key) is not None
    })
    del log[:-30]
