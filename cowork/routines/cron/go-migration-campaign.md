# go migration campaign

**Trigger** — cron `40 7 * * 1-5` (weekdays 07:40 UTC)
**Summary** — advances the current Go-migration wave by one phase, or opens the next wave
**Workstream** — [`workstreams/go-migration.md`](../../workstreams/go-migration.md)
**Model** — `heavy` ([models.md](../../models.md))

The fleet's second building lane. The program of record is
[`cowork/migration/program.md`](../../migration/program.md); the lane and its approval story
are [`house-rules.md`](../../house-rules.md), **The migration lane** — read both, the charter,
and the program doc's §5 conventions before anything else. `40 7` on purpose: `0 7` carries
sweeps, `20 7` the integrations campaign, `30 7` the fortnightlies.

## Run

1. **Work in flight.** `gh pr list --label "workstream:go-migration" --state open`. If one is
   open, drive it to green on both halves — CI via `gh pr checks` (the `Go core` and
   `Python ↔ Go parity` checks must be present and green, not skipped), review via the
   `pr-feedback` status — merge it per step 6 the moment both are green, and **stop**. That is
   the whole run and the normal weekday.

2. **Carry `main` forward.** With nothing in flight, merge `origin/main` into
   `chore/go-migration` and push. The integration branch is long-lived — thirteen waves live on
   it before any of it reaches `main` — so `main` keeps moving underneath it (Dependabot, a
   human's own work). Merging forward once per wave keeps each conflict small and local to the
   wave that caused it; skipping it saves nothing and hands the final PR every conflict at once.
   If the merge conflicts, resolve it, run `make test-scoped`, and treat that as the whole run.

3. **Which wave?** Read the program doc's §3 table. The next wave is the first unchecked row
   whose ordering dependencies (§3, **Ordering**) are all checked. Never infer progress from
   memory or from Slack — the table and
   `gh pr list --label "workstream:go-migration" --state merged` are the state.

4. **Spec first.** If the wave has no `## PR N — Wave X` section in the program doc yet, this
   run writes one — following the §6/§7 template: verified ground truth, scope in/out, the
   gate, phase commits, lockstep bumps, risks — commits it on the wave branch as phase 0, and
   stops. Code starts next run, against a spec that exists.

5. **Advance one phase.** On branch `cowork/migration-w<N>`, **cut from and based on
   `chore/go-migration`** (one branch per wave, kept for the wave's whole life — **the prefix is
   load-bearing**: `scripts/pr_feedback.py`'s parity hold keys on `cowork/migration-w` plus the
   workstream label, so a wave built on any other branch name merges without its gate
   enforced; the *base* is what keeps the wave off `main`, the *prefix* is what keeps its gate
   armed, and they are independent): implement the next phase commit from the wave's
   spec section. Every
   phase ends green on the verification the spec names (`make go-test && make go-lint &&
   make parity && make test && make lint`, as applicable). When the last phase is done: flip
   the wave's checkbox — `☐` becomes `✔`, exactly that glyph, per the program doc's own edit
   note — and add the previous wave's freeze-table entry on the same branch,
   spawn an independent `code-reviewer` at `deep` (the builder never reviews its own work),
   fix every blocker and should-fix, then open the PR titled
   `migration(w<N>): <the row's contents clause>`, labelled `cowork`,
   `workstream:go-migration`, `type:chore`, `semver:none`, body carrying `Closes YEA-<n>` —
   `cowork-scribe` opens the Linear ticket when the wave branch is created, and it satisfies
   the rest of the Definition of Done's comms items. **The PR's base is `chore/go-migration`**
   (`gh pr create --base chore/go-migration`); a wave PR opened against `main` is wrong and
   must be re-based rather than merged.

6. **Merge the wave into `chore/go-migration` once both halves are green.** CI via
   `gh pr checks` — the `Go core` and `Python ↔ Go parity` checks **present and passing, never
   skipped** — and review via the `pr-feedback` status. Then `gh pr merge <n> --merge`. This is
   the one place the fleet merges its own work, and it is bounded: the integration branch is
   not `main`, it ships nothing to a user, and it carries the same six required contexts as
   `main` so a red wave cannot land. **`main` is still only ever a human's** — when the last
   §3 row is `✔`, a human opens and merges the single `chore/go-migration` → `main` PR.

   Wave N+1 therefore does not wait for anything: it branches off `chore/go-migration`, which
   already carries wave N. Keep `semver:none` on every wave — the version bump and the
   `yeaboi-core` wheel ride the final merge to `main`, once, not thirteen times.

7. **Post nothing to the channel.** [`cron/go-migration-daily.md`](go-migration-daily.md) carries
   this lane's story; a building routine that also narrates is two voices for one fact.

8. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- **An open PR on `workstream:go-migration`** → drive it, stop. One open PR per workstream is
  what makes a many-session wave serial.
- **The next wave's ordering dependencies are unmerged** → exit quietly; the program's
  ordering is not yours to reorder.
- **A gate that will not go green after two runs of honest effort** → comment the blocker on
  the wave's Linear ticket and stop touching the branch; the daily post carries blockers to
  a human.
- **Never merge anything into `main`, and never open a wave PR against it.** The integration
  branch is the only thing this lane merges into. The one PR that reaches `main` is a human's.
- **Never edit the program doc's table, decisions, or conventions** — checkbox flips and
  `## PR N — Wave X` spec appends only. Never delete a Python twin the plan does not name.
  **Never weaken a parity corpus to make it pass** — a wrong gate is worse than a red one, and
  rewriting the test to fit the port is the one move this lane's approval never covered.
- **Never edit outside the charter's `Owns` and its `**Extends**` sites**, and never anything
  but the named operation at the latter. Before touching an `Extends` path, run the collision
  guard from house-rules' campaign lane: if platform has an open PR touching that file, take a
  different angle this run.
- **Never apply `claude-implement` or `feedback-override`**, to anything.
- **A wave overrunning its estimate is not a failure**; it runs until its gate is green. Never
  abandon or restart a wave branch without a human closing it.
