#!/usr/bin/env bash
# scripts/wt-issue.sh <issue-number> [open|headless] — worktree from a GitHub issue.
#
# Resolves the branch belonging to an ongoing GitHub issue and delegates to
# scripts/wt.sh, which (since the remote-branch case landed there) checks the
# branch out tracking origin/<branch>. Resolution order:
#
#   1. Branches linked in the issue's Development section (`gh issue develop --list`)
#   2. The head branch of an open same-repo PR that closes the issue (GraphQL
#      closedByPullRequestsReferences — the reliable link, unlike grepping titles)
#
# Exactly one match is required; several are printed for the caller to pick from
# with `make wt-new NAME=<branch>`. No interactive prompts — like wt.sh, this is
# an entry point for unattended fan-out, where a prompt hangs the orchestrating
# agent forever.
#
# Backs `make wt-issue ISSUE=<number> [HEADLESS=1]`.

set -euo pipefail

NUM="${1:?usage: wt-issue.sh <issue-number> [open|headless]}"
ACTION="${2:-open}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$NUM" in
  *[!0-9]*|'') echo "[wt-issue] '$NUM' is not an issue number" >&2; exit 1 ;;
esac
# Only the two provisioning actions may pass through — this script resolves a
# branch to CREATE a worktree; forwarding e.g. `rm` would delete one instead.
case "$ACTION" in
  open|headless) ;;
  *) echo "[wt-issue] action must be 'open' or 'headless' (got '$ACTION')" >&2; exit 1 ;;
esac

# gh and git resolve the repository from the cwd; run from the script's own
# checkout so a caller outside any repo gets the right answer, not a silent miss.
cd "$SCRIPT_DIR"

command -v gh >/dev/null 2>&1 || {
  echo "[wt-issue] 'gh' CLI not found — install it (brew install gh) and run \`gh auth login\`" >&2
  exit 1
}
gh auth status >/dev/null 2>&1 || {
  echo "[wt-issue] 'gh' is not authenticated — run \`gh auth login\`" >&2
  exit 1
}

NWO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

# --- 1. linked branches (Development section) --------------------------------
# Output is "branch<TAB>url" lines; keep only the branch column.
# `|| true`: gh exits non-zero on an unknown issue number, a token without repo
# scope, or a network blip — under `set -e` that would abort here with no
# message at all, never reaching the PR fallback or the guidance below.
CANDIDATES="$(gh issue develop --list "$NUM" 2>/dev/null | cut -f1 | sed '/^$/d' | sort -u || true)"
SOURCE="linked branches"

# --- 2. fallback: head branch of an open same-repo PR that closes the issue --
if [ -z "$CANDIDATES" ]; then
  # select(.isCrossRepository | not): a fork PR's head branch does not exist on
  # origin, so resolving it would send wt.sh down its cut-new-from-main arm.
  CANDIDATES="$(gh api graphql \
    -f query='query($owner:String!,$repo:String!,$num:Int!){
      repository(owner:$owner,name:$repo){ issue(number:$num){
        closedByPullRequestsReferences(first:10, includeClosedPrs:false){
          nodes{ headRefName isCrossRepository } } } } }' \
    -f owner="${NWO%/*}" -f repo="${NWO#*/}" -F num="$NUM" \
    --jq '.data.repository.issue.closedByPullRequestsReferences.nodes[]
          | select(.isCrossRepository | not) | .headRefName' \
    2>/dev/null | sed '/^$/d' | sort -u || true)"
  SOURCE="open PRs closing it"
fi

if [ -z "$CANDIDATES" ]; then
  echo "[wt-issue] issue #$NUM has no linked branch and no open same-repo PR that closes it." >&2
  echo "[wt-issue] create a linked branch with \`gh issue develop $NUM\`, or use \`make wt-new NAME=<slug>\`." >&2
  exit 1
fi

COUNT="$(printf '%s\n' "$CANDIDATES" | wc -l | tr -d ' ')"
if [ "$COUNT" != "1" ]; then
  echo "[wt-issue] issue #$NUM has $COUNT candidate branches ($SOURCE) — pick one with \`make wt-new NAME=<branch>\`:" >&2
  printf '%s\n' "$CANDIDATES" | sed 's/^/[wt-issue]   /' >&2
  exit 1
fi

BRANCH="$CANDIDATES"

# The branch must actually exist on origin: a linked branch created in another
# repository (`gh issue develop --branch-repo`) resolves to a name wt.sh would
# silently re-cut from origin/main — the exact failure mode this tooling removes.
if ! git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  echo "[wt-issue] branch '$BRANCH' ($SOURCE of issue #$NUM) does not exist on origin — created in a fork?" >&2
  exit 1
fi

TITLE="$(gh issue view "$NUM" --json title --jq .title 2>/dev/null || true)"
echo "[wt-issue] issue #$NUM${TITLE:+ ($TITLE)} → branch '$BRANCH'"
exec bash "$SCRIPT_DIR/wt.sh" "$BRANCH" "$ACTION"
