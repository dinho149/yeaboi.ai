# Sweep procedure

The shared run shape for all fourteen workstream sweep routines. Your routine file names the
workstream and any per-run focus; everything else is here.

1. **Read** [house-rules.md](house-rules.md), [models.md](models.md), your charter in
   `workstreams/<name>.md`, and the `.claude/skills/*/SKILL.md` your charter names.

2. **Check for work in flight** — `gh pr list --label "workstream:<name>" --state open --json
   number,createdAt,url`. If a PR is open: drive it to green and **stop**. That is the whole run.
   Green means both halves:
   - **CI** — `gh pr checks <n>`, then fix what is red.
   - **Feedback** — `make pr-feedback PR=<n>`, then follow the procedure in
     `.claude/commands/pr-feedback.md`. DoD item 10. This line used to read "answer review
     comments" in brackets with nothing behind it, and for months nothing did.

     **On a cowork PR you may fix a finding; you may not dismiss one.** Fix what is right and
     push — a re-review reports `open=0` on its own, which is what clears the gate. If you
     believe a `blocker` or `should-fix` is wrong, **do not write an `<!-- addressed: … -->`
     marker**: convert the PR to a proposal (see step 5) and let a human answer it.
     `scripts/pr_feedback.py` enforces this rather than trusting it — an ack from the PR's own
     author does not count on an unattended PR — so a marker written here does nothing except
     make the PR look answered to a reader. Disagreeing with a review is a judgement call, and
     the whole point of the auto lane is that it only carries work that needs none.

   If that PR is already green and more than 7 days old, comment once on it saying the workstream has
   been blocked on it since `<date>` and has scouted nothing in the meantime. One open PR per
   workstream plus a weekly cadence means an unmerged PR stops this workstream indefinitely, and
   without that comment the digest would report the silence as if the scout had found nothing.

3. **Scout** — spawn `cowork-scout` at the `standard` tier (`security` and `integrations` use
   `deep`) with your charter's paths. If your charter declares a `**Reads**` paragraph, pass
   those paths too — the scout may look there, and no builder may ever edit there. It returns
   ranked finds, each classified `auto` or `propose` against the allowlist in house-rules. The finds include up to 3
   user-facing opportunities (`type: feature|improvement`) alongside defects — see the opportunity
   pass in `.claude/agents/cowork-scout.md`; they ride the same ranking and the same propose lane.
   Nothing found is a normal outcome: exit silently.

4. **Deduplicate** — `gh issue list --label "workstream:<name>" --state open` and
   `gh issue list --label "workstream:<name>" --state closed --limit 50`. Drop any find that
   restates an open proposal or one that was closed unapproved. Do not re-file rejected ideas.

5. **Auto lane** — take **one** `auto` find per run, never more: the one with the highest
   `impact`, breaking ties toward the lower `risk`. Impact over risk, in that order — a low-risk
   nothing is still nothing, and the reason risk is the tie-break rather than the sort key is that
   the allowlist has already excluded everything genuinely risky. Put the find's
   `impact`/`effort`/`risk` and the number of `auto` finds you passed over in the PR body, so a
   wrong pick is legible afterwards instead of being an unrecorded judgement.

   A `type: bug` find carries one extra obligation, and it is the admission ticket rather than a
   nicety: **a regression test that fails before the fix and passes after.** Run it both ways and
   paste both results into the PR body. No reproduction, no auto lane — re-file it as a proposal.

   Tiers come from [models.md](models.md); pass each one explicitly on spawn:
   - spawn `cowork-scribe` (`standard`) to open the Linear ticket (DoD item 1)
   - spawn `cowork-builder` (`deep`) to implement it in a branch off `main` and run the DoD gate
   - **you** then spawn `code-reviewer` (`deep`) on `git diff main...HEAD` with a one-paragraph
     description of the find — the builder does not review its own work, and agents do not nest
   - **fix** every `blocker` and `should-fix` finding, then have the builder open the PR labelled
     `cowork` + `workstream:<name>` + the find's `type:<type>` — the daily standup reads the type
     off the PR, so a PR without one ships untagged. A finding you cannot fix, or think is wrong,
     ends the auto lane for this find: close the branch, file it as a proposal quoting the finding,
     and stop. Nothing here may overrule a reviewer
   - once the PR exists, spawn `cowork-scribe` (`standard`) again to attach it to the Linear ticket
     and move the ticket to **In Review** — the `Closes YEA-NN` line in the PR body then carries it
     to Done on merge
   - **arm auto-merge, but only if the gate is actually armed.** Check the ruleset first, exactly
     as `.github/workflows/codeql-triage.yml` does — this is a fact to read, not a judgement to
     make:

     ```bash
     gh api "repos/$REPO/rules/branches/main" \
       --jq 'any(.[]; .type=="required_status_checks" and any(.parameters.required_status_checks[]; .context=="pr-feedback"))'
     ```

     `true` → `gh pr merge <n> --auto --squash`. The ruleset then merges it once every required
     check is green, which is what makes this lane unattended.

     `false` → **do not arm it.** Say so in the run log: `pr-feedback` is not a required status
     check, so `--auto` would merge on CI alone, with no review in the loop — which is precisely
     the thing the auto lane is trading a human approval for. Leave the PR open for a human to
     merge. The automation is real either way; the unattended *merge* is not, until that context is
     added ([house-rules.md](house-rules.md), **The gate**).

6. **Propose lane** — hand every remaining `propose` find to `cowork-scribe` (`standard`), which
   files one GitHub issue each (`cowork:proposal` + `workstream:<name>` + `type:<type>`). **No
   Linear ticket, and no Slack post.** The issue is the queue; the digest is the only thing that
   talks to Slack about proposals; and Linear is opened at approval, not at proposal — see
   [definition-of-done.md](definition-of-done.md).

7. **Stop.** Do not follow interesting threads outside your charter. File them as proposals for the
   owning workstream and move on.

## Stop conditions

Abort the run and report if: `main` cannot be fetched; `make test` fails on a clean checkout (that is
a `platform` problem, not yours — file an issue); or the scout returns more than 10 finds (something
is wrong with the charter's scope, and filing 10 issues would bury the digest).
