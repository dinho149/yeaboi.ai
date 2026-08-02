#!/usr/bin/env bash
# scripts/wt.sh <name> [open|headless|rm] — git-worktree lifecycle for parallel Claude sessions.
#
#   bash scripts/wt.sh my-feature          -> create .claude/worktrees/my-feature + provision
#   bash scripts/wt.sh my-feature open     -> create (if needed) + open a new VS Code window
#                                             (claude auto-starts in the integrated terminal)
#   bash scripts/wt.sh my-feature headless -> create + provision WITHOUT VS Code auto-launch;
#                                             for worktrees driven by background agents from
#                                             an orchestrating Claude session
#   bash scripts/wt.sh my-feature rm       -> remove worktree dir + git branch
#
# A new branch is cut from the freshly fetched upstream default branch
# (origin/main) rather than from whatever the main checkout happens to be sitting
# on — otherwise a stale main silently hands every new feature branch an old
# base, and the first `/sync-main` turns into a surprise rebase. Each fallback
# (no origin, failed fetch, unresolvable default branch, pre-existing branch)
# prints why it took a local base instead.
#
# Provisioning per worktree: copy the main checkout's .env, create a uv venv with
# the package installed editable (same as `make install`), install pre-commit
# hooks, and (except for headless) write .vscode/ auto-launch files so opening
# the folder starts a claude session.
#
# Backs `make wt-new` / `make wt-open` / `make wt-headless` / `make wt-rm`.
# Editor CLI comes from $CODE (default: code) — e.g. `CODE=cursor make wt-open NAME=my-feature`.

set -euo pipefail

NAME="${1:?usage: wt.sh <name> [open|headless|rm]}"
ACTION="${2:-create}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Self-heal: a stray `core.bare=true` (left by an interrupted "clean checkout of
# main" from a parallel session) makes every work-tree git command fail with
# "this operation must be run in a work tree". This repo is never legitimately
# bare, so force it off. `git config` writes the config file directly and works
# even while the flag is set, so this must run BEFORE any rev-parse below.
git -C "$SCRIPT_DIR" config core.bare false 2>/dev/null || true

ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
# Always operate against the MAIN checkout, even when invoked from inside a
# worktree (the main worktree is the first `git worktree list` entry).
ROOT="$(git -C "$ROOT" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
TARGET="$ROOT/.claude/worktrees/$NAME"

# Same uv fallback as the Makefile's UV := $(or ...) resolution.
UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"

if [ "$ACTION" = "rm" ]; then
  git -C "$ROOT" worktree remove --force "$TARGET" 2>/dev/null || true
  rm -rf "$TARGET"
  git -C "$ROOT" worktree prune
  git -C "$ROOT" branch -D "$NAME" 2>/dev/null || true
  echo "[wt] removed worktree '$NAME' (dir + branch)"
  exit 0
fi

# --- base: latest upstream default branch ------------------------------------
# Resolved before `worktree add` so the new branch starts at origin/main rather
# than at the main checkout's HEAD, which may be stale or on another branch.
# Left empty when there is no usable remote ref; the add then falls back to HEAD.
BASE_REF=""
DEFAULT_BRANCH="main"

# Never prompt: wt.sh is the entry point for unattended fan-out (/migrate,
# /babysit-prs), where an unknown host key or a locked ssh key would otherwise
# hang the orchestrating agent forever with no output. Both settings turn a
# would-be prompt into a plain non-zero exit, which the callers below handle.
git_offline() { GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}" git "$@"; }

