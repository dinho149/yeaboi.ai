#!/usr/bin/env bash
# scripts/tooling-sync.sh — the one committed file each yeaboi repo needs in
# order to reach the shared tooling. Everything else (mk fragments, worktree
# scripts, contract vendoring) comes from the `.tooling/` clone this creates.
#
#   bash scripts/tooling-sync.sh           sync `.tooling/` to the sha in .tooling-rev
#   bash scripts/tooling-sync.sh --bump    move .tooling-rev to the tip of main, then sync
#   bash scripts/tooling-sync.sh --check   assert the checkout matches the pin (CI)
#
# `.tooling/` is gitignored, not a submodule: `git worktree add` does not
# populate submodules, and this workflow lives in worktrees. The Makefile calls
# this at parse time only when the pin and the checkout disagree, so the steady
# state costs two file reads and no network. The stamp naming what the checkout
# holds lives inside `.tooling/.git/`, where it can never show up in the
# checkout's own `git status` — anywhere else it reads as a dirty pin.
#
# THIS FILE IS COPIED, NOT INCLUDED. `make tooling-check` fails when a repo's
# copy has drifted from bootstrap/tooling-sync.sh in the tooling repo.

set -euo pipefail

DIR="${TOOLING_DIR:-.tooling}"
REPO="${TOOLING_REPO:-https://github.com/yeaboi-ai/yeaboi-tooling.git}"
PIN=".tooling-rev"
MODE="${1:-sync}"

say() { echo "[tooling] $*" >&2; }
die() { say "$*"; exit 1; }

# Never prompt: this runs at Makefile parse time, including in unattended
# fan-out, where an unknown host key would otherwise hang forever with no output.
git_offline() { GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}" git "$@"; }

[ -f "$PIN" ] || die "no $PIN in $(pwd) — run this from the repo root"
want="$(tr -d '[:space:]' <"$PIN")"
[ -n "$want" ] || die "$PIN is empty; it must hold one commit sha of $REPO"

STAMP="$DIR/.git/tooling-rev"
have=""
[ -f "$STAMP" ] && have="$(tr -d '[:space:]' <"$STAMP")"

fetch_to() {
  local rev="$1"
  if [ ! -d "$DIR/.git" ]; then
    rm -rf "$DIR"
    say "cloning $REPO -> $DIR"
    git_offline clone --quiet "$REPO" "$DIR" || die "clone failed — check the network, or set TOOLING_REPO"
  fi
  if ! git -C "$DIR" cat-file -e "$rev^{commit}" 2>/dev/null; then
    git_offline -C "$DIR" fetch --quiet --tags origin || say "fetch failed — working from what is already cloned"
  fi
  git -C "$DIR" cat-file -e "$rev^{commit}" 2>/dev/null || die "$REPO has no commit $rev (is $PIN a sha from this repo?)"
  git -C "$DIR" checkout --quiet --detach "$rev"
  printf '%s\n' "$rev" >"$STAMP"
  say "synced $DIR to ${rev:0:12}"
}

case "$MODE" in
  --bump)
    if [ ! -d "$DIR/.git" ]; then fetch_to "$want"; fi
    git_offline -C "$DIR" fetch --quiet origin main || die "cannot reach $REPO to bump"
    tip="$(git -C "$DIR" rev-parse origin/main)"
    if [ "$tip" = "$want" ]; then say "already at the tip of main (${tip:0:12})"; exit 0; fi
    printf '%s\n' "$tip" >"$PIN"
    fetch_to "$tip"
    say "bumped $PIN ${want:0:12} -> ${tip:0:12} — commit it"
    ;;
  --check)
    [ "$have" = "$want" ] || die "$DIR is at ${have:-nothing} but $PIN says ${want:0:12} — run: make tooling-sync"
    if [ -n "$(git -C "$DIR" status --porcelain)" ]; then
      die "$DIR has local modifications; it is a pinned checkout, not a place to edit. Edit $REPO and bump $PIN."
    fi
    canonical="$DIR/bootstrap/tooling-sync.sh"
    if [ -f "$canonical" ] && ! diff -q "$canonical" "${BASH_SOURCE[0]}" >/dev/null; then
      die "scripts/tooling-sync.sh has drifted from $canonical — copy the canonical one back: cp $canonical scripts/tooling-sync.sh"
    fi
    say "ok — $DIR pinned at ${want:0:12}, bootstrap matches"
    ;;
  sync)
    [ "$have" = "$want" ] && exit 0
    fetch_to "$want"
    ;;
  *)
    die "usage: tooling-sync.sh [--bump|--check]"
    ;;
esac
