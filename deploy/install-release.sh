#!/usr/bin/env bash
set -Eeuo pipefail

release_id="${1:?release id is required}"
archive="${2:?release archive is required}"
root=/opt/trpg-master
release="$root/releases/$release_id"
previous=""
candidate=""
archive_copy=""
config_backup=""
rollback_needed=0
service_was_enabled=0
service_was_active=0
timer_was_enabled=0
timer_was_active=0
context_gc_timer_was_enabled=0
context_gc_timer_was_active=0
monitor_timer_was_enabled=0
monitor_timer_was_active=0

service_name=trpg-master.service
backup_timer=trpg-master-backup.timer
context_gc_timer=trpg-master-context-gc.timer
monitor_service=trpg-master-monitor.service
monitor_timer=trpg-master-monitor.timer
health_url=http://127.0.0.1:8765/api/ready
unit_dir=/etc/systemd/system
nginx_available=/etc/nginx/sites-available/trpg-master
nginx_enabled=/etc/nginx/sites-enabled/trpg-master
installer_target=/usr/local/sbin/trpg-install-release

if [[ ! "$release_id" =~ ^[0-9a-f]{7,64}$ ]]; then
    echo "invalid release id" >&2
    exit 2
fi
if [[ ! -f "$archive" ]]; then
    echo "release archive not found" >&2
    exit 2
fi

install -d -m 0755 "$root" "$root/releases"
install -d -m 0700 -o trpgdeploy -g trpgdeploy \
    /var/lib/trpg-master /var/log/trpg-master /var/backups/trpg-master
install -d -m 0755 "$unit_dir" /etc/nginx/sites-available /etc/nginx/sites-enabled \
    /usr/local/sbin

exec 9>"$root/.install.lock"
if ! flock -n 9; then
    echo "another production release installation is already running" >&2
    exit 3
fi

if [[ -L "$root/current" ]]; then
    previous="$(readlink -f "$root/current")"
    case "$previous" in
        "$root"/releases/*) ;;
        *)
            echo "current release points outside managed releases: $previous" >&2
            exit 2
            ;;
    esac
elif [[ -e "$root/current" ]]; then
    echo "current path exists but is not a symlink" >&2
    exit 2
fi
systemctl is-enabled --quiet "$service_name" >/dev/null 2>&1 && service_was_enabled=1
systemctl is-active --quiet "$service_name" >/dev/null 2>&1 && service_was_active=1
systemctl is-enabled --quiet "$backup_timer" >/dev/null 2>&1 && timer_was_enabled=1
systemctl is-active --quiet "$backup_timer" >/dev/null 2>&1 && timer_was_active=1
systemctl is-enabled --quiet "$context_gc_timer" >/dev/null 2>&1 && context_gc_timer_was_enabled=1
systemctl is-active --quiet "$context_gc_timer" >/dev/null 2>&1 && context_gc_timer_was_active=1
systemctl is-enabled --quiet "$monitor_timer" >/dev/null 2>&1 && monitor_timer_was_enabled=1
systemctl is-active --quiet "$monitor_timer" >/dev/null 2>&1 && monitor_timer_was_active=1

cleanup_stale_release_artifacts() {
    local entry name
    while IFS= read -r -d '' entry; do
        [[ "$entry" == "$previous" ]] && continue
        name="${entry##*/}"
        if [[ "$name" =~ ^\.install-[0-9a-f]{7,64}-[A-Za-z0-9]{6}$ ]] \
            || [[ "$name" =~ ^\.incomplete-[0-9a-f]{7,64}-[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]]; then
            rm -rf -- "$entry"
        fi
    done < <(
        find "$root/releases" -mindepth 1 -maxdepth 1 -type d \
            \( -name '.install-*' -o -name '.incomplete-*' \) \
            -mmin +1440 -print0
    )
}
cleanup_stale_release_artifacts