resolve_base() {
  git -C "$ROOT" remote get-url origin >/dev/null 2>&1 || {
    echo "[wt] note: no 'origin' remote — branching from the main checkout's HEAD"
    return
  }
  echo "[wt] fetching origin…"
  if ! git_offline -C "$ROOT" fetch --quiet --prune origin; then
    echo "[wt] note: \`git fetch origin\` failed (offline? credentials?) — branching from the local base, which may be stale"
  fi
  # origin/HEAD names the default branch. It is unset on clones built with
  # `git init` + `git remote add` and after a default-branch rename, so ask the
  # remote rather than assuming 'main' — guessing wrong lands us back on the
  # stale-HEAD behaviour this function exists to remove.
  local head_ref
  head_ref="$(git -C "$ROOT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [ -z "$head_ref" ]; then
    git_offline -C "$ROOT" remote set-head origin --auto >/dev/null 2>&1 || true
    head_ref="$(git -C "$ROOT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  fi
  [ -n "$head_ref" ] && DEFAULT_BRANCH="${head_ref#origin/}" || true
  if git -C "$ROOT" show-ref --verify --quiet "refs/remotes/origin/$DEFAULT_BRANCH"; then
    BASE_REF="origin/$DEFAULT_BRANCH"
  else
    echo "[wt] note: no 'origin/$DEFAULT_BRANCH' ref — branching from the main checkout's HEAD, which may be stale"
  fi
}

# Keep the main checkout's own default branch in step, so `git log main` there
# and the base of the new worktree agree. Fast-forward only, and never touching a
# dirty tree — this is a convenience, not something that may eat local work.
sync_local_default() {
  [ -n "$BASE_REF" ] || return 0
  local before after err
  if [ "$(git -C "$ROOT" branch --show-current)" = "$DEFAULT_BRANCH" ]; then
    # -uno: untracked files never block a fast-forward, and the main checkout
    # always has some (log output, build artefacts). Counting them would make
    # this branch dead code.
    if [ -n "$(git -C "$ROOT" status --porcelain -uno)" ]; then
      echo "[wt] note: main checkout has uncommitted changes — left '$DEFAULT_BRANCH' alone (the new worktree still starts at $BASE_REF)"
      return 0
    fi
    before="$(git -C "$ROOT" rev-parse HEAD)"
    if ! err="$(git -C "$ROOT" merge --ff-only "$BASE_REF" 2>&1)"; then
      # Report git's own reason: 'diverged' is only one of them (a held
      # index.lock and an unmerged index land here too).
      echo "[wt] note: could not fast-forward '$DEFAULT_BRANCH' in the main checkout — left alone"
      echo "[wt]       git said: $(printf '%s' "$err" | head -1)"
      return 0
    fi
    after="$(git -C "$ROOT" rev-parse HEAD)"
    [ "$before" != "$after" ] && echo "[wt] fast-forwarded '$DEFAULT_BRANCH' in the main checkout" || true
  elif git -C "$ROOT" worktree list --porcelain | grep -qFx "branch refs/heads/$DEFAULT_BRANCH"; then
    echo "[wt] note: '$DEFAULT_BRANCH' is checked out in another worktree — left alone"
  else
    # Not checked out anywhere: fetch refuses a non-fast-forward ref update, so
    # this is safe without an explicit ancestry check — but say so when it is
    # refused, rather than leaving a diverged local default silently behind.
    before="$(git -C "$ROOT" rev-parse "$DEFAULT_BRANCH" 2>/dev/null || echo none)"
    if git -C "$ROOT" fetch --quiet origin "$DEFAULT_BRANCH:$DEFAULT_BRANCH" 2>/dev/null; then
      after="$(git -C "$ROOT" rev-parse "$DEFAULT_BRANCH" 2>/dev/null || echo none)"
      [ "$before" != "$after" ] && echo "[wt] fast-forwarded local '$DEFAULT_BRANCH'" || true
    else
      echo "[wt] note: local '$DEFAULT_BRANCH' has diverged from $BASE_REF — left alone"
    fi
  fi
}

