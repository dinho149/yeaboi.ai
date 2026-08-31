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

# --- hooks: one per worktree, not one per .git -------------------------------
# Every worktree shares a common gitdir, so `pre-commit install` wrote ONE
# .git/hooks/pre-commit with INSTALL_PYTHON pinned to whichever worktree ran it
# last, and deleting that worktree broke `git commit` in all the others. The
# comment that used to sit here claimed it was idempotent; it was not.
#
# core.hooksPath is relative and git chdirs to the working-tree root before
# running a hook, so this one shared setting gives every worktree its OWN
# .githooks/ — including the main checkout and every worktree that already
# exists, the moment any one of them runs this.
git config core.hooksPath .githooks

# The poisoned shared hook is inert once hooksPath is set, but a file naming a
# venv that may not exist is a trap for whoever reads .git/hooks next.
"$UV" run pre-commit uninstall >/dev/null 2>&1 || true
echo "[provision] hooks: .githooks/ in this worktree"
