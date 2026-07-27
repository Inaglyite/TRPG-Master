#!/usr/bin/env python3
"""Fail a packaged backend build if required assets are missing or private data leaked."""

from __future__ import annotations

import argparse
from pathlib import Path


def bundle_violations(bundle: Path, *, windows: bool = True) -> list[str]:
    bundle = bundle.resolve()
    internal = bundle / "_internal"
    problems: list[str] = []
    required = [
        internal / "alembic.ini",
        internal / "migrations" / "env.py",
        internal / "migrations" / "versions" / "20260722_0001_database_control_plane.py",
        internal / "migrations" / "versions" / "20260722_0004_room_action_idempotency.py",
        internal / "mod",
        internal / "rules",
        internal / "skills",
        internal / "tools",
        internal / "characters" / "default",
    ]
    if windows:
        required.append(bundle / "trpg-server.exe")
    else:
        required.append(bundle / "trpg-server")
    for path in required:
        if not path.exists():
            problems.append(f"missing required packaged resource: {path.relative_to(bundle)}")

    if not bundle.exists():
        return problems
    for candidate in bundle.rglob("*"):
        relative = candidate.relative_to(bundle)
        lowered_parts = tuple(part.lower() for part in relative.parts)
        name = candidate.name.lower()
        if name in {".env.json", "savegame.json", "player_profile.json"}:
            problems.append(f"private runtime file included: {relative}")
        if name == "worlds" or name == "saves":
            problems.append(f"private runtime directory included: {relative}")
        if name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")):
            problems.append(f"database file included: {relative}")
        if "characters" in lowered_parts:
            index = lowered_parts.index("characters")
            if len(lowered_parts) > index + 1 and lowered_parts[index + 1] == "custom":
                problems.append(f"custom character data included: {relative}")
        if "mod" in lowered_parts and "characters" in lowered_parts:
            problems.append(f"module runtime character data included: {relative}")
    return sorted(set(problems))


def verify_bundle(bundle: Path, *, windows: bool = True) -> None:
    problems = bundle_violations(bundle, windows=windows)
    if problems:
        raise RuntimeError("unsafe/incomplete backend bundle:\n- " + "\n- ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--platform", choices=("windows", "linux"), default="windows")
    args = parser.parse_args()
    verify_bundle(args.bundle, windows=args.platform == "windows")
    print(f"Verified backend bundle: {args.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
