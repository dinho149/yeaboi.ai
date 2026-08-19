#!/usr/bin/env bash
# scripts/wt-list.sh — list every git worktree with branch, clean/dirty status,
# how far behind origin/main it is, and path. Backs `make wt-list`. The main
# checkout is included, marked (main).
#
# The BEHIND column exists because staleness had exactly one reporter —
# scripts/wt.sh's report_behind(), which fires once, at worktree creation, and
# only on the existing-branch arms. A branch cut fresh never reported again, and
# four agent worktrees drifted 30 commits behind with nothing saying so. A stale
# branch is where the merge conflicts and the red CI come from: /ship verifies
# the tree it has, not the tree that will land.

set -euo pipefail

# Colours only when stdout is a terminal — `make wt-list | grep …` stays clean.
if [ -t 1 ]; then
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  GREEN=""; YELLOW=""; DIM=""; BOLD=""; RESET=""
fi

# Self-heal a stray `core.bare=true` left by an interrupted parallel session
# (see scripts/wt.sh) so `make wt-list` never dies with "must be run in a work tree".
git config core.bare false 2>/dev/null || true

MAIN_ROOT="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"

# Read-only, and deliberately not a fetch: `make wt-list` is a status command and
# should not reach the network. The count is against whatever origin/main this
# checkout last fetched, so it is a floor on the real drift, never an overstatement.
BASE_REF="origin/main"
git rev-parse --verify --quiet "$BASE_REF" >/dev/null || BASE_REF=""

printf "%s%-24s  %-7s  %-8s  %s%s\n" "$BOLD" "BRANCH" "STATUS" "BEHIND" "PATH" "$RESET"
printf "%s%-24s  %-7s  %-8s  %s%s\n" "$DIM" "------" "------" "------" "----" "$RESET"

# `git worktree list --porcelain` stanzas:
#   worktree <abs-path>
#   HEAD <sha>
#   branch refs/heads/<name>     (or `detached`)
git worktree list --porcelain | awk '
  /^worktree /  { wt=$2 }
  /^branch /    { print wt "\t" $2 }
  /^detached$/  { print wt "\t(detached)" }
' | while IFS=$'\t' read -r wt branch; do
  short_branch="${branch#refs/heads/}"
  [ "$wt" = "$MAIN_ROOT" ] && short_branch="$short_branch (main)"
  if [ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]; then
    status_cell="${YELLOW}dirty${RESET}  "
  else
    status_cell="${GREEN}clean${RESET}  "
  fi
  if [ -z "$BASE_REF" ] || [ "$branch" = "(detached)" ]; then
    behind_cell="${DIM}?${RESET}       "
  else
    behind="$(git -C "$wt" rev-list --count "HEAD..$BASE_REF" 2>/dev/null || echo "?")"
    if [ "$behind" = "0" ]; then
      behind_cell="${GREEN}0${RESET}       "
    else
      # Anything non-zero is worth reading; /sync-main is the fix.
      pad="$(printf "%-7s" "$behind")"
      behind_cell="${YELLOW}${pad}${RESET} "
    fi
  fi
  printf "%-24s  %s  %s  %s\n" "$short_branch" "$status_cell" "$behind_cell" "$wt"
done
