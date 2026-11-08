#!/usr/bin/env python3
"""Read rotated JSONL receipts as a bounded human-readable timeline (no model calls)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.app.logger import LOG_DIR


def read_timeline(directory: Path, *, world: str = "", turn: str = "", limit: int = 100):
    rows = deque(maxlen=limit)
    skipped = 0
    # Read oldest rotation first; tolerate an interrupted final write.
    paths = [directory / f"diagnostics.jsonl.{i}" for i in range(3, 0, -1)]
    paths.append(directory / "diagnostics.jsonl")
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if not isinstance(row, dict) or not row.get("event"):
                    skipped += 1
                    continue
                if world and row.get("world_id") != world:
                    continue
                if turn and row.get("turn_id") != turn:
                    continue
                rows.append(row)
    return list(rows), skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--world", default="", help="精确匹配 world_id")
    parser.add_argument("--turn", default="", help="精确匹配 turn_id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true", help="输出筛选后的原始 JSONL")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit 必须在 1–10000 之间")
    rows, skipped = read_timeline(args.log_dir, world=args.world, turn=args.turn, limit=args.limit)
    if not rows:
        print(f"未找到匹配记录：{args.log_dir}（确认后端已重启且发生过新回合）", file=sys.stderr)
        return 1
    for row in rows:
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
            continue
        details = " ".join(f"{key}={value}" for key, value in row.items()
                           if key not in {"v", "ts", "event", "pid", "thread_id", "run_id", "frames"})
        print(f"{row.get('ts', '?')} {row['event']:<20} {details}")
        for frame in row.get("frames", []):
            print(f"  ↳ {frame['file']}:{frame['line']} {frame['function']}")
    if skipped:
        print(f"已跳过 {skipped} 条损坏或不完整记录。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
