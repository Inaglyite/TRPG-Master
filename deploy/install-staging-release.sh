#!/usr/bin/env bash
set -Eeuo pipefail

release_id="${1:?release id is required}"
archive="${2:?release archive is required}"
root=/opt/trpg-master-staging
release="$root/releases/$release_id"
previous=""

if [[ ! "$release_id" =~ ^[0-9a-f]{7,64}$ ]]; then
    echo "invalid release id" >&2
    exit 2
fi
if [[ ! -f "$archive" ]]; then
    echo "release archive not found" >&2
    exit 2
fi
if [[ -L "$root/current" ]]; then
    previous="$(readlink -f "$root/current")"
fi

install -d -o trpgdeploy -g trpgdeploy \
    "$root/releases" /var/lib/trpg-master-staging /var/log/trpg-master-staging
if [[ ! -e "$release" ]]; then
    install -d -o trpgdeploy -g trpgdeploy "$release"
    tar -xzf "$archive" -C "$release"
    chown -R trpgdeploy:trpgdeploy "$release"
    runuser -u trpgdeploy -- python3 -m venv "$release/.venv"
    runuser -u trpgdeploy -- "$release/.venv/bin/pip" \
        install --disable-pip-version-check -r "$release/requirements.txt"
fi

ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"

if ! systemctl restart trpg-master-staging.service; then
    if [[ -n "$previous" && -d "$previous" ]]; then
        ln -sfn "$previous" "$root/current.next"
        mv -Tf "$root/current.next" "$root/current"
        systemctl restart trpg-master-staging.service || true
    fi
    exit 1
fi

for _ in {1..30}; do
    if curl --fail --silent --show-error http://127.0.0.1:8766/api/health >/dev/null; then
        find "$root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
            | sort -nr | tail -n +6 | cut -d' ' -f2- | xargs -r rm -rf
        exit 0
    fi
    sleep 1
done

journalctl -u trpg-master-staging.service --no-pager -n 80 >&2
if [[ -n "$previous" && -d "$previous" ]]; then
    ln -sfn "$previous" "$root/current.next"
    mv -Tf "$root/current.next" "$root/current"
    systemctl restart trpg-master-staging.service || true
fi
exit 1
