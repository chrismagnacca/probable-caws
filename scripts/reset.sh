#!/bin/bash
# Runs APP_RESET_CMD if set (e.g. to reseed a local dev database between evaluator runs).
# Called by the orchestrator before every evaluator session. No-op if unset.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f scripts/app.env ]; then
  # shellcheck disable=SC1091
  source scripts/app.env
fi

if [ -z "${APP_RESET_CMD:-}" ]; then
  echo "reset: APP_RESET_CMD not set, nothing to do"
  exit 0
fi

echo "reset: running APP_RESET_CMD"
bash -c "$APP_RESET_CMD"
