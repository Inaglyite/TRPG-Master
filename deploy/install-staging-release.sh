#!/usr/bin/env bash
set -Eeuo pipefail

release_id="${1:?release id is required}"
archive="${2:?release archive is required}"
root=/opt/trpg-master-staging
release="$root/releases/$release_id"
previous=""
candidate=""
config_backup=""
rollback_needed=0
service_was_enabled=0
timer_was_enabled=0
timer_was_active=0

service_name=trpg-master-staging.service
backup_timer=trpg-master-staging-backup.timer
unit_dir=/etc/systemd/system
nginx_available=/etc/nginx/sites-available/trpg-master-staging
nginx_enabled=/etc/nginx/sites-enabled/trpg-master-staging
installer_target=/usr/local/sbin/trpg-install-staging-release

if [[ ! "$release_id" =~ ^[0-9a-f]{7,64}$ ]]; then
    echo "invalid release id" >&2
    exit 2
fi
if [[ ! -f "$archive" ]]; then
    echo "release archive not found" >&2
    exit 2
fi

install -d -m 0755 "$root" "$root/releases"
install -d -o trpgdeploy -g trpgdeploy \
    /var/lib/trpg-master-staging /var/log/trpg-master-staging \
    /var/backups/trpg-master-staging
install -d -m 0755 "$unit_dir" /etc/nginx/sites-available /etc/nginx/sites-enabled \
    /usr/local/sbin

exec 9>"$root/.install.lock"
if ! flock -n 9; then
    echo "another staging release installation is already running" >&2
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
systemctl is-enabled --quiet "$backup_timer" >/dev/null 2>&1 && timer_was_enabled=1
systemctl is-active --quiet "$backup_timer" >/dev/null 2>&1 && timer_was_active=1

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
    echo "staging release activation failed; restoring previous configuration" >&2
    restore_managed_path "$nginx_enabled" nginx-enabled || true
    restore_managed_path "$nginx_available" nginx-available || true
    restore_managed_path "$unit_dir/trpg-master-staging-backup.timer" backup-timer || true
    restore_managed_path "$unit_dir/trpg-master-staging-backup.service" backup-service || true
    restore_managed_path "$unit_dir/trpg-master-staging.service" app-service || true
    restore_managed_path "$installer_target" installer || true

    if [[ -n "$previous" && -d "$previous" ]]; then
        ln -sfn "$previous" "$root/current.next"
        mv -Tf "$root/current.next" "$root/current"
    elif [[ -L "$root/current" ]]; then
        rm -f -- "$root/current"
    fi

    systemctl daemon-reload || true
    if [[ "$service_was_enabled" -eq 1 ]]; then
        systemctl enable "$service_name" || true
    else
        systemctl disable "$service_name" || true
    fi
    if [[ "$timer_was_enabled" -eq 1 ]]; then
        systemctl enable "$backup_timer" || true
    else
        systemctl disable "$backup_timer" || true
    fi
    if [[ "$timer_was_active" -eq 1 ]]; then
        systemctl start "$backup_timer" || true
    else
        systemctl stop "$backup_timer" || true
    fi
    if nginx -t; then
        systemctl reload nginx.service || true
    fi
    if [[ -n "$previous" && -d "$previous" ]]; then
        systemctl restart "$service_name" || true
    else
        systemctl stop "$service_name" || true
    fi
}

on_exit() {
    local status=$?
    trap - EXIT
    if [[ "$status" -ne 0 && "$rollback_needed" -eq 1 ]]; then
        rollback_release
    fi
    if [[ -n "$candidate" && -d "$candidate" ]]; then
        rm -rf -- "$candidate"
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
    echo "using completed staging release: $release_id"
elif [[ -e "$release" ]]; then
    if [[ "$previous" == "$release" ]]; then
        echo "active release is missing its completion marker; refusing same-SHA replacement" >&2
        exit 2
    fi
    quarantine="$root/releases/.incomplete-$release_id-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    echo "moving incomplete staging release aside: $quarantine" >&2
    mv -- "$release" "$quarantine"
fi

if [[ ! -d "$release" ]]; then
    candidate="$(mktemp -d "$root/releases/.install-$release_id-XXXXXX")"
    tar --extract --gzip --file "$archive" --directory "$candidate" \
        --no-same-owner --no-same-permissions

    required_files=(
        server.py
        requirements.txt
        alembic.ini
        frontend/dist/index.html
        deploy/install-staging-release.sh
        deploy/backup-trpg-master.sh
        deploy/trpg-master-staging.service
        deploy/trpg-master-staging-backup.service
        deploy/trpg-master-staging-backup.timer
        deploy/nginx-trpg-master-staging.conf
    )
    for required in "${required_files[@]}"; do
        if [[ ! -f "$candidate/$required" || -L "$candidate/$required" ]]; then
            echo "release is missing a regular file: $required" >&2
            exit 2
        fi
    done

    if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
        echo "Python 3.12 or newer is required to install a release" >&2
        exit 2
    fi

    chmod 0755 "$candidate/deploy/install-staging-release.sh" \
        "$candidate/deploy/backup-trpg-master.sh"
    chown -R trpgdeploy:trpgdeploy "$candidate"
    runuser -u trpgdeploy -- python3 -m venv "$candidate/.venv"
    runuser -u trpgdeploy -- "$candidate/.venv/bin/pip" \
        install --disable-pip-version-check -r "$candidate/requirements.txt"
    printf '%s\n' "$release_id" >"$candidate/.release-complete"
    chown -R root:root "$candidate"
    mv -- "$candidate" "$release"
    candidate=""
fi

config_backup="$(mktemp -d "$root/.config-backup-XXXXXX")"
backup_managed_path "$installer_target" installer
backup_managed_path "$unit_dir/trpg-master-staging.service" app-service
backup_managed_path "$unit_dir/trpg-master-staging-backup.service" backup-service
backup_managed_path "$unit_dir/trpg-master-staging-backup.timer" backup-timer
backup_managed_path "$nginx_available" nginx-available
backup_managed_path "$nginx_enabled" nginx-enabled

rollback_needed=1
install_managed_file "$release/deploy/install-staging-release.sh" \
    "$installer_target" 0755
install_managed_file "$release/deploy/trpg-master-staging.service" \
    "$unit_dir/trpg-master-staging.service" 0644
install_managed_file "$release/deploy/trpg-master-staging-backup.service" \
    "$unit_dir/trpg-master-staging-backup.service" 0644
install_managed_file "$release/deploy/trpg-master-staging-backup.timer" \
    "$unit_dir/trpg-master-staging-backup.timer" 0644
install_managed_file "$release/deploy/nginx-trpg-master-staging.conf" \
    "$nginx_available" 0644
ln -sfn "$nginx_available" "$nginx_enabled.next"
mv -Tf "$nginx_enabled.next" "$nginx_enabled"

systemctl daemon-reload
nginx -t

ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
systemctl restart "$service_name"

healthy=0
for _ in {1..30}; do
    if curl --fail --silent --show-error --max-time 2 \
        http://127.0.0.1:8766/api/ready >/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done
if [[ "$healthy" -ne 1 ]]; then
    journalctl -u "$service_name" --no-pager -n 80 >&2 || true
    echo "new staging release did not become healthy" >&2
    exit 1
fi

systemctl reload nginx.service
systemctl enable "$service_name"
systemctl enable --now "$backup_timer"
rollback_needed=0

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

echo "staging release activated: $release_id"
