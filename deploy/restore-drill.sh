#!/usr/bin/env bash
set -Eeuo pipefail

# restore-drill.sh -- 加密备份的隔离恢复演练（阶段 3）
#
# 默认 dry-run：解密并校验备份、列出 PostgreSQL dump 内容，绝不连接任何数据库。
# --restore 模式：把备份导入到显式指定的隔离演练数据库，并执行只读验证查询。
#
# 安全约束（不变量）：
#   - 脚本从不执行 DROP DATABASE / DROP TABLE / TRUNCATE 等破坏性语句；
#   - pg_restore 不使用 --clean/--if-exists，绝不覆盖已存在的数据库；
#   - --restore 目标数据库名必须以 TRPG_RESTORE_DB_PREFIX（默认 trpg_drill_）开头，
#     且不得与 TRPG_PRODUCTION_DATABASE_URL 中的数据库同名，否则立即退出；
#   - 默认 dry-run 模式不调用任何数据库客户端，不会连接生产或演练数据库。
#
# 用法：
#   restore-drill.sh [--dry-run] [--archive FILE] [--latest]
#   restore-drill.sh --restore postgresql+psycopg://user:pass@host:5432/trpg_drill_yyyymmdd
#
# 环境变量：
#   TRPG_BACKUP_ROOT              备份目录（默认 /var/backups/trpg-master）
#   TRPG_BACKUP_PREFIX            备份文件名前缀（默认 trpg-master）
#   TRPG_BACKUP_PASSPHRASE_FILE   GPG 口令文件（必需）
#   TRPG_PG_RESTORE               可选：pg_restore 可执行文件路径
#                                  （PostgreSQL client bin 常不在 PATH，例如
#                                   /usr/lib/postgresql/17/bin/pg_restore）
#   TRPG_RESTORE_DB_PREFIX        --restore 目标数据库名必须以此开头（默认 trpg_drill_）
#   TRPG_PRODUCTION_DATABASE_URL  可选：生产数据库 URL，目标与其同库名时拒绝

usage() {
    grep -E '^#( |$)' -- "$0" | sed 's/^# \{0,1\}//'
}

