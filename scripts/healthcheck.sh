#!/bin/bash
# Single-shot health probe. The orchestrator does the polling loop; this script just checks once.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f scripts/app.env ]; then
  # shellcheck disable=SC1091
  source scripts/app.env
fi

if [ -z "${APP_HEALTH_URL:-}" ]; then
  echo "healthcheck: APP_HEALTH_URL not set" >&2
  exit 1
fi

if [ -z "${APP_URL:-}" ]; then
  echo "healthcheck: APP_URL not set" >&2
  exit 1
fi

curl -sf "$APP_HEALTH_URL" >/dev/null && curl -sf "$APP_URL" >/dev/null
