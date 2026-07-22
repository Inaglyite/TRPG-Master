#!/usr/bin/env bash
set -Eeuo pipefail

backup_root=/var/backups/trpg-master
runtime_root=/var/lib/trpg-master
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup_root"
work="$(mktemp -d "$backup_root/.backup-$stamp-XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

: "${TRPG_DATABASE_URL:?TRPG_DATABASE_URL is required}"
: "${TRPG_BACKUP_PASSPHRASE_FILE:?TRPG_BACKUP_PASSPHRASE_FILE is required}"
pg_url="${TRPG_DATABASE_URL/postgresql+psycopg:/postgresql:}"
pg_url="${pg_url/postgresql+psycopg2:/postgresql:}"
pg_dump --format=custom --no-owner --no-acl "$pg_url" > "$work/database.dump"
tar --create --gzip --file "$work/runtime.tar.gz" \
    --exclude='trpg-master.db*' --directory "$runtime_root" .
sha256sum "$work/database.dump" "$work/runtime.tar.gz" > "$work/SHA256SUMS"
tar --create --file - --directory "$work" database.dump runtime.tar.gz SHA256SUMS \
    | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-file "$TRPG_BACKUP_PASSPHRASE_FILE" \
        --output "$backup_root/trpg-master-$stamp.tar.gpg"
find "$backup_root" -maxdepth 1 -type f -name 'trpg-master-*.tar.gpg' -mtime +30 -delete
