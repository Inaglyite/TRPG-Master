#!/usr/bin/env python3
"""Minimal operator CLI for creating the first account and revoking sessions."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.auth import create_user  # noqa: E402
from src.database import (  # noqa: E402
    LoginSession,
    database_url,
    initialize_database,
    session_scope,
    utcnow,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create")
    create.add_argument("username")
    revoke = subcommands.add_parser("revoke-sessions")
    revoke.add_argument("user_id")
    args = parser.parse_args()
    url = args.database_url or database_url()
    if url.startswith("sqlite:"):
        initialize_database(url)
    if args.command == "create":
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("两次密码不一致")
        user = create_user(url, args.username, password)
        print(f"created {user.id} {user.username}")
        return 0
    with session_scope(url) as session:
        rows = session.query(LoginSession).filter_by(user_id=args.user_id, revoked_at=None).all()
        now = utcnow()
        for row in rows:
            row.revoked_at = now
    print(f"revoked {len(rows)} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
