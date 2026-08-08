#!/bin/sh

set -eu

unit_name=blockclock-adapter.service
unit_path=/etc/systemd/system/$unit_name
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer with sudo: sudo $0" >&2
    exit 1
fi

if [ ! -f "$project_dir/.env" ]; then
    echo "Refusing to install without $project_dir/.env" >&2
    exit 1
fi

docker_bin=$(command -v docker || true)
if [ -z "$docker_bin" ]; then
    echo "Docker is not installed or is not in PATH" >&2
    exit 1
fi

systemctl_bin=$(command -v systemctl || true)
if [ -z "$systemctl_bin" ]; then
    echo "systemctl is not installed or is not in PATH" >&2
    exit 1
fi

# systemd expands percent specifiers and uses backslash and double quote while
# parsing quoted paths. Refuse unusual paths rather than generating an unsafe
# or ambiguous unit file.
case "$project_dir$docker_bin" in
    *%*|*\\*|*\"*)
        echo "Cannot install from a path containing %, backslash, or double quote" >&2
        exit 1
        ;;
esac

temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT HUP INT TERM

cat >"$temporary" <<EOF
[Unit]
Description=Umbrel BLOCKCLOCK adapter
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
ConditionPathExists="$project_dir/.env"

[Service]
Type=oneshot
WorkingDirectory="$project_dir"
ExecStart="$docker_bin" compose up -d --remove-orphans
ExecStop="$docker_bin" compose stop
RemainAfterExit=yes
Restart=on-failure
RestartSec=10
TimeoutStartSec=120
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
EOF

install -m 0644 "$temporary" "$unit_path"
"$systemctl_bin" daemon-reload
"$systemctl_bin" enable --now "$unit_name"

echo "Installed and started $unit_name"
"$systemctl_bin" --no-pager --full status "$unit_name"