wait_for_service_ready() {
    local attempts="${1:-30}"
    local attempt
    for ((attempt=0; attempt<attempts; attempt++)); do
        if curl --fail --silent --show-error --max-time 2 \
            "$health_url" >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

backup_managed_path() {
    local target="$1"
    local key="$2"
    if [[ -L "$target" ]]; then
        printf 'symlink\n' >"$config_backup/$key.kind"
        readlink "$target" >"$config_backup/$key.target"
    elif [[ -f "$target" ]]; then
        printf 'file\n' >"$config_backup/$key.kind"
        cp -a -- "$target" "$config_backup/$key.data"
    elif [[ -e "$target" ]]; then
        echo "managed path is not a regular file or symlink: $target" >&2
        return 1
    else
        printf 'missing\n' >"$config_backup/$key.kind"
    fi
}

restore_managed_path() {
    local target="$1"
    local key="$2"
    local kind
    kind="$(<"$config_backup/$key.kind")"
    rm -f -- "$target"
    case "$kind" in
        file) cp -a -- "$config_backup/$key.data" "$target" ;;
        symlink) ln -s -- "$(<"$config_backup/$key.target")" "$target" ;;
        missing) ;;
        *) echo "unknown backup kind for $target: $kind" >&2; return 1 ;;
    esac
}

