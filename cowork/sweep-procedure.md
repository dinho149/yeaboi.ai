# Sweep procedure

The shared run shape for all thirteen maintenance sweep routines. Your routine file names the
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

3. **Scout** — spawn `cowork-scout` at the `standard` tier (`security` uses `deep`) with your
   charter's paths. If your charter declares a `**Reads**` paragraph, pass those paths too — the
   scout may look there, and no builder may ever edit there. It returns ranked finds, each
   classified `auto` or `propose` against the allowlist in house-rules, and each typed from a
   closed vocabulary of four: `bug`, `chore`, `docs`, `security`. **A sweep never returns a
   `feature` or an `improvement`** — capability work exists only inside an integration campaign
   (`integration-campaign.md`), approved by provider rather than by find. Nothing found is a
   normal outcome: exit silently.

4. **Deduplicate — four outcomes, not one.** `gh issue list --label "workstream:<name>" --state
   open` and `gh issue list --label "workstream:<name>" --state closed --limit 50`. For a find that
   restates an issue you already have, what happens depends on that issue's label *and* on the lane
   you put the find in:

   - restates an issue **closed** unapproved → **drop it.** A closing is a rejection and a
     rejection is durable. Do not re-file rejected ideas.
   - restates an open **`cowork:queued`** issue → **it already is your work item.** Do not file it
     and do not drop it: carry that issue's number into step 5.
   - restates an open **`cowork:proposal`** issue and your find is **`propose`** → **drop it.** The
     question is already in front of a human; asking it twice does not answer it.
   - restates an open **`cowork:proposal`** issue and your find is **`auto`** → **reclassify that
     issue in place**, and carry it into step 5:

     ```bash
     gh issue edit <n> --add-label cowork:queued --remove-label cowork:proposal
     gh issue comment <n> --body "…"
     ```

     Add before you remove, so a run that dies between the two leaves an issue carrying both —
     which `--proposal-slots` reads as queued and the digest reads as still listed, the harmless
     direction on each side. **Never `gh api` with `PUT .../labels`**: that verb replaces the whole
     set, which is how #172 lost its workstream and type labels in one call and ran without a
     charter.

     The write-up on the issue does not change. **What changes is who answers it.** An issue is a
     question waiting on a human, and a find on the auto-lane allowlist is by definition not one.
     Dropping such a find because a question about the same thing is open is how a fleet with an
     unattended lane came to have forty-two open proposals, none of them ever answered, and nothing
     shipped.

     **Never reclassify a `codeql:` issue.** `codeql-triage.yml` opens one only for a rule whose
     `propose` entry in `.github/codeql/triage-policy.yml` records why a human has to decide it,
     so queuing it hands a recorded human decision back to a machine to re-make. It also breaks
     that workflow's own dedupe, which searches `--label cowork:proposal` for the rule id: once
     the issue carries `cowork:queued` instead, next week's run does not find it and opens a
     second **public** issue re-asking the same question. Leave it a proposal.

     **Only for an issue whose find you classified `auto` yourself, this run, having read the
     issue.** Never in bulk, never on the strength of a matching title. The one-time backfill of an
     existing backlog is `scripts/cowork_setup.py --migrate-proposals`, which a human runs and no
     routine can.

   `--proposal-slots <name>` (step 6) returns the open proposal half of that list as `blocking`,
   and `uv run python scripts/cowork_setup.py --queued <name>` returns the other half — both over
   REST rather than GraphQL, which is the half that answers in a routine session, where `gh issue
   list --json` is refused by the egress proxy. Use them when the `gh` reads above come back empty;
   an empty result there means "could not ask", not "nothing open", and a dedupe run against
   nothing re-files everything.

