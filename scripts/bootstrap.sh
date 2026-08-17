#!/bin/bash
# One-time (idempotent) setup: git identity, root repo, app/ repo + baseline tag, eval/ deps.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT_DIR="$(pwd)"

if [ -f scripts/app.env ]; then
  # shellcheck disable=SC1091
  source scripts/app.env
fi

echo "bootstrap: workspace root is $ROOT_DIR"

# --- root repo (repo #1: state history) ---
if [ ! -d .git ]; then
  echo "bootstrap: initializing root git repo"
  git init
  if [ -z "$(git config --local user.email 2>/dev/null || true)" ]; then
    git config --local user.email "harness@localhost"
  fi
  if [ -z "$(git config --local user.name 2>/dev/null || true)" ]; then
    git config --local user.name "harness@localhost"
  fi
  git add -A
  git commit -m "harness: initial commit" --allow-empty >/dev/null
  echo "bootstrap: root repo initialized with initial commit"
else
  echo "bootstrap: root repo already initialized, skipping"
  if [ -z "$(git config --local user.email 2>/dev/null || true)" ]; then
    git config --local user.email "harness@localhost"
  fi
  if [ -z "$(git config --local user.name 2>/dev/null || true)" ]; then
    git config --local user.name "harness@localhost"
  fi
fi

# --- app repo (repo #2: the generated application) ---
mkdir -p app
if [ ! -d app/.git ]; then
  echo "bootstrap: initializing app/ git repo"
  (
    cd app
    git init
    if [ -z "$(git config --local user.email 2>/dev/null || true)" ]; then
      git config --local user.email "harness@localhost"
    fi
    if [ -z "$(git config --local user.name 2>/dev/null || true)" ]; then
      git config --local user.name "harness@localhost"
    fi
    git commit --allow-empty -m "baseline: empty app/ tree" >/dev/null
    git tag good/BASELINE
  )
  echo "bootstrap: app/ repo initialized, tagged good/BASELINE"
else
  echo "bootstrap: app/ repo already initialized, skipping"
  if [ -z "$(git -C app config --local user.email 2>/dev/null || true)" ]; then
    git -C app config --local user.email "harness@localhost"
  fi
  if [ -z "$(git -C app config --local user.name 2>/dev/null || true)" ]; then
    git -C app config --local user.name "harness@localhost"
  fi
  if ! git -C app rev-parse good/BASELINE >/dev/null 2>&1; then
    echo "bootstrap: tag good/BASELINE missing, tagging current HEAD"
    git -C app tag good/BASELINE
  fi
fi

# --- eval/ dependencies ---
echo "bootstrap: installing eval/ dependencies"
(
  cd eval
  npm install
  npx playwright install chromium
)

echo "bootstrap: done"