install_managed_file() {
    local source="$1"
    local target="$2"
    local mode="$3"
    local temporary
    temporary="$(mktemp "$(dirname "$target")/.${target##*/}.XXXXXX")"
    if ! install -o root -g root -m "$mode" "$source" "$temporary"; then
        rm -f -- "$temporary"
        return 1
    fi
    if ! mv -Tf -- "$temporary" "$target"; then
        rm -f -- "$temporary"
        return 1
    fi
}

rollback_release() {
    local failed=0
    echo "release activation failed; restoring previous service configuration" >&2
    restore_managed_path "$nginx_enabled" nginx-enabled || failed=1
    restore_managed_path "$nginx_available" nginx-available || failed=1
    restore_managed_path "$unit_dir/trpg-master-context-gc.timer" context-gc-timer || failed=1
    restore_managed_path "$unit_dir/trpg-master-context-gc.service" context-gc-service || failed=1
    restore_managed_path "$unit_dir/trpg-master-backup.timer" backup-timer || failed=1
    restore_managed_path "$unit_dir/trpg-master-backup.service" backup-service || failed=1
    restore_managed_path "$unit_dir/trpg-master-monitor.timer" monitor-timer || failed=1
    restore_managed_path "$unit_dir/trpg-master-monitor.service" monitor-service || failed=1
    restore_managed_path "$unit_dir/trpg-master.service" app-service || failed=1
    restore_managed_path "$installer_target" installer || failed=1

    if [[ -n "$previous" && -d "$previous" ]]; then
        if ! ln -sfn "$previous" "$root/current.next" \
            || ! mv -Tf "$root/current.next" "$root/current"; then
            failed=1
        fi
    elif [[ -L "$root/current" ]]; then
        rm -f -- "$root/current" || failed=1
    fi

    systemctl daemon-reload || failed=1
    if [[ "$service_was_enabled" -eq 1 ]]; then
        systemctl enable "$service_name" || failed=1
    elif systemctl cat "$service_name" >/dev/null 2>&1; then
        systemctl disable "$service_name" || failed=1
    fi
    if [[ "$timer_was_enabled" -eq 1 ]]; then
        systemctl enable "$backup_timer" || failed=1
    elif systemctl cat "$backup_timer" >/dev/null 2>&1; then
        systemctl disable "$backup_timer" || failed=1
    fi
    if [[ "$timer_was_active" -eq 1 ]]; then
        systemctl start "$backup_timer" || failed=1
    elif systemctl cat "$backup_timer" >/dev/null 2>&1; then
        systemctl stop "$backup_timer" || failed=1
    fi
    if [[ "$context_gc_timer_was_enabled" -eq 1 ]]; then
        systemctl enable "$context_gc_timer" || failed=1
    elif systemctl cat "$context_gc_timer" >/dev/null 2>&1; then
        systemctl disable "$context_gc_timer" || failed=1
    fi
    if [[ "$context_gc_timer_was_active" -eq 1 ]]; then
        systemctl start "$context_gc_timer" || failed=1
    elif systemctl cat "$context_gc_timer" >/dev/null 2>&1; then
        systemctl stop "$context_gc_timer" || failed=1
    fi
    if [[ "$monitor_timer_was_enabled" -eq 1 ]]; then
        systemctl enable "$monitor_timer" || failed=1
    elif systemctl cat "$monitor_timer" >/dev/null 2>&1; then
        systemctl disable "$monitor_timer" || failed=1
    fi
    if [[ "$monitor_timer_was_active" -eq 1 ]]; then
        systemctl start "$monitor_timer" || failed=1
    elif systemctl cat "$monitor_timer" >/dev/null 2>&1; then
        systemctl stop "$monitor_timer" || failed=1
    fi
    if nginx -t; then
        systemctl reload nginx.service || failed=1
    else
        failed=1
    fi
    if [[ "$service_was_active" -eq 1 && -n "$previous" && -d "$previous" ]]; then
        if ! systemctl restart "$service_name" || ! wait_for_service_ready 30; then
            journalctl -u "$service_name" --no-pager -n 80 >&2 || true
            echo "previous release did not recover readiness" >&2
            failed=1
        fi
    elif systemctl cat "$service_name" >/dev/null 2>&1; then
        systemctl stop "$service_name" || failed=1
    fi
    if [[ "$failed" -ne 0 ]]; then
        echo "CRITICAL: release rollback was incomplete" >&2
        return 1
    fi
    echo "previous release configuration and service state restored" >&2
}

on_exit() {
    local status=$?
    trap - EXIT
    if [[ "$status" -ne 0 && "$rollback_needed" -eq 1 ]]; then
        rollback_release || true
    fi
    if [[ -n "$candidate" && -d "$candidate" ]]; then
        rm -rf -- "$candidate"
    fi
    if [[ -n "$archive_copy" ]]; then
        rm -f -- "$archive_copy"
    fi
    if [[ -n "$config_backup" && -d "$config_backup" ]]; then
        rm -rf -- "$config_backup"
    fi
    exit "$status"
}
trap on_exit EXIT

if [[ -L "$release" ]]; then
    echo "release path must not be a symlink: $release" >&2
    exit 2
fi
if [[ -d "$release" && -f "$release/.release-complete" ]] \
    && [[ ! -L "$release/.release-complete" ]] \
    && [[ "$(<"$release/.release-complete")" == "$release_id" ]]; then
    echo "using completed release: $release_id"
elif [[ -e "$release" ]]; then
    if [[ "$previous" == "$release" ]]; then
        echo "active release is missing its completion marker; refusing same-SHA replacement" >&2
        exit 2
    fi
    quarantine="$root/releases/.incomplete-$release_id-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    echo "moving incomplete release aside: $quarantine" >&2
    mv -- "$release" "$quarantine"
fi

if [[ ! -d "$release" ]]; then
    if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
        echo "Python 3.12 or newer is required to install a release" >&2
        exit 2
    fi
    candidate="$(mktemp -d "$root/releases/.install-$release_id-XXXXXX")"
    archive_copy="$(mktemp "$root/releases/.archive-$release_id-XXXXXX")"
    install -o root -g root -m 0600 "$archive" "$archive_copy"
    if ! python3 - "$archive_copy" "$candidate" <<'PY'
# release-archive-validator-v1
from __future__ import annotations

import gzip
import sys
import tarfile
from pathlib import Path

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 1024 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TRAILING_ZERO_BYTES = 20 * 1024 * 1024
MAX_TAR_STREAM_BYTES = (
    MAX_UNCOMPRESSED_BYTES
    + MAX_MEMBERS * 1024
    + MAX_TRAILING_ZERO_BYTES
)
READ_CHUNK_BYTES = 1024 * 1024
ZERO_BLOCK = b"\0" * 512
REGULAR_TYPES = {b"\0", b"0"}
METADATA_TYPES = {b"x", b"g", b"L", b"K"}


class ArchiveValidationError(Exception):
    pass


def reject(message: str) -> None:
    raise ArchiveValidationError(message)


class BoundedReader:
    def __init__(self, source: gzip.GzipFile) -> None:
        self.source = source
        self.total = 0

    def read(self, size: int = READ_CHUNK_BYTES) -> bytes:
        if size < 0:
            size = READ_CHUNK_BYTES
        request = min(size, READ_CHUNK_BYTES, MAX_TAR_STREAM_BYTES - self.total + 1)
        if request <= 0:
            reject("uncompressed archive stream is too large")
        chunk = self.source.read(request)
        self.total += len(chunk)
        if self.total > MAX_TAR_STREAM_BYTES:
            reject("uncompressed archive stream is too large")
        return chunk

    def read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.read(size - len(chunks))
            if not chunk:
                reject("truncated tar stream")
            chunks.extend(chunk)
        return bytes(chunks)

    def discard(self, size: int) -> None:
        remaining = size
        while remaining:
            chunk = self.read(min(remaining, READ_CHUNK_BYTES))
            if not chunk:
                reject("truncated tar member")
            remaining -= len(chunk)


def tar_number(field: bytes) -> int:
    if field and field[0] & 0x80:
        reject("base-256 tar numbers are forbidden")
    value = field.rstrip(b"\0 ").lstrip(b" ")
    if not value:
        return 0
    if any(character not in b"01234567" for character in value):
        reject("invalid tar number")
    return int(value, 8)


def verify_header_checksum(header: bytes) -> None:
    expected = tar_number(header[148:156])
    actual = sum(header[:148]) + (8 * ord(" ")) + sum(header[156:])
    if expected != actual:
        reject("invalid tar header checksum")


def preflight_physical_stream(archive_path: Path) -> None:
    with archive_path.open("rb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
            reader = BoundedReader(compressed)
            count = 0
            total_size = 0
            zero_blocks = 0
            while zero_blocks < 2:
                header = reader.read_exact(512)
                if header == ZERO_BLOCK:
                    zero_blocks += 1
                    continue
                if zero_blocks:
                    reject("single zero block inside tar stream")
                verify_header_checksum(header)
                count += 1
                if count > MAX_MEMBERS:
                    reject("too many archive records")
                size = tar_number(header[124:136])
                kind = header[156:157]
                if kind in REGULAR_TYPES:
                    if size > MAX_SINGLE_FILE_BYTES:
                        reject("archive member is too large")
                    total_size += size
                    if total_size > MAX_UNCOMPRESSED_BYTES:
                        reject("uncompressed archive is too large")
                elif kind in METADATA_TYPES:
                    if size > MAX_METADATA_BYTES:
                        reject("tar metadata record is too large")
                elif kind == b"5":
                    if size:
                        reject("directory record has a body")
                else:
                    reject("links and special files are forbidden")
                reader.discard((size + 511) // 512 * 512)

            trailing = 0
            while True:
                chunk = reader.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                trailing += len(chunk)
                if trailing > MAX_TRAILING_ZERO_BYTES or any(chunk):
                    reject("invalid data after tar end marker")


def validate_member(
    member: tarfile.TarInfo,
    seen: set[str],
    state: dict[str, int],
) -> None:
    state["count"] += 1
    if state["count"] > MAX_MEMBERS:
        reject("too many archive members")
    name = member.name.rstrip("/")
    parts = name.split("/")
    if (
        not name
        or len(name) > 4096
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 for character in name)
    ):
        reject("unsafe member path")
    normalized = "/".join(parts)
    if normalized in seen:
        reject("duplicate member path")
    seen.add(normalized)
    if not (member.isfile() or member.isdir()):
        reject("links and special files are forbidden")
    if member.size < 0:
        reject("invalid member size")
    if member.isfile():
        if member.size > MAX_SINGLE_FILE_BYTES:
            reject("archive member is too large")
        state["total"] += member.size
        if state["total"] > MAX_UNCOMPRESSED_BYTES:
            reject("uncompressed archive is too large")


def validate_logical_archive(archive_path: Path) -> None:
    seen: set[str] = set()
    state = {"count": 0, "total": 0}
    with archive_path.open("rb") as raw:
        with tarfile.open(fileobj=raw, mode="r|gz") as bundle:
            for member in bundle:
                validate_member(member, seen, state)
    if not state["count"]:
        reject("empty archive")


def extract_validated(archive_path: Path, destination: Path) -> None:
    seen: set[str] = set()
    state = {"count": 0, "total": 0}
    with archive_path.open("rb") as raw:
        with tarfile.open(fileobj=raw, mode="r|gz") as bundle:
            for member in bundle:
                validate_member(member, seen, state)
                bundle.extract(member, destination, filter="data")
    if not state["count"]:
        reject("empty archive")


try:
    archive_path = Path(sys.argv[1])
    destination = Path(sys.argv[2]).resolve(strict=True)
    if (
        archive_path.stat().st_size <= 0
        or archive_path.stat().st_size > MAX_ARCHIVE_BYTES
    ):
        reject("compressed archive size is invalid")
    if not destination.is_dir() or destination.is_symlink() or any(destination.iterdir()):
        reject("destination is not an empty directory")
    preflight_physical_stream(archive_path)
    validate_logical_archive(archive_path)
    extract_validated(archive_path, destination)
except ArchiveValidationError as exc:
    print(f"invalid release archive: {exc}", file=sys.stderr)
    raise SystemExit(2)
except (EOFError, MemoryError, OSError, tarfile.TarError, ValueError):
    print("invalid release archive: unreadable or corrupt", file=sys.stderr)
    raise SystemExit(2)
PY
    then
        exit 2
    fi
    rm -f -- "$archive_copy"
    archive_copy=""

    required_files=(
        server.py
        requirements.txt
        alembic.ini
        frontend/dist/index.html
        deploy/install-release.sh
        deploy/backup-trpg-master.sh
        deploy/trpg-master.service
        deploy/trpg-master-backup.service
        deploy/trpg-master-backup.timer
        deploy/trpg-master-context-gc.service
        deploy/trpg-master-context-gc.timer
        deploy/trpg-master-monitor.service
        deploy/trpg-master-monitor.timer
        deploy/monitor-trpg-master.sh
        deploy/restore-drill.sh
        deploy/nginx-trpg-master.conf
    )
    validate_required_release_files() {
        local required
        for required in "${required_files[@]}"; do
            if [[ ! -f "$candidate/$required" || -L "$candidate/$required" ]]; then
                echo "release is missing a regular file: $required" >&2
                return 1
            fi
        done
    }
    validate_required_release_files

    chmod 0755 "$candidate/deploy/install-release.sh" \
        "$candidate/deploy/backup-trpg-master.sh" \
        "$candidate/deploy/monitor-trpg-master.sh" \
        "$candidate/deploy/restore-drill.sh"
    chmod 0755 "$candidate"
    install -d -m 0700 -o trpgdeploy -g trpgdeploy "$candidate/.venv"
    runuser -u trpgdeploy -- python3 -m venv "$candidate/.venv"
    runuser -u trpgdeploy -- "$candidate/.venv/bin/pip" \
        install --disable-pip-version-check --no-cache-dir \
        -r "$candidate/requirements.txt"
    # pip writes absolute shebangs to console scripts.  The venv is built in
    # a temporary directory and then atomically renamed to the release path,
    # so repair those entry points before activation; otherwise systemd's
    # ExecStartPre would keep pointing at the removed .install-* directory.
    find "$candidate/.venv/bin" -maxdepth 1 -type f -perm /111 -exec \
        sed -i \
            -e "1s|^#!$candidate/.venv/bin/python.*$|#!$release/.venv/bin/python3|" \
            -e "2s|$candidate/.venv/bin/python3|$release/.venv/bin/python3|" {} +
    chown -R root:root "$candidate/.venv"
    chmod 0755 "$candidate/.venv"
    validate_required_release_files
    unsafe_source="$(
        find "$candidate" -xdev -path "$candidate/.venv" -prune -o \
            \( ! -user root -o -perm /022 \) -print -quit
    )"
    if [[ -n "$unsafe_source" ]]; then
        echo "release source ownership changed during dependency installation" >&2
        exit 2
    fi
    printf '%s\n' "$release_id" >"$candidate/.release-complete"
    mv -- "$candidate" "$release"
    candidate=""
fi

config_backup="$(mktemp -d "$root/.config-backup-XXXXXX")"
backup_managed_path "$installer_target" installer
backup_managed_path "$unit_dir/trpg-master.service" app-service
backup_managed_path "$unit_dir/trpg-master-backup.service" backup-service
backup_managed_path "$unit_dir/trpg-master-backup.timer" backup-timer
backup_managed_path "$unit_dir/trpg-master-context-gc.service" context-gc-service
backup_managed_path "$unit_dir/trpg-master-context-gc.timer" context-gc-timer
backup_managed_path "$unit_dir/trpg-master-monitor.service" monitor-service
backup_managed_path "$unit_dir/trpg-master-monitor.timer" monitor-timer
backup_managed_path "$nginx_available" nginx-available
backup_managed_path "$nginx_enabled" nginx-enabled

rollback_needed=1
install_managed_file "$release/deploy/install-release.sh" "$installer_target" 0755
install_managed_file "$release/deploy/trpg-master.service" \
    "$unit_dir/trpg-master.service" 0644
install_managed_file "$release/deploy/trpg-master-backup.service" \
    "$unit_dir/trpg-master-backup.service" 0644
install_managed_file "$release/deploy/trpg-master-backup.timer" \
    "$unit_dir/trpg-master-backup.timer" 0644
install_managed_file "$release/deploy/trpg-master-context-gc.service" \
    "$unit_dir/trpg-master-context-gc.service" 0644
install_managed_file "$release/deploy/trpg-master-context-gc.timer" \
    "$unit_dir/trpg-master-context-gc.timer" 0644
install_managed_file "$release/deploy/trpg-master-monitor.service" \
    "$unit_dir/trpg-master-monitor.service" 0644
install_managed_file "$release/deploy/trpg-master-monitor.timer" \
    "$unit_dir/trpg-master-monitor.timer" 0644
install_managed_file "$release/deploy/nginx-trpg-master.conf" "$nginx_available" 0644
ln -sfn "$nginx_available" "$nginx_enabled.next"
mv -Tf "$nginx_enabled.next" "$nginx_enabled"

systemctl daemon-reload
nginx -t

ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
systemctl restart "$service_name"

if ! wait_for_service_ready 30; then
    journalctl -u "$service_name" --no-pager -n 80 >&2 || true
    echo "new release did not become healthy" >&2
    exit 1
fi

systemctl reload nginx.service
systemctl enable "$service_name"
systemctl enable --now "$backup_timer"
systemctl enable --now "$context_gc_timer"
systemctl enable --now "$monitor_timer"
rollback_needed=0

# Keep the current and immediate rollback targets regardless of mtime, plus
# four additional completed releases. Directory names are validated before
# removal so temporary/quarantined directories are never cleanup targets.
kept=0
while IFS= read -r entry; do
    old_release="${entry#* }"
    old_id="${old_release##*/}"
    [[ "$old_id" =~ ^[0-9a-f]{7,64}$ ]] || continue
    [[ "$old_release" == "$release" || "$old_release" == "$previous" ]] && continue
    kept=$((kept + 1))
    if ((kept > 4)); then
        rm -rf -- "$old_release" || \
            echo "warning: could not remove old release $old_release" >&2
    fi
done < <(find "$root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr)

echo "release activated: $release_id"
