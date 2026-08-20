#!/usr/bin/env python3
"""Run reference-aware H2 context-event garbage collection.

This is an operator command, not an application endpoint.  The scheduled
service invokes its safe default (archived worlds only); ``--all`` must be
explicitly requested for a reference-aware sweep of live worlds' old epochs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TRPG H2 context-event maintenance")
    parser.add_argument(
        "--all",
        action="store_true",
        help="also collect unreferenced closed epochs from active worlds",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="override TRPG_DATABASE_URL for an explicit operator run",
    )
    parser.add_argument(
        "--runtime-root",
        default="",
        help="derive embedded SQLite URL from this runtime root when no URL is configured",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sys.path.insert(0, str(_project_root()))
    from src.context_maintenance import collect_context_events
    from src.database import database_url

    configured = str(args.database_url or os.environ.get("TRPG_DATABASE_URL") or "").strip()
    root = Path(args.runtime_root).resolve() if args.runtime_root else None
    url = configured or database_url(root)
    reports = collect_context_events(url, scope="all" if args.all else "archived")
    print(
        json.dumps(
            {
                "scope": "all" if args.all else "archived",
                "worlds": [report.to_dict() for report in reports],
                "sessions": sum(report.sessions for report in reports),
                "events": sum(report.events for report in reports),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
