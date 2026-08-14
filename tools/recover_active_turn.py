#!/usr/bin/env python3
"""Explicit local-operator recovery for a fenced active database turn.

This is deliberately not an HTTP/admin endpoint.  It is for the rare case in
which automatic recovery correctly fails closed because an active turn belongs
to a legacy, remote, or otherwise unknowable process owner.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shlex
import socket
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.database import World, session_scope  # noqa: E402
from src.database_turn_journal import DatabaseTurnJournal  # noqa: E402
from src.turn_journal import TurnJournalError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读检查或由本机维护者显式中断一个未知所有者的活动回合。"
            " 不提供网络接口。"
        )
    )
    parser.add_argument("--world-id", required=True, help="要检查的既有世界 ID")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help="运行数据目录；默认读取 TRPG_RUNTIME_ROOT（桌面版默认为当前项目）",
    )
    parser.add_argument(
        "--database-url",
        help="可选的数据库 URL；优先从受限环境变量注入，避免把凭据留在 shell 历史中",
    )
    parser.add_argument(
        "--expected-turn-id",
        help="只读检查输出的活动回合 ID；强制操作时必填",
    )
    parser.add_argument(
        "--expected-owner-token",
        help="只读检查输出的当前 owner token；强制操作时必填，用作 CAS 栅栏",
    )
    parser.add_argument(
        "--reason",
        default="维护者确认原服务已停止，回收遗留活动回合",
        help="写入审计记录的回收原因（1-500 字符）",
    )
    parser.add_argument(
        "--operator",
        help="写入审计记录的操作者标识；默认是当前系统用户和主机名",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="执行中断；不带此参数始终只读",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认已停止/核实原服务；必须与 --force 同时提供",
    )
    return parser


def _runtime_root(value: Path | None) -> Path:
    if value is not None:
        return value.expanduser().resolve()
    configured = os.environ.get("TRPG_RUNTIME_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else ROOT


def _validate_world_id(value: object) -> str:
    world_id = str(value or "").strip()
    if not world_id or world_id in {".", ".."} or Path(world_id).name != world_id:
        raise ValueError(f"非法 world_id: {world_id!r}")
    return world_id


def _existing_database_url(runtime_root: Path) -> str:
    """Resolve the configured database without creating a directory or schema."""
    configured = os.environ.get("TRPG_DATABASE_URL", "").strip()
    if configured:
        db_url = configured
    else:
        database_file = runtime_root / "trpg-master.db"
        if not database_file.is_file():
            raise TurnJournalError(f"SQLite 数据库不存在: {database_file}")
        db_url = f"sqlite:///{database_file}"

    parsed = make_url(db_url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in {None, ":memory:"}:
        database_file = Path(parsed.database)
        if not database_file.is_absolute():
            database_file = (Path.cwd() / database_file).resolve()
        if not database_file.is_file():
            raise TurnJournalError(f"SQLite 数据库不存在: {database_file}")
    return db_url


def _journal_for_existing_world(args: argparse.Namespace) -> DatabaseTurnJournal:
    """Open only existing metadata; never initialize or migrate a world/database."""
    world_id = _validate_world_id(args.world_id)
    runtime_root = _runtime_root(args.runtime_root)
    db_url = _existing_database_url(runtime_root)
    with session_scope(db_url) as session:
        world = session.get(World, world_id)
        if world is None:
            raise TurnJournalError(f"世界不存在: {world_id}")
        module_name = world.module_name
    return DatabaseTurnJournal(
        runtime_root / "worlds" / world_id,
        world_id=world_id,
        module_name=module_name,
        # A no-force invocation must be truly read-only.  Automatic recovery
        # remains the default for normal engine construction.
        recover_on_init=False,
    )


def _print_candidates(
    world_id: str, runtime_root: Path, candidates: list[dict]
) -> None:
    if not candidates:
        print("没有活动回合；无需强制回收。")
        return
    print("活动回合（仅本机维护者可见 owner token）：")
    for candidate in candidates:
        print(
            "- "
            f"turn_id={candidate['turn_id']} "
            f"kind={candidate['kind']} "
            f"created_at={candidate['created_at']}"
        )
        print(f"  owner_token={candidate['owner_token']}")
        print(
            "  仅在确认原服务已停止后执行：\n"
            "  python tools/recover_active_turn.py "
            f"--world-id {shlex.quote(world_id)} "
            f"--runtime-root {shlex.quote(str(runtime_root))} "
            f"--expected-turn-id {shlex.quote(candidate['turn_id'])} "
            f"--expected-owner-token {shlex.quote(candidate['owner_token'])} "
            "--force --yes"
        )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.force:
        if not args.yes:
            parser.error("--force 必须同时提供 --yes")
        if not args.expected_turn_id or args.expected_owner_token is None:
            parser.error("--force 必须提供 --expected-turn-id 和 --expected-owner-token")
    elif args.yes:
        parser.error("--yes 只能与 --force 一起使用")
    elif args.expected_turn_id or args.expected_owner_token is not None:
        parser.error("比较字段仅用于 --force；先执行一次只读检查")

    previous_database_url = os.environ.get("TRPG_DATABASE_URL")
    try:
        if args.database_url:
            os.environ["TRPG_DATABASE_URL"] = args.database_url
        journal = _journal_for_existing_world(args)
        if not args.force:
            _print_candidates(
                journal.world_id,
                journal.world_dir.parent.parent,
                journal.active_turn_recovery_candidates(),
            )
            return 0

        operator = args.operator or f"{getpass.getuser() or 'unknown'}@{socket.gethostname()}"
        recovered = journal.force_interrupt_active_turn(
            expected_turn_id=args.expected_turn_id,
            expected_owner_token=args.expected_owner_token,
            reason=args.reason,
            operator=operator,
        )
    except (FileNotFoundError, TurnJournalError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    finally:
        if args.database_url:
            if previous_database_url is None:
                os.environ.pop("TRPG_DATABASE_URL", None)
            else:
                os.environ["TRPG_DATABASE_URL"] = previous_database_url

    print(
        f"已中断回合 {recovered['turn_id']}；状态为 interrupted，"
        "未提交的叙述和状态变更不会被当作完成回合恢复。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
