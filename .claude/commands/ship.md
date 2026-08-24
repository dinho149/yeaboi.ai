---
description: Verify (independent review + full tests), commit, push, and open a PR for the current branch
---

Ship the current feature branch. Arguments (optional): $ARGUMENTS — may include `auto-merge` to enable auto-merge for low-risk changes (docs/chores/small fixes only).

The contract this branch must satisfy: tests for every change, lint clean, security scan clean, surface parity (a capability lands on every surface or records a reasoned exemption), observability (logging per CLAUDE.md), and fresh web bundles when `frontend/` changed. The steps below are how it is executed interactively.

**Two things about the order, because both were bugs.** The branch is **committed and rebased onto
`origin/main` before anything is verified**: a gate run on a stale base proves something about a tree
that will never exist, and `/ship` used to never fetch at all. And the **review runs in the background
alongside the test gate**, not in front of it — they are independent, and running them in series put
the reviewer's whole wall clock on the critical path for no benefit.

Follow these steps in order. If any step fails, stop, report what failed, and fix it before continuing. Never skip the verification steps.

---

1. **Sanity.** Run `git branch --show-current`. If on `main`, stop: create a
   feature branch first. Then `git fetch origin` (you need it in step 3 and it costs nothing here).

2. **Commit.** Stage the relevant changes and commit with a lowercase imperative
   message (e.g. "add streaming output"), ending with the Co-Authored-By trailer from CLAUDE.md's Git
   Conventions.

   Commit **with `SKIP=unit-tests`**: pre-commit's `unit-tests` hook is `make test-scoped`, which the
   Stop hook already ran at the end of the last turn and which step 5's gate is about to run in full.
   Three runs of the same tests is how a gate becomes one people pass with `--no-verify`. gitleaks,
   ruff and `ruff-format` still run — those are the hooks that catch something the gate does not.

   Committing here, rather than after the review, is what makes steps 3 and 4 possible at all: you
   cannot rebase a dirty tree, and a diff taken before the commit does not contain the work.

3. **Rebase onto `origin/main`.** `git rebase origin/main`. Conflicts are resolved with the playbook
   in `/sync-main` — read it rather than improvising, because for every generated file in this repo
   ("take the other side") is the wrong answer and produces a tree that merges green and reds CI.

   If the branch was pushed before, the later push needs `--force-with-lease`.

4. **Independent verification (fresh context, no author bias) — IN THE BACKGROUND.** Spawn the
   `code-reviewer` subagent (`.claude/agents/code-reviewer.md`).
   Give it ONLY: (a) the output of **`git diff origin/main...HEAD`**, (b) a one-paragraph description
   of what this branch was supposed to do — NOT this conversation's history. Its checklist (spec fit,
   skill-based conventions, correctness) lives in the agent definition.

   **`origin/main`, never the local `main` ref.** Local `main` in a worktree is routinely several
   commits behind, and a three-dot diff taken against it hands the reviewer other people's
   already-merged PRs and none of this branch's work. That has happened, silently, and a review of the
   wrong diff reports clean. `tests/unit/test_ship_gate.py` fails if any command or agent file names
   the local ref again.

   Do not wait for it here. Move straight to step 5 and collect its findings in step 6.

5. **Full test gate — in the foreground, while step 4 runs.** One command:

   ```
   make ship-gate
   ```

   That is `lint` → `format-check` → `test` (parallel unit lane + serial integration lane) → `security`
   → `preflight`, in fail-fast order, as a single `make` invocation so `lint` resolves once for itself
   and for `security`.

   `preflight` is the new half. `make test` proves the Python suite and nothing else; CI checks
   further things, and `scripts/preflight.py` runs the ones this branch's diff needs — front-end
   bundles, docs site, golden evaluators, the wheel's contents,
   actionlint — decided by `scripts/test_scope.py`, and printing every job it skipped and why. If it
   adds a capability, confirm its `CAPABILITIES` row and `FeatureTip` exist (`make test` fails without
   them).

6. **Resolve the review.** Collect the `code-reviewer` findings. Fix every finding at `blocker` or
   `should-fix` severity, or explain in the PR body why it is intentionally not addressed.

   Re-verify the fixes with `make test-scoped` + `make lint` — **not** the whole gate again. The gate
   in step 5 already covered the branch; this is checking a small delta on top of it.

7. **Last mile, then push + PR.** `main` moves during a ship. Before pushing:

   ```
   git fetch origin && git rev-list --count HEAD..origin/main
   ```

   If that is not `0`, rebase again (step 3's playbook) and re-run `make test-scoped`. Then
   `git push -u origin <branch>` and `gh pr create` against `main` with:
   - Title: same style as the commit message.
   - Body: a Summary section (what and why), a Test plan section (what was run), and the standard
     "🤖 Generated with Claude Code" footer.

   **The branch is stale again about a minute later, and that is by design.** `auto-version.yml` pushes
   a `chore: bump version … [auto]` commit onto the PR *branch*, touching `pyproject.toml` and
   `src/yeaboi/changelog_data.json` — and *not* `uv.lock`, which then disagrees with both. So any later
   push from this worktree must `git pull --rebase` first, and must **never** force-push over that
   commit.

8. **Auto-merge (only if `auto-merge` was passed)** — three conditions, all of them:
   - the change is genuinely low-risk (docs, chore, small fix; no `src/yeaboi/agent/`, schema, or
     workflow changes), and
   - `gh pr view --json mergeStateStatus` is not `BEHIND`, and
   - CI has not been superseded by a newer `origin/main` since step 7.

   Then `gh pr merge --auto --squash`. If any condition fails, say so and skip this step.

   The middle condition is not ceremony: the `main-branch` ruleset does **not** require a branch to be
   up to date before merging, so `--auto` on a branch that has fallen behind merges a tree CI never
   built.

9. **Hand off the review loop** — say plainly that the PR is **not done yet**, and why:
   `claude-review.yml` fires on `workflow_run` *after* CI succeeds, which is minutes from now, so at
   this moment its review does not exist. The `code-reviewer` pass in step 4 is not it — that one had
   no CI results, no diff-on-`main` context, and nobody else's eyes.

   Name the follow-up: `/pr-feedback <n>` once CI is green, or `/babysit-prs` across every open PR. Do
   not wait for it here; a `/ship` that blocks for ten minutes gets run less often.

   **On a branch you are shipping by hand, that review is advisory and the `pr-feedback` status stays
   green.** It runs once, it posts what it found, and it does not hold the merge — you are the person
   it would otherwise be arguing with. Read it anyway; that is the whole point of it existing. The gate
   enforces on the unattended lane (`cowork/…`, `feature/issue-…`, triage and sentinel branches, or
   anything labelled `cowork`), where nobody is on the other end. **A human reviewer's unresolved
   thread, or a `Request changes` review, still holds the check here** — that one has somebody waiting
   by construction.

   So step 8 does need the judgement it asks for: `gh pr merge --auto` waits on the required checks, and
   on a hand-shipped branch `pr-feedback` will be green whatever the review says. An auto-merge can
   outrun the review here — which is why step 8 is limited to changes that are genuinely low-risk.

10. **Report** — output the PR URL and a one-line status, ending with what is still outstanding: the
    pending review. If step 3 or step 7 rebased, say how far behind the branch had
    fallen and what conflicted.

---

Review feedback is not answered here, for the plain reason that it does not exist yet — `/ship` opens the PR, and answering what comes back is a separate sitting (`/pr-feedback`).
