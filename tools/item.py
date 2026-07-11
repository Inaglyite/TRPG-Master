#!/usr/bin/env python3
# ruff: noqa: E402
"""CLI adapter for deterministic inventory use."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import config as cfg
from src.inventory import InventoryError, use_item


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "缺少物品使用参数"}, ensure_ascii=False))
        return 2
    try:
        params = json.loads(sys.argv[1])
        if not isinstance(params, dict):
            raise InventoryError("工具参数必须是 JSON 对象")
        world = json.loads(cfg.STATE_FILE.read_text(encoding="utf-8"))
        result = use_item(
            world,
            item=str(params.get("item", "")),
            operation=str(params.get("operation", "use")),
            amount=params.get("amount", 1),
            reason=str(params.get("reason", "")),
        )
        cfg.STATE_FILE.write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
    except (InventoryError, json.JSONDecodeError, OSError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