5. **Auto lane — build one item per run, and prefer the one that already has an issue.**

   Read the queue first:

   ```bash
   uv run python scripts/cowork_setup.py --queued <name>
   ```

   It returns the open `cowork:queued` issues for this workstream with their `impact`, `risk` and
   age, already sorted. Merge them with today's `auto` finds — a find carrying a number from step 4
   is the *same item* as the queued issue it names, and is listed once, not twice — into one list,
   and take the top of it:

   1. **`critical: true` first.** A critical find pre-empts the queue exactly as it pre-empts the
      cap ([house-rules.md](house-rules.md), **Critical**).
   2. then **highest `impact`** — the same key this step has always sorted on;
   3. ties to **lower `risk`** — the same tie-break;
   4. ties to **the queued item**, and among queued items to the **oldest**.

   One comparator over one list, because the queue is not a different kind of work — it is the same
   finds, already written down. Impact still outranks age, so a genuinely more important thing
   found this morning still ships this morning; the queue wins every tie, which is what drains it.
   And the queue is finite and shrinking by construction: **nothing files into it** (step 4 only
   moves an issue that already exists, and a passed-over `auto` find is still dropped), so it
   terminates.

   Take the top item and no more. Put in the PR body which it was, its `impact`/`effort`/`risk`,
   whether it came from the queue (with the issue number) or from today's scout, and how many
   `auto` items you passed over — so a wrong pick is legible afterwards instead of being an
   unrecorded judgement.

   **A queued issue is a hypothesis, not a permission.** Before you spawn anything, check it
   against the auto-lane allowlist yourself, exactly as you would a fresh find. It may have been
   written when the item was still a proposal, and the backfill that queued it applied a mechanical
   rule rather than a judgement. Three outcomes:

   - **it clears the allowlist** → build it. Everything below this line is unchanged.
   - **it does not clear it** — no reproducible test, the fix would change user-facing wording, the
     paths fall outside your charter's `Owns` → **bounce it**:
     `gh issue edit <n> --add-label cowork:proposal --remove-label cowork:queued`, comment one line
     naming the condition that failed, and move to the next item on the list. A wrongly-queued item
     costs one comment; it never costs a merge.
   - **the evidence no longer reproduces** — the `**Evidence**` line points at code that has
     changed or a command that now passes → **close it** with `no longer reproduces at <sha>`, and
     move on. That is the correct terminal state for a stale find, and closing is what stops it
     coming back.

   A `type: bug` find carries one extra obligation, and it is the admission ticket rather than a
   nicety: **a regression test that fails before the fix and passes after.** Run it both ways and
   paste both results into the PR body. No reproduction, no auto lane — re-file it as a proposal.
   **The queue does not waive this**, for a queued item any more than for a fresh find.

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
     to Done on merge. **When you built a queued item, the PR body also carries `Closes #<n>`** for
     its GitHub issue. The merge is the only thing that closes a queue entry; without that line the
     queue only grows
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

6. **Propose lane — file into the slots you have, and no further.** Ask how many there are; do not
   count the queue by eye:

   ```bash
   uv run python scripts/cowork_setup.py --proposal-slots <name>
   ```

   It answers `{"cap": 2, "open": …, "slots": …, "queued": …, "blocking": [ … ]}`. **`queued` is
   reported, never counted**: a `cowork:queued` issue is work in flight and occupies no slot, so a
   workstream with six queued items and no open proposals has its full two. That is the whole
   arithmetic of the split — a work item must not hold a slot that only a human verb can release.

   Then, in this order:

   - **Every `critical: true` find is filed, whatever `slots` says.** The four cases are in
     [house-rules.md](house-rules.md), **Critical**; the scout has already scored them and you do
     not re-score. One exception, and it points the other way: a critical *security* find that
     would require disclosure takes the carve-out in
     [`cron/security-sweep.md`](routines/cron/security-sweep.md) and never becomes a public issue.
   - **Fill the remaining slots** with the highest-ranked non-critical `propose` finds — the same
     impact-over-effort order the scout returned them in.
   - **Drop the rest. Silently.** No issue, no comment on the blocking issues, no Slack. A held
     find is not lost: you will survey the same surface next run and re-rank it, so one that still
     matters comes back and one that stopped mattering does not.
   - **`slots: null` means the query failed, and that is zero slots** — file the criticals and
     nothing else. A read you could not make is never spoken as a clean answer.

   Say in your run log how many you filed and how many you held. Nothing persists between runs, so
   that line is the only place a single run is legible; `cron/digest.md` reports the standing
   picture from the same command.

   Each filed find goes to `cowork-scribe` (`standard`), one GitHub issue each (`cowork:proposal` +
   `workstream:<name>` + `type:<type>`). **No Linear ticket, and no Slack post.** The issue is the
   queue; the digest is the only thing that talks to Slack about proposals; and Linear is opened at
   approval, not at proposal — see [definition-of-done.md](definition-of-done.md).

7. **Stop.** Do not follow interesting threads outside your charter. File them as proposals for the
   owning workstream and move on.

## Stop conditions

Abort the run and report if: `main` cannot be fetched; `make test` fails on a clean checkout (that is
a `platform` problem, not yours — file an issue); or the scout returns more than 10 finds (something
is wrong with the charter's scope, and filing 10 issues would bury the digest).

**A full proposal queue is not one of these.** `slots: 0` ends step 6 and nothing else: the auto
lane in step 5 still runs, and a run that ships a PR and files no proposal is a good run. Exit
quietly, the way step 2 does when a PR is already open — no issue, no Slack, no explanation posted
anywhere. The digest is where the fleet's held workstreams are reported, once, in one place.

**And a queue with work in it is not one either.** `cowork:queued` items change what step 5 builds
and nothing else: step 6 still files, and a run that ships a queued item *and* files a proposal is a
normal run. The queue is reported by the digest, once, in one place — a sweep says nothing about it.
