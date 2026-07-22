#!/usr/bin/env bash
set -Eeuo pipefail

backup_root="${TRPG_BACKUP_ROOT:-/var/backups/trpg-master}"
runtime_root="${TRPG_BACKUP_RUNTIME_ROOT:-/var/lib/trpg-master}"
backup_prefix="${TRPG_BACKUP_PREFIX:-trpg-master}"
retention_days="${TRPG_BACKUP_RETENTION_DAYS:-30}"

case "$backup_root" in
    /var/backups/trpg-master|/var/backups/trpg-master-*) ;;
    *) echo "unsafe backup root: $backup_root" >&2; exit 2 ;;
esac
case "$runtime_root" in
    /var/lib/trpg-master|/var/lib/trpg-master-*) ;;
    *) echo "unsafe runtime root: $runtime_root" >&2; exit 2 ;;
esac
if [[ ! "$backup_prefix" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    echo "invalid backup prefix" >&2
    exit 2
fi
if [[ ! "$retention_days" =~ ^[1-9][0-9]{0,3}$ ]]; then
    echo "invalid backup retention" >&2
    exit 2
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup_root"
work="$(mktemp -d "$backup_root/.backup-$stamp-XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
export GNUPGHOME="${GNUPGHOME:-$work/gnupg}"
install -d -m 0700 "$GNUPGHOME"

: "${TRPG_DATABASE_URL:?TRPG_DATABASE_URL is required}"
: "${TRPG_BACKUP_PASSPHRASE_FILE:?TRPG_BACKUP_PASSPHRASE_FILE is required}"
pg_url="${TRPG_DATABASE_URL/postgresql+psycopg:/postgresql:}"
pg_url="${pg_url/postgresql+psycopg2:/postgresql:}"
pg_dump --format=custom --no-owner --no-acl "$pg_url" > "$work/database.dump"
tar --create --gzip --file "$work/runtime.tar.gz" \
    --exclude='trpg-master.db*' --directory "$runtime_root" .
sha256sum "$work/database.dump" "$work/runtime.tar.gz" > "$work/SHA256SUMS"
tar --create --file - --directory "$work" database.dump runtime.tar.gz SHA256SUMS \
    | gpg --batch --yes --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-file "$TRPG_BACKUP_PASSPHRASE_FILE" \
        --output "$backup_root/$backup_prefix-$stamp.tar.gpg"
find "$backup_root" -maxdepth 1 -type f -name "$backup_prefix-*.tar.gpg" \
    -mtime "+$retention_days" -delete
