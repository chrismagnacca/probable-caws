#!/bin/bash
# Idempotent stop: kill PIDs recorded by start.sh, falling back to killing listeners on the
# configured ports (only if they match a recorded PID). Always exits 0.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f scripts/app.env ]; then
  # shellcheck disable=SC1091
  source scripts/app.env
fi

PIDFILE="state/.tmp/app.pids"

# start.sh records the process-group leader's PID (PGID); kill the whole group so the
# dev servers spawned under the bash -c wrapper die with it.
kill_group() {
  local pid="$1"
  if [ -z "$pid" ]; then
    return 0
  fi
  if kill -0 -- "-$pid" >/dev/null 2>&1; then
    kill -TERM -- "-$pid" >/dev/null 2>&1 || true
  elif kill -0 "$pid" >/dev/null 2>&1; then
    kill -TERM "$pid" >/dev/null 2>&1 || true
  else
    return 0
  fi
  local waited=0
  while kill -0 "$pid" >/dev/null 2>&1 || kill -0 -- "-$pid" >/dev/null 2>&1; do
    if [ "$waited" -ge 5 ]; then
      kill -KILL -- "-$pid" >/dev/null 2>&1 || true
      kill -KILL "$pid" >/dev/null 2>&1 || true
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

RECORDED_PIDS=""
if [ -f "$PIDFILE" ]; then
  RECORDED_PIDS="$(cat "$PIDFILE" 2>/dev/null || true)"
  while IFS= read -r pid; do
    [ -n "$pid" ] && kill_group "$pid"
  done <<EOF
$RECORDED_PIDS
EOF
  rm -f "$PIDFILE"
fi

# Fallback for stale pidfiles: kill listeners on APP_PORT/API_PORT only if they belong to a
# process group we recorded — never an unrelated occupant of the port.
for port in "${APP_PORT:-}" "${API_PORT:-}"; do
  [ -z "$port" ] && continue
  for pid in $(lsof -ti ":$port" 2>/dev/null || true); do
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$pgid" ] && printf '%s\n' "$RECORDED_PIDS" | grep -qx "$pgid" 2>/dev/null; then
      kill_group "$pgid"
    fi
  done
done

echo "stop: done"
exit 0
