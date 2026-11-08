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


def read_timeline(directory: Path, *, world: str = "", turn: str = "", limit: int = 100, search: str = "", _index: bool = False):
    rows = _TurnIndex() if _index else deque(maxlen=limit)
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
                if search and search not in row.get("output_text", ""):
                    continue
                rows.append(row)
    return list(rows), skipped


class _TurnIndex:
    def __init__(self):
        self.turns = {}

    def append(self, row):
        key = (row.get("world_id", ""), row.get("turn_id", ""))
        if not key[1]:
            return
        item = self.turns.setdefault(key, {"world_id": key[0], "turn_id": key[1],
            "first_seen": row.get("ts"), "events": 0, "outputs": 0, "status": "unknown",
            "saw_start": False, "saw_end": False})
        item["events"] += 1
        item["last_seen"] = row.get("ts")
        item["outputs"] += int(row.get("content_recorded", False))
        if row["event"] == "turn.started":
            item["saw_start"] = True
            item["status"] = "started"
        elif row["event"] in {"turn.committed", "turn.incomplete"}:
            item["saw_end"] = True
            item["status"] = row.get("status", "committed")

    def __iter__(self):
        return iter(self.turns.values())


def list_turns(directory: Path, *, world: str = "", limit: int = 100):
    rows, skipped = read_timeline(directory, world=world, _index=True)
    return rows[-limit:], skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--world", default="", help="精确匹配 world_id")
    parser.add_argument("--turn", default="", help="精确匹配 turn_id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true", help="输出筛选后的原始 JSONL")
    parser.add_argument("--list-turns", action="store_true", help="列出保留日志内的历史回合及完整性")
    parser.add_argument("--search", default="", help="在保留的输出内容中查找截图中的文字")
    parser.add_argument("--show-content", action="store_true", help="显示脱敏后的叙事内容（可能含剧透）")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit 必须在 1–10000 之间")
    if args.list_turns and (args.turn or args.search):
        parser.error("--list-turns 不与 --turn/--search 同用")
    print(f"日志目录：{args.log_dir.resolve()}（仅查询仍保留的日志；不是数据库全部历史）", file=sys.stderr)
    if args.list_turns:
        rows, skipped = list_turns(args.log_dir, world=args.world, limit=args.limit)
    else:
        rows, skipped = read_timeline(args.log_dir, world=args.world, turn=args.turn, limit=args.limit, search=args.search)
    if not rows:
        print(f"未找到匹配记录：{args.log_dir}（确认后端已重启且发生过新回合）", file=sys.stderr)
        return 1
    for row in rows:
        row = dict(row)
        if not args.show_content:
            row.pop("output_text", None)
        if args.list_turns:
            print(json.dumps(row, ensure_ascii=False))
            continue
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
            continue
        details = " ".join(f"{key}={value}" for key, value in row.items()
                           if key not in {"v", "ts", "event", "pid", "thread_id", "run_id", "frames", "output_text"})
        print(f"{row.get('ts', '?')} {row['event']:<20} {details}")
        if "output_text" in row:
            print(json.dumps(row["output_text"], ensure_ascii=False))
        for frame in row.get("frames", []):
            print(f"  ↳ {frame['file']}:{frame['line']} {frame['function']}")
    if skipped:
        print(f"已跳过 {skipped} 条损坏或不完整记录。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