mode=dry-run
target_url=""
archive=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) mode=dry-run ;;
        --latest) archive="" ;;
        --archive)
            if [[ $# -lt 2 ]]; then
                echo "--archive requires a path" >&2
                exit 2
            fi
            archive="$2"
            shift
            ;;
        --restore)
            if [[ $# -lt 2 ]]; then
                echo "--restore requires a database URL" >&2
                exit 2
            fi
            mode=restore
            target_url="$2"
            shift
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

backup_root="${TRPG_BACKUP_ROOT:-/var/backups/trpg-master}"
backup_prefix="${TRPG_BACKUP_PREFIX:-trpg-master}"
passphrase_file="${TRPG_BACKUP_PASSPHRASE_FILE:-}"
pg_restore_bin="${TRPG_PG_RESTORE:-pg_restore}"
restore_prefix="${TRPG_RESTORE_DB_PREFIX:-trpg_drill_}"
production_url="${TRPG_PRODUCTION_DATABASE_URL:-}"

# pg_restore 是 dry-run 与 restore 的共同前置依赖，缺失时清晰失败。
if ! command -v "$pg_restore_bin" >/dev/null 2>&1; then
    echo "pg_restore is required but was not found (looked for: $pg_restore_bin)" >&2
    echo "install postgresql-client or point TRPG_PG_RESTORE at the pg_restore binary" >&2
    exit 2
fi

if [[ ! "$backup_prefix" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    echo "invalid backup prefix: $backup_prefix" >&2
    exit 2
fi
if [[ -z "$passphrase_file" ]]; then
    echo "TRPG_BACKUP_PASSPHRASE_FILE is required" >&2
    exit 2
fi
if [[ ! "$restore_prefix" =~ ^[a-z_][a-z0-9_]{0,32}$ ]]; then
    echo "invalid TRPG_RESTORE_DB_PREFIX (must match ^[a-z_][a-z0-9_]{0,32}$): $restore_prefix" >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
drill_python="$script_dir/../.venv/bin/python"
if [[ ! -x "$drill_python" ]]; then
    drill_python="$(command -v python3 || true)"
fi
if [[ -z "$drill_python" || ! -x "$drill_python" ]]; then
    echo "Python is required to parse the database URL" >&2
    exit 2
fi

# ---- --restore 目标校验：先于任何解密/连接执行 ----
work=""
target_dbname=""
production_dbname=""
if [[ "$mode" == "restore" ]]; then
    if [[ -z "$target_url" ]]; then
        echo "--restore requires a database URL" >&2
        exit 2
    fi

    work="$(mktemp -d "${TMPDIR:-/tmp}/.restore-drill-XXXXXX")"
    cleanup() {
        rm -rf -- "$work"
    }
    trap cleanup EXIT

    if ! "$drill_python" - "$work" "$target_url" "$production_url" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from sqlalchemy.engine import make_url
except Exception:
    print("SQLAlchemy is required to parse the database URL", file=sys.stderr)
    raise SystemExit(2)


def fail() -> None:
    print("invalid PostgreSQL database URL", file=sys.stderr)
    raise SystemExit(2)


def scalar(value: object) -> str:
    if not isinstance(value, str):
        fail()
    if any(character in value for character in ("\x00", "\r", "\n")):
        fail()
    return value


def pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


try:
    work = Path(sys.argv[1])
    target = make_url(sys.argv[2])
    production = make_url(sys.argv[3]) if sys.argv[3] else None
    if target.drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    } or not target.database:
        fail()

    dbname = target.database
    if not isinstance(dbname, str) or not dbname.isascii() \
            or not all(character.isalnum() or character == "_" for character in dbname):
        fail()
    (work / "target_dbname").write_text(dbname, encoding="utf-8")

    host = target.host or ""
    if not isinstance(host, str) or any(character in host for character in ("\x00", "\r", "\n")):
        fail()
    port = str(target.port or 5432)
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        fail()
    username = target.username or ""
    if not isinstance(username, str) or any(
        character in username for character in ("\x00", "\r", "\n")
    ):
        fail()
    password = target.password or ""

    pgpass_host = host or "localhost"
    pgpass_username = username or "*"
    line = ":".join(
        pgpass_escape(field)
        for field in (pgpass_host, port, dbname, pgpass_username, password)
    )
    path = work / ".pgpass"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    (work / "pgargs").write_text(
        "\n".join((host, port, pgpass_username, dbname)) + "\n",
        encoding="utf-8",
    )

    if production is not None:
        if production.drivername not in {
            "postgresql",
            "postgresql+psycopg",
            "postgresql+psycopg2",
        } or not production.database:
            fail()
        production_db = production.database
        if not isinstance(production_db, str) or not production_db.isascii() \
                or not all(character.isalnum() or character == "_" for character in production_db):
            fail()
        (work / "production_dbname").write_text(production_db, encoding="utf-8")
except SystemExit:
    raise
except Exception:
    fail()
PY
    then
        exit 2
    fi

    target_dbname="$(<"$work/target_dbname")"
    if [[ "$target_dbname" != "$restore_prefix"* ]]; then
        echo "refusing to restore: database name '$target_dbname' does not start" \
            "with the drill prefix '$restore_prefix'" >&2
        echo "use an isolated database such as ${restore_prefix}yyyymmdd; never a production database" >&2
        exit 2
    fi
    if [[ -f "$work/production_dbname" ]]; then
        production_dbname="$(<"$work/production_dbname")"
        if [[ "$target_dbname" == "$production_dbname" ]]; then
            echo "refusing to restore: '$target_dbname' is the configured production database" >&2
            exit 2
        fi
    fi
    if ! command -v psql >/dev/null 2>&1; then
        echo "psql is required for --restore but was not found" >&2
        exit 2
    fi
    if ! command -v createdb >/dev/null 2>&1; then
        echo "createdb is required for --restore but was not found" >&2
        exit 2
    fi
fi

# ---- 选择并校验备份归档 ----
# 演练脚本只读备份根（写路径约束见 backup-trpg-master.sh），因此不硬性限制
# /var/backups 前缀，但要求绝对路径、真实目录且不是符号链接；
# 备份归档本身仍必须是 backup_root 下的常规文件（下方校验）。
if [[ "$backup_root" != /* ]]; then
    echo "backup root must be an absolute path: $backup_root" >&2
    exit 2
fi
if [[ -L "$backup_root" ]]; then
    echo "backup root must not be a symbolic link: $backup_root" >&2
    exit 2
fi
backup_root_real="$(realpath -e -- "$backup_root" 2>/dev/null || true)"
if [[ -z "$backup_root_real" || "$backup_root_real" != "$backup_root" ]]; then
    echo "unsafe backup root: $backup_root" >&2
    exit 2
fi
if [[ ! -d "$backup_root" ]]; then
    echo "backup root does not exist: $backup_root" >&2
    exit 2
fi
if [[ -z "$archive" ]]; then
    archive="$(find "$backup_root" -maxdepth 1 -type f \
        -name "$backup_prefix-*.tar.gpg" -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | head -1 | cut -d' ' -f2-)"
    if [[ -z "$archive" ]]; then
        echo "no backup found in $backup_root (prefix $backup_prefix)" >&2
        exit 2
    fi
fi
if [[ ! -f "$archive" ]]; then
    echo "backup archive not found: $archive" >&2
    exit 2
fi
archive_real="$(realpath -e -- "$archive" 2>/dev/null || true)"
if [[ -z "$archive_real" || "$archive_real" != "$archive" ]]; then
    echo "backup archive must be a regular file, not a symlink: $archive" >&2
    exit 2
fi
case "$archive" in
    "$backup_root"/*.tar.gpg) ;;
    *)
        echo "backup archive must live under $backup_root and end in .tar.gpg: $archive" >&2
        exit 2
        ;;
esac

if ! command -v gpg >/dev/null 2>&1; then
    echo "gpg is required to decrypt backups but was not found" >&2
    exit 2
fi
if [[ ! -f "$passphrase_file" ]]; then
    echo "backup passphrase file not found: $passphrase_file" >&2
    exit 2
fi

# ---- 解密并校验备份内容 ----
if [[ -z "$work" ]]; then
    work="$(mktemp -d "${TMPDIR:-/tmp}/.restore-drill-XXXXXX")"
    cleanup() {
        rm -rf -- "$work"
    }
    trap cleanup EXIT
fi
if ! gpg --batch --quiet --pinentry-mode loopback \
        --passphrase-file "$passphrase_file" --decrypt "$archive" \
    | tar --extract --gzip --file - --directory "$work"; then
    echo "backup decryption or extraction failed: $archive" >&2
    exit 1
fi
if [[ ! -f "$work/database.dump" || ! -f "$work/SHA256SUMS" ]]; then
    echo "backup is missing database.dump or SHA256SUMS: $archive" >&2
    exit 1
fi
if ! (cd "$work" && sha256sum -c SHA256SUMS); then
    echo "backup checksum verification failed: $archive" >&2
    exit 1
fi

if [[ "$mode" == "dry-run" ]]; then
    # 只读演练：列出 dump 内容，不连接任何数据库，不设置任何 libpq 连接参数。
    unset PGPASSWORD PGPASSFILE PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER \
        PGSERVICE PGSERVICEFILE
    echo "dry-run restore drill for $archive"
    echo "--- pg_restore --list (database.dump members) ---"
    if ! "$pg_restore_bin" --list "$work/database.dump"; then
        echo "pg_restore could not read database.dump" >&2
        exit 1
    fi
    echo "--- dry-run passed: archive decrypts, checksums match, dump is readable ---"
    exit 0
fi

# ---- 恢复演练：导入隔离数据库 ----
if [[ -z "$target_dbname" ]]; then
    echo "internal error: missing target database name" >&2
    exit 2
fi
IFS= read -r pg_host < <(sed -n '1p' "$work/pgargs")
IFS= read -r pg_port < <(sed -n '2p' "$work/pgargs")
IFS= read -r pg_user < <(sed -n '3p' "$work/pgargs")
export PGPASSFILE="$work/.pgpass"
unset PGPASSWORD PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER \
    PGSERVICE PGSERVICEFILE
if [[ -n "$pg_host" ]]; then
    export PGHOST="$pg_host"
fi
if [[ -n "$pg_port" ]]; then
    export PGPORT="$pg_port"
fi
if [[ -n "$pg_user" ]]; then
    export PGUSER="$pg_user"
fi

exists="$(PGDATABASE=postgres psql -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$target_dbname'")"
if [[ "$exists" == "1" ]]; then
    echo "drill database already exists: $target_dbname" >&2
    echo "restore-drill never drops databases; remove it manually with:" >&2
    echo "  PGDATABASE=postgres psql -c 'DROP DATABASE $target_dbname'" >&2
    echo "or pick a new drill database name" >&2
    exit 1
fi
PGDATABASE=postgres createdb "$target_dbname"
if ! PGDATABASE="$target_dbname" "$pg_restore_bin" --no-owner --no-acl \
        --exit-on-error "$work/database.dump"; then
    echo "restore into drill database failed: $target_dbname" >&2
    echo "the partially restored drill database was left in place for inspection;" >&2
    echo "remove it manually (the script never drops databases) and re-run" >&2
    exit 1
fi

# 只读验证：关键表存在 + 核心数据计数。
expected_tables=(users worlds world_members world_states world_invites \
    world_investigators sessions turns turn_events snapshots save_slots \
    player_notes model_calls room_actions audit_events)
present="$(PGDATABASE="$target_dbname" psql -tAc \
    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")"
missing=()
for table in "${expected_tables[@]}"; do
    if ! grep -qx "$table" <<<"$present"; then
        missing+=("$table")
    fi
done
if [[ "${#missing[@]}" -gt 0 ]]; then
    echo "drill verification failed: missing tables: $(IFS=,; echo "${missing[*]}")" >&2
    exit 1
fi
counts="$(PGDATABASE="$target_dbname" psql -tAc \
    "SELECT 'users=' || count(*) FROM users UNION ALL \
     SELECT 'worlds=' || count(*) FROM worlds UNION ALL \
     SELECT 'turns=' || count(*) FROM turns")"
echo "--- restore drill verification ---"
echo "$counts"
echo "restore drill passed: $archive -> $target_dbname"
echo "drill database '$target_dbname' was left in place for inspection;"
echo "remove it manually with: PGDATABASE=postgres psql -c 'DROP DATABASE $target_dbname'"
