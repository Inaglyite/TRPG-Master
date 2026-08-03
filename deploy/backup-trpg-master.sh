#!/usr/bin/env bash
set -Eeuo pipefail

backup_root="${TRPG_BACKUP_ROOT:-/var/backups/trpg-master}"
runtime_root="${TRPG_BACKUP_RUNTIME_ROOT:-/var/lib/trpg-master}"
backup_prefix="${TRPG_BACKUP_PREFIX:-trpg-master}"
retention_days="${TRPG_BACKUP_RETENTION_DAYS:-30}"

if [[ ! "$backup_root" =~ ^/var/backups/trpg-master(-[a-z0-9][a-z0-9-]{0,63})?$ ]]; then
    echo "unsafe backup root: $backup_root" >&2
    exit 2
fi
if [[ ! "$runtime_root" =~ ^/var/lib/trpg-master(-[a-z0-9][a-z0-9-]{0,63})?$ ]]; then
    echo "unsafe runtime root: $runtime_root" >&2
    exit 2
fi
if [[ ! "$backup_prefix" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    echo "invalid backup prefix" >&2
    exit 2
fi
if [[ ! "$retention_days" =~ ^[1-9][0-9]{0,3}$ ]]; then
    echo "invalid backup retention" >&2
    exit 2
fi

verify_managed_directory() {
    local path="$1"
    local label="$2"
    local create="$3"
    local parent resolved owner mode

    parent="${path%/*}"
    resolved="$(realpath -e -- "$parent" 2>/dev/null || true)"
    if [[ "$resolved" != "$parent" ]]; then
        echo "unsafe $label parent: $parent" >&2
        exit 2
    fi

    # Check an existing entry before install(1) can follow it. A dangling
    # symlink is rejected by the explicit -L branch as well.
    if [[ -e "$path" || -L "$path" ]]; then
        resolved="$(realpath -e -- "$path" 2>/dev/null || true)"
        if [[ "$resolved" != "$path" ]]; then
            echo "unsafe $label: symbolic links are forbidden" >&2
            exit 2
        fi
    elif [[ "$create" -ne 1 ]]; then
        echo "$label does not exist: $path" >&2
        exit 2
    fi

    if [[ "$create" -eq 1 ]]; then
        install -d -m 0700 "$path"
    fi
    resolved="$(realpath -e -- "$path" 2>/dev/null || true)"
    if [[ "$resolved" != "$path" || "$(stat -c %F -- "$path")" != "directory" ]]; then
        echo "unsafe $label: expected a canonical directory" >&2
        exit 2
    fi
    owner="$(stat -c %u -- "$path")"
    mode="$(stat -c %a -- "$path")"
    if [[ "$owner" != "$EUID" || ! "$mode" =~ ^[0-7]{3,4}$ ]] \
        || (( (8#$mode & 0077) != 0 )); then
        echo "unsafe $label ownership or permissions" >&2
        exit 2
    fi
}

verify_managed_directory "$backup_root" "backup root" 1
verify_managed_directory "$runtime_root" "runtime root" 0

# Hold the directory inode lock on an inherited descriptor for the complete
# transaction. There is no environment marker or re-entry mode that a caller
# can forge to skip serialization.
exec 9<"$backup_root"
flock --exclusive 9

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work="$(mktemp -d "$backup_root/.backup-$stamp-XXXXXX")"
partial=""
cleanup() {
    local status=$?
    if [[ -n "$partial" ]]; then
        rm -f -- "$partial"
    fi
    rm -rf -- "$work"
    return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
export GNUPGHOME="$work/gnupg"
install -d -m 0700 "$GNUPGHOME"

: "${TRPG_DATABASE_URL:?TRPG_DATABASE_URL is required}"
: "${TRPG_BACKUP_PASSPHRASE_FILE:?TRPG_BACKUP_PASSPHRASE_FILE is required}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
backup_python="$script_dir/../.venv/bin/python"
if [[ ! -x "$backup_python" ]]; then
    backup_python="$(command -v python3 || true)"
fi
if [[ -z "$backup_python" || ! -x "$backup_python" ]]; then
    echo "Python is required to parse the database URL" >&2
    exit 2
fi

if ! "$backup_python" - "$work" <<'PY'
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


def scalar_query_value(value: object) -> str:
    if not isinstance(value, str):
        fail()
    return value


def safe_field(value: str) -> str:
    if any(character in value for character in ("\x00", "\r", "\n")):
        fail()
    return value


def pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


try:
    work = Path(sys.argv[1])
    url = make_url(os.environ["TRPG_DATABASE_URL"])
    if url.drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }:
        fail()

    query = dict(url.query)
    unknown_query = set(query) - {"host", "port"}
    if unknown_query:
        fail()

    query_host = query.get("host")
    if query_host is not None and url.host not in {None, ""}:
        fail()
    host = (
        scalar_query_value(query_host)
        if query_host is not None
        else (url.host or "")
    )

    query_port = query.get("port")
    if query_port is not None and url.port is not None:
        fail()
    raw_port: object = query_port if query_port is not None else url.port
    if raw_port in {None, ""}:
        port = "5432"
        include_port = False
    else:
        port = (
            scalar_query_value(raw_port)
            if isinstance(raw_port, str)
            else str(raw_port)
        )
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            fail()
        include_port = True

    database = url.database
    if not database:
        fail()
    username = url.username or ""
    password = url.password or ""

    host = safe_field(host)
    port = safe_field(port)
    database = safe_field(database)
    username = safe_field(username)
    password = safe_field(password)

    pgpass_host = host or "localhost"
    pgpass_username = username or "*"
    pgpass_line = ":".join(
        pgpass_escape(field)
        for field in (
            pgpass_host,
            port,
            database,
            pgpass_username,
            password,
        )
    )

    pgpass_path = work / ".pgpass"
    descriptor = os.open(
        pgpass_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(pgpass_line)
            handle.write("\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

    arguments: list[str] = []
    if host:
        arguments.extend(("--host", host))
    if include_port:
        arguments.extend(("--port", port))
    arguments.extend(("--dbname", database))
    if username:
        arguments.extend(("--username", username))

    args_path = work / "pg_dump.args"
    descriptor = os.open(
        args_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for argument in arguments:
                handle.write(argument.encode("utf-8"))
                handle.write(b"\0")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
except SystemExit:
    raise
except Exception:
    fail()
PY
then
    exit 2
fi

unset TRPG_DATABASE_URL PGPASSWORD PGPASSFILE PGHOST PGHOSTADDR PGPORT \
    PGDATABASE PGUSER PGSERVICE PGSERVICEFILE
pg_dump_args=(--format=custom --no-owner --no-acl --no-password)
while IFS= read -r -d '' argument; do
    pg_dump_args+=("$argument")
done < "$work/pg_dump.args"
export PGPASSFILE="$work/.pgpass"
pg_dump "${pg_dump_args[@]}" > "$work/database.dump"
unset PGPASSFILE
tar --create --gzip --file "$work/runtime.tar.gz" \
    --exclude='trpg-master.db*' --directory "$runtime_root" .
(
    cd "$work"
    sha256sum database.dump runtime.tar.gz > SHA256SUMS
)

# Encrypt to a unique hidden file in backup_root so publishing it is a
# same-filesystem atomic rename.  The output is never visible under the final
# backup name until encryption and verification have both succeeded.
partial="$(mktemp "$backup_root/.${backup_prefix}-${stamp}.partial.XXXXXX")"
chmod 0600 "$partial"
tar --create --file - --directory "$work" database.dump runtime.tar.gz SHA256SUMS \
    | gpg --batch --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-file "$TRPG_BACKUP_PASSPHRASE_FILE" \
        > "$partial"

# Do not trust a successful encryption exit status alone.  GPG's authenticated
# decrypt plus a complete tar traversal detects a truncated/corrupt archive
# before it can be published or considered for retention.
gpg --batch --quiet --pinentry-mode loopback \
        --passphrase-file "$TRPG_BACKUP_PASSPHRASE_FILE" \
        --decrypt "$partial" \
    | tar --list --file - >/dev/null

# A second backup completed within the same UTC second gets a suffix instead of
# replacing the first one.  mv --no-clobber also closes the check/rename race;
# it leaves the source in place when another writer wins.
published=""
for sequence in $(seq 0 999); do
    if (( sequence == 0 )); then
        candidate="$backup_root/$backup_prefix-$stamp.tar.gpg"
    else
        printf -v suffix '%02d' "$sequence"
        candidate="$backup_root/$backup_prefix-$stamp-$suffix.tar.gpg"
    fi
    if [[ -e "$candidate" ]]; then
        continue
    fi
    mv --no-clobber -- "$partial" "$candidate"
    if [[ ! -e "$partial" ]]; then
        published="$candidate"
        partial=""
        break
    fi
done
if [[ -z "$published" ]]; then
    echo "unable to publish backup without overwriting an existing file" >&2
    exit 1
fi

find "$backup_root" -maxdepth 1 -type f -name "$backup_prefix-*.tar.gpg" \
    -mtime "+$retention_days" -delete
