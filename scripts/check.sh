#!/bin/bash
# Install (if needed) and run the app's own lint/test commands.
# check.sh --install-only runs only the install step (used after rollback/resume).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f scripts/app.env ]; then
  # shellcheck disable=SC1091
  source scripts/app.env
fi

INSTALL_ONLY=0
if [ "${1:-}" = "--install-only" ]; then
  INSTALL_ONLY=1
fi

# If app/ has no files beyond .git/.gitkeep, there is nothing to check yet.
NON_GIT_ENTRIES="$(find app -mindepth 1 -maxdepth 1 ! -name '.git' ! -name '.gitkeep' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$NON_GIT_ENTRIES" = "0" ]; then
  echo "check: empty tree, skipping"
  exit 0
fi

if [ -z "${APP_INSTALL_CMD:-}" ]; then
  echo "check: APP_INSTALL_CMD not set, skipping install"
else
  echo "check: running install"
  bash -c "$APP_INSTALL_CMD"
fi

if [ "$INSTALL_ONLY" = "1" ]; then
  echo "check: --install-only, skipping checks"
  exit 0
fi

if [ -z "${APP_CHECK_CMD:-}" ]; then
  echo "check: APP_CHECK_CMD not set, skipping checks"
  exit 0
fi

echo "check: running checks"
bash -c "$APP_CHECK_CMD"
