---
description: Fan out a mechanical migration across the codebase using parallel background agents in isolated worktrees
---

Run a codebase-wide mechanical migration. Arguments: $ARGUMENTS — a migration
description (e.g. "replace `_PAD` aliases with direct `PAD` imports", "swap
`datetime.utcnow()` for `datetime.now(UTC)`"). If the description ends with the
word `pr-per-chunk`, open one PR per chunk instead of one aggregate PR.

This command is for **mechanical, well-specified** changes that repeat across
many files. If the change needs per-file judgment, do it by hand instead.

1. **Discover** — from the description, derive grep patterns and build the list
   of target files (Grep/Glob). Present to the user, and STOP for confirmation:
   - your interpretation of the migration (the exact transformation),
   - the full file list,
   - the chunking plan (group by package, ~5–10 files per chunk).
   Do not proceed until the user confirms.

2. **Small case (≤3 files)** — skip fan-out entirely. Create a branch
   `feature/migrate-<slug>`, apply the change inline, verify `make test-fast`
   and `make lint`, then suggest `/ship`. Stop here.

3. **Fan out** — for each chunk N:
   - `make wt-headless NAME=migrate-<slug>-N` (isolated worktree + branch),
   - spawn the `migrator` subagent (`.claude/agents/migrator.md`) in the
     background, passing: the worktree path, the EXACT migration spec, the
     chunk's file list, and the instruction to commit in that worktree when
     `make test-fast` + `make lint` are green.
   Kick off all chunks before doing anything else; then track them to
   completion. Chunks touch disjoint files, so the `migrator` agents run
   without colliding.

4. **Aggregate (default)** — once every agent has reported:
   - create `feature/migrate-<slug>` off `main` in a fresh worktree,
   - cherry-pick / merge each chunk worktree's commit into it (disjoint files ⇒
     no conflicts expected; if one occurs, resolve by re-applying the spec by
     hand to those files),
   - run the FULL gate: `make test` + `make lint`,
   - report per-chunk status (migrated / skipped files+why / failed+why),
   - run `/ship` on the aggregate branch.
   With `pr-per-chunk`: instead run `/ship` from each chunk worktree and hand
   the resulting PRs to `/babysit-prs`.

5. **Cleanup** — after the PR(s) exist, remove the chunk worktrees
   (`make wt-rm NAME=migrate-<slug>-N`), warning the user first if any is dirty.

Failure policy: a failed chunk NEVER blocks the others — aggregate everything
that succeeded, list what failed with each agent's stated reason, and offer to
retry the failed chunks with a refined spec.