if [ ! -d "$TARGET" ]; then
  mkdir -p "$ROOT/.claude/worktrees"
  resolve_base
  sync_local_default
  # New branch by default; reuse the branch if it already exists.
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$NAME"; then
    git -C "$ROOT" worktree add "$TARGET" "$NAME"
    # An existing branch keeps its own history — rebasing it here could conflict
    # or rewrite pushed commits, so only report the gap and let /sync-main do it.
    if [ -n "$BASE_REF" ]; then
      behind="$(git -C "$ROOT" rev-list --count "$NAME..$BASE_REF" 2>/dev/null || echo 0)"
      if [ "$behind" != "0" ]; then
        echo "[wt] note: existing branch '$NAME' is $behind commit(s) behind $BASE_REF — run /sync-main in the worktree"
      fi
    fi
  elif [ -n "$BASE_REF" ]; then
    # --no-track: branching off a remote-tracking ref would otherwise set the new
    # branch's upstream to origin/main, so a bare `git push` in the worktree aims
    # at main. /ship pushes with `-u origin <branch>` and sets it properly.
    git -C "$ROOT" worktree add --no-track "$TARGET" -b "$NAME" "$BASE_REF"
    echo "[wt] branched '$NAME' from $BASE_REF"
  else
    git -C "$ROOT" worktree add "$TARGET" -b "$NAME"
  fi

  # --- .env: carry API keys over from the main checkout ------------------------
  if [ -f "$ROOT/.env" ]; then
    cp "$ROOT/.env" "$TARGET/.env"
    echo "[wt] copied .env from main checkout"
  else
    echo "[wt] note: no $ROOT/.env — run \`make env\` in the main checkout, then re-create this worktree"
  fi

  # --- venv: editable install so make test / make run work immediately --------
  echo "[wt] creating venv + installing deps (uv)…"
  (cd "$TARGET" && "$UV" venv >/dev/null && "$UV" pip install -q -e ".[dev]")

  # --- pre-commit: ruff/gitleaks/unit-test hooks guaranteed in every worktree --
  # (hooks land in the shared .git/hooks, so this is idempotent across worktrees)
  if (cd "$TARGET" && "$UV" run pre-commit install >/dev/null 2>&1); then
    echo "[wt] pre-commit hooks installed"
  else
    echo "[wt] note: pre-commit install failed — run \`make pre-commit\` in the worktree"
  fi

  # --- .vscode/: auto-launch claude in the integrated terminal on folder open --
  # `runOn: folderOpen` + workspace-scoped `task.allowAutomaticTasks: on` skips
  # VS Code's "allow automatic tasks?" prompt. The Workspace Trust prompt is
  # unavoidable on first open of any folder; trust once and it sticks.
  # Skipped for headless worktrees — those are driven by background agents,
  # not a human-attended editor window.
  if [ "$ACTION" != "headless" ]; then
  mkdir -p "$TARGET/.vscode"
  cat > "$TARGET/.vscode/settings.json" <<'EOF'
{
  "task.allowAutomaticTasks": "on"
}
EOF
  # Add --dangerously-skip-permissions to the command for unattended fan-out runs.
  cat > "$TARGET/.vscode/tasks.json" <<'EOF'
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "claude",
      "type": "shell",
      "command": "claude",
      "presentation": {
        "reveal": "always",
        "panel": "new",
        "focus": true,
        "clear": true,
        "showReuseMessage": false
      },
      "runOptions": { "runOn": "folderOpen" },
      "problemMatcher": []
    }
  ]
}
EOF
  fi
fi

echo "[wt] worktree ready: $TARGET"
if [ "$ACTION" = "headless" ]; then
  echo "[wt] headless — no VS Code auto-launch; drive it with a background agent from your orchestrating session"
fi

if [ "$ACTION" = "open" ]; then
  CODE="${CODE:-code}"
  if ! command -v "$CODE" >/dev/null 2>&1; then
    echo "[wt] '$CODE' CLI not found on PATH." >&2
    echo "     In VS Code: Cmd-Shift-P → \"Shell Command: Install 'code' command in PATH\"" >&2
    echo "     Or override the editor: CODE=cursor make wt-open NAME=$NAME" >&2
    exit 1
  fi
  "$CODE" -n "$TARGET"
  echo "[wt] opened $NAME in $CODE; claude auto-starts in the integrated terminal"
fi
