#!/bin/bash
# Start the app in the background (macOS/BSD-compatible). Refuses if the configured ports are
# already occupied. Writes the backgrounded process group's PID(s) to state/.tmp/app.pids and
# appends app output to logs/app.log.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f scripts/app.env ]; then
  # shellcheck disable=SC1091
  source scripts/app.env
fi

mkdir -p state/.tmp logs

check_port_free() {
  local port="$1"
  local label="$2"
  if [ -n "$port" ] && lsof -ti ":$port" >/dev/null 2>&1; then
    echo "start: PORT_CONFLICT — $label port $port is already in use" >&2
    exit 3
  fi
}

check_port_free "${APP_PORT:-}" "APP_PORT"
check_port_free "${API_PORT:-}" "API_PORT"

if [ -z "${APP_START_CMD:-}" ]; then
  echo "start: APP_START_CMD not set" >&2
  exit 1
fi

echo "start: launching app" >>logs/app.log
# Job control (set -m) puts the backgrounded job in its own process group, PGID == $!,
# so stop.sh can kill the whole tree (dev servers are children of this bash -c wrapper).
set -m
nohup bash -c "$APP_START_CMD" >>logs/app.log 2>&1 &
APP_PID=$!
set +m

echo "$APP_PID" >state/.tmp/app.pids
echo "start: launched, pgid $APP_PID (logs/app.log)"
