#!/usr/bin/env bash
# Start and verify the Raspberry Pi staging stack from a development machine.
# Override TRPG_PI_SSH_TARGET if its LAN address or SSH user changes.
set -Eeuo pipefail

if (( $# != 0 )); then
    echo "usage: $(basename "$0")" >&2
    exit 2
fi

readonly pi_target="${TRPG_PI_SSH_TARGET:-inaglyite@192.168.5.22}"

exec ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=15 \
    "$pi_target" \
    'sudo -n /usr/local/sbin/trpg-start-staging'
