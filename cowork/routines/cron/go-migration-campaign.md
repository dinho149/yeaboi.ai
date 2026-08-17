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
   `pr-feedback` status — arm the merge per step 5, and **stop**. That is the whole run and
   the normal weekday.

   **Bootstrap exception, first runs only:** PR #224 (Wave 6, branch `go-docs-score`) predates
   this workstream. If it is open and unlabelled, label it `cowork` +
   `workstream:go-migration` + `type:chore`, rebase it on `origin/main`, repair its failed
   auto-version run (a wave PR bumps **minor** — see
   `.claude/skills/ci-and-release/SKILL.md`), and drive it exactly as above. Its branch does
   not carry the wave prefix, so the parity hold covers it **by number** instead
   (`PARITY_GATED_PRS` in `scripts/pr_feedback.py`) — the label is what arms that, so apply
   it before anything else.

2. **Which wave?** Read the program doc's §3 table. The next wave is the first unchecked row
   whose ordering dependencies (§3, **Ordering**) are all checked. Never infer progress from
   memory or from Slack — the table and
   `gh pr list --label "workstream:go-migration" --state merged` are the state.

3. **Spec first.** If the wave has no `## PR N — Wave X` section in the program doc yet, this
   run writes one — following the §6/§7 template: verified ground truth, scope in/out, the
   gate, phase commits, lockstep bumps, risks — commits it on the wave branch as phase 0, and
   stops. Code starts next run, against a spec that exists.

4. **Advance one phase.** On branch `cowork/migration-w<N>` (one branch per wave, kept for the
   wave's whole life — **the prefix is load-bearing**: `scripts/pr_feedback.py`'s parity hold
   keys on `cowork/migration-w` plus the workstream label, so a wave built on any other branch
   name merges without its gate enforced): implement the next phase commit from the wave's
   spec section. Every
   phase ends green on the verification the spec names (`make go-test && make go-lint &&
   make parity && make test && make lint`, as applicable). When the last phase is done: flip
   the wave's checkbox — `☐` becomes `✔`, exactly that glyph, per the program doc's own edit
   note — and add the previous wave's freeze-table entry on the same branch,
   spawn an independent `code-reviewer` at `deep` (the builder never reviews its own work),
   fix every blocker and should-fix, then open the PR titled
   `migration(w<N>): <the row's contents clause>`, labelled `cowork`,
   `workstream:go-migration`, `type:chore`, body carrying `Closes YEA-<n>` — `cowork-scribe`
   opens the Linear ticket when the wave branch is created, and it satisfies the rest of the
   Definition of Done's comms items.

5. **Leave the wave PR open — never merge, never arm auto-merge.** A gate-green wave waits,
   with every other fleet PR, for the next release batch: a human's `make batch-assemble` folds
   it in and the human's merge of the batch PR ships it
   ([release-signoff.md](../../release-signoff.md)). The parity hold still rides the
   `pr-feedback` context on the wave PR itself, unchanged. Waves therefore advance one per
   batch cycle — wave N+1 branches off `main`, which gains wave N only when the batch ships —
   which is the program's one-wave-at-a-time cadence with the shipping made human. Label the
   wave PR `semver:none` like every fleet PR; the batch carries the bump, and a wave that bumps
   `packaging/yeaboi-core` still publishes its core wheel only when the batch merge lands the
   bump on `main`.

6. **Post nothing to the channel.** The Tuesday progress post and the wave-merged post carry
   this lane's story; a building routine that also narrates is two voices for one fact.

7. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- **An open PR on `workstream:go-migration`** → drive it, stop. One open PR per workstream is
  what makes a many-session wave serial.
- **The next wave's ordering dependencies are unmerged** → exit quietly; the program's
  ordering is not yours to reorder.
- **A gate that will not go green after two runs of honest effort** → comment the blocker on
  the wave's Linear ticket and stop touching the branch; the Tuesday post carries blockers to
  a human.
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
