#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-}" -ne 0 ]]; then
    echo "must be run as root" >&2
    exit 2
fi
if (( $# != 2 )); then
    echo "usage: install-release-activation-entrypoint.sh <source-dir> <upload-user>" >&2
    exit 2
fi

source_dir="$1"
upload_user="$2"
if [[ ! "$upload_user" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]]; then
    echo "invalid upload user" >&2
    exit 2
fi
if ! getent passwd "$upload_user" >/dev/null; then
    echo "upload user does not exist: $upload_user" >&2
    exit 2
fi
if [[ ! -f "$source_dir/trpg-activate-release" || -L "$source_dir/trpg-activate-release" ]]; then
    echo "fixed activation entrypoint is missing or is a symlink" >&2
    exit 2
fi

spool_root=/var/lib/trpg-master-release
incoming_dir="$spool_root/incoming"
entrypoint=/usr/local/sbin/trpg-activate-release
sudoers_file=/etc/sudoers.d/trpg-master-release
sudoers_tmp="${sudoers_file}.tmp.$$"
entrypoint_tmp="${entrypoint}.tmp.$$"
cleanup() {
    rm -f -- "$sudoers_tmp" "$entrypoint_tmp"
}
trap cleanup EXIT

install -d -o root -g root -m 0755 "$spool_root"
if [[ -L "$incoming_dir" || -e "$incoming_dir" && ! -d "$incoming_dir" ]]; then
    echo "incoming path is not a directory: $incoming_dir" >&2
    exit 2
fi
install -d -o "$upload_user" -g "$upload_user" -m 0700 "$incoming_dir"
chown "$upload_user:$upload_user" "$incoming_dir"
chmod 0700 "$incoming_dir"

install -o root -g root -m 0755 "$source_dir/trpg-activate-release" "$entrypoint_tmp"
mv -Tf -- "$entrypoint_tmp" "$entrypoint"

printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/trpg-activate-release\n' \
    "$upload_user" >"$sudoers_tmp"
chown root:root "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
mv -Tf -- "$sudoers_tmp" "$sudoers_file"
chown root:root "$sudoers_file"
chmod 0440 "$sudoers_file"

echo "installed root-owned release activation entrypoint for $upload_user"
