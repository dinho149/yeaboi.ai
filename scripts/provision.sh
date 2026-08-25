#!/usr/bin/env bash
# scripts/provision.sh — what a fresh worktree of this repo needs. The shared
# wt.sh runs it from the new worktree's root, after copying `.env` across.
#
# This is the seam between the shared worktree script and a toolchain: here it
# is a uv venv with the package installed editable (the same as `make install`)
# plus pre-commit; in the front-end and desktop repos it is `npm ci`.

set -euo pipefail

# Same uv fallback as the Makefile's UV := $(or ...) resolution.
UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"

echo "[provision] creating venv + installing deps (uv)…"
"$UV" venv >/dev/null
"$UV" pip install -q -e ".[dev]"

# pre-commit hooks land in the shared .git/hooks, so this is idempotent across
# worktrees. A failure here is not worth losing the worktree over.
if "$UV" run pre-commit install >/dev/null 2>&1; then
  echo "[provision] pre-commit hooks installed"
else
  echo "[provision] note: pre-commit install failed — run \`make pre-commit\` in the worktree"
fi
