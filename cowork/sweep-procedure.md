# Sweep procedure

The shared run shape for all thirteen maintenance sweep routines. Your routine file names the
workstream and any per-run focus; everything else is here.

1. **Read** [house-rules.md](house-rules.md), [models.md](models.md), your charter in
   `workstreams/<name>.md`, and the `.claude/skills/*/SKILL.md` your charter names. **Then read your
   section of [calibration.md](calibration.md)** — what this workstream keeps getting wrong, with
   the evidence behind it, so you do not spend this run re-making a mistake somebody already paid
   for. No heading for your workstream means nothing is recorded, which is the normal state.

2. **Check for work in flight** — `gh pr list --label "workstream:<name>" --state open --json
   number,createdAt,url`. If a PR is open and not yet green: drive it to green and **stop**. That
   is the whole run. If it is open and **green, it is waiting for the next release batch** — the
   healthy steady state, not a stall: a human's `make batch-assemble` will fold it in
   ([release-signoff.md](release-signoff.md)). Stop quietly; one open PR per workstream still
   means this workstream builds nothing new until it ships.
   Green means both halves:
   - **CI** — `gh pr checks <n>`, then fix what is red.
   - **Feedback** — `make pr-feedback PR=<n>`, then follow the procedure in
     `.claude/commands/pr-feedback.md`. DoD item 10. This line used to read "answer review
     comments" in brackets with nothing behind it, and for months nothing did.

     **On a cowork PR you may fix a finding; you may not dismiss one.** Fix what is right and
     push, then **reply saying what you changed** and end the reply with
     `<!-- addressed: claude-review fixed=N -->`. Both halves are required: the re-review
     reporting `open=0` is what stops the finding blocking, and the reply is what stops the
     whole record of the fix being a number going down. If you believe a `blocker` or
     `should-fix` is wrong, **do not write an `answered=` claim**: convert the PR to a proposal
     (see step 5) and let a human answer it. `scripts/pr_feedback.py` enforces the split rather
     than trusting it — a dismissal from the PR's own author does not count on an unattended PR,
     and a `fixed=` claim from that same author does, because the reviewer's next read of the
     diff is what checks it. Disagreeing with a review is a judgement call, and the whole point
     of the auto lane is that it only carries work that needs none.

     Same rule on the resolve button: reply in a thread before you resolve it. A thread this
     PR's author resolved with nothing from them in it comes back as an open item.

   If that PR is already green and more than 14 days old, comment once on it saying the workstream
   has been waiting on it since `<date>` and has scouted nothing in the meantime. Under weekly
   batches a green PR routinely waits up to a week — that is the design — but fourteen days means
   it missed a batch, usually because `batch_assemble.py` skipped it for a conflict; a rebase is
   what puts it in the next one, and without the comment the digest would report the silence as if
   the scout had found nothing. A rebase resets nothing in the review round count
   (`scripts/pr_feedback.py` counts rounds, not pushes), so drive a rebased PR back to green
   promptly rather than letting it sit.

3. **Run your lenses, then scout.** If your routine file has a `## Lenses` section, run each one
   named there first and hand the output to the scout as evidence:

   ```bash
   uv run python scripts/hygiene_lens.py --lens <lens> --workstream <name> --json
   ```

   A lens is a standing thing to look for with a command behind it — see
   [hygiene-lenses.md](hygiene-lenses.md). Its output is *evidence*, never a verdict: the `lane`
   it reports is the ceiling for a find from that lens, and the scout still classifies it against
   [house-rules.md](house-rules.md) like anything else. **A lens result of nothing is a result.**

   Each lens returns **one** find listing up to `max_batch` instances, not one per instance —
   house-rules already grants three same-`type` `chore` items in one PR, and six one-line
   deletions read together is one review. Anything over the cap comes back as `held` and is
   reported, never dropped.

   **Survey narrow, confirm wide, change narrow.** A lens finds only inside your `**Owns**` paths,
   but confirming a find may read the whole repository — proving a negative is a read, and a read
   changes nothing about who may edit. The find stays yours: the symbol lives in your paths, and
   only your builder may touch it.

   Then spawn `cowork-scout` at the `standard` tier (`security` uses `deep`) with your
   charter's paths. If your charter declares a `**Reads**` paragraph, pass those paths too — the
   scout may look there, and no builder may ever edit there. It returns ranked finds, each
   classified `auto` or `propose` against the allowlist in house-rules, and each typed from a
   closed vocabulary of four: `bug`, `chore`, `docs`, `security`. **A sweep never returns a
   `feature` or an `improvement`** — capability work exists only inside an integration campaign
   (`integration-campaign.md`), approved by provider rather than by find. Nothing found is a
   normal outcome: exit silently.

4. **Deduplicate — five outcomes, not one.** `gh issue list --label "workstream:<name>" --state
   open` and `gh issue list --label "workstream:<name>" --state closed --limit 50`. For a find that
   restates an issue you already have, what happens depends on that issue's label *and* on the lane
   you put the find in:

   - restates an issue **closed** unapproved → **drop it.** A closing is a rejection and a
     rejection is durable. Do not re-file rejected ideas.
   - restates an open **`cowork:queued`** issue → **it already is your work item.** Do not file it
     and do not drop it: carry that issue's number into step 5.
   - restates an open **`cowork:proposal`** issue and your find is **`propose`** → **drop it.** The
     question is already in front of a human; asking it twice does not answer it.
   - restates an **open** issue carrying **neither** label, **and listed by `--lapsed`** → **that
     is a lapsed question, and it is yours to re-ask or to build.** `cron/digest.md` step 4 strips
     `cowork:proposal` at fourteen days and leaves the issue open precisely so this outcome exists:
     the question stopped being asked, the find was never rejected. Re-label it in place rather
     than filing a second write-up — `cowork:proposal` if your find is `propose` (it takes one
     slot, and it keeps its number, its body and its history), `cowork:queued` if your find is
     `auto`, carried into step 5 like any other. Add before you remove, and never `gh api` with
     `PUT .../labels`, exactly as above.

     ```bash
     uv run python scripts/cowork_setup.py --lapsed <name>
     ```

     **Carrying neither label is a much wider set than "lapsed", and the difference is the whole
     safety of this outcome.** *Every* issue a human opened and tagged `workstream:<name>` has that
     shape, and so does every `integration:candidate`. Re-labelling one of those `cowork:queued`
     puts a stranger's issue into the unattended build lane with no human verb ever having been
     given — the one thing the queue label is supposed to mean. So the shape does not qualify an
     issue; **a recorded lapse does**, which is a `cowork:proposal` `unlabeled` event, which is
     what `lapsed_items()` checks and what `--lapsed` reports. An issue that is not in that list is
     somebody else's: leave its labels alone and file your find the ordinary way. `lapsed: null`
     means the query failed — treat every issue as not-lapsed that run, because the direction that
     costs you a duplicate is safer than the one that builds unasked.
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

     **End that comment with the marker**, on its own line:
     `<!-- bounced: reason=<condition> -->`, where `<condition>` is one of exactly five —
     `no-repro`, `user-facing-wording`, `outside-owns`, `public-api`, `needs-judgement`. They are
     the auto-lane conditions from [house-rules.md](house-rules.md) rather than a second taxonomy,
     because the axes a find fails on are exactly the conditions it had to clear. The sentence is
     for whoever reads the issue; the marker is for `scripts/cowork_metrics.py`, which counts how
     often each workstream misclassifies — a scout that bounces on `no-repro` every week is calling
     things `auto` it cannot prove, and that is a fact about the charter, not about the week.
   - **the evidence no longer reproduces** — the `**Evidence**` line points at code that has
     changed or a command that now passes → **close it** with `no longer reproduces at <sha>`
     followed by `<!-- rejected: reason=no-longer-reproduces sha=<sha> -->`, and move on. That is
     the correct terminal state for a stale find, and closing is what stops it coming back. It is a
     *different* fault from a bounce and carries a different marker: a bounce means the find was
     misclassified, a stale close means it was real and nobody got to it in time.

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
     `cowork` + `workstream:<name>` + the find's `type:<type>` + `semver:none` — the daily standup
     reads the type off the PR, so a PR without one ships untagged, and `semver:none` is what stops
     `auto-version.yml` bumping the version on every fleet branch: the release batch that ships
     this PR carries the one bump, and a per-PR bump collides with every other constituent the
     moment `scripts/batch_assemble.py` squashes them together. A finding you cannot fix, or think is wrong,
     ends the auto lane for this find: close the branch, file it as a proposal quoting the finding,
     and stop. Nothing here may overrule a reviewer
   - once the PR exists, spawn `cowork-scribe` (`standard`) again to attach it to the Linear ticket
     and move the ticket to **In Review** — the `Closes YEA-NN` line in the PR body then carries it
     to Done on merge. **When you built a queued item, the PR body also carries `Closes #<n>`** for
     its GitHub issue. The merge is the only thing that closes a queue entry; without that line the
     queue only grows
   - **leave the PR open — never merge, never arm auto-merge.** A gate-green fleet PR is FINISHED
     from this sweep's point of view: it waits, with every other fleet PR, for the next release
     batch. A human runs `make batch-assemble`, which folds every gate-green fleet PR into one
     `batch/<date>` PR, hand-tests the assembled build, and merges it — that merge is the
     sign-off, and it is the only way fleet work reaches `main` and users
     ([release-signoff.md](release-signoff.md)). `gh pr merge` in any form — including `--auto` —
     is not this routine's to run: the auto lane's "unattended" ends at the open PR, and an open
     green PR here is the healthy steady state, not a stall.

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

   **A find carrying `owner:` is filed for the owner, and against the owner's slots.** The scout
   sets that field on anything outside your `**Owns**` (`cowork-scout.md`, step 2) and it returned
   in the payload and was read by nobody: the label recipe below said `workstream:<name>`,
   `<name>` was always *yours*, and [house-rules.md](house-rules.md)'s *a find outside your
   charter's paths becomes a proposal issue labelled for the owning workstream* was true in prose
   and false in practice. Issue #170 is a `tests/unit/test_surface_parity.py` find — **platform**'s
   file — labelled `workstream:analysis`. It held one of analysis's two slots for a file analysis
   may not open, and froze the workstream that found it for ten days.

   So for a routed find: resolve the owner rather than reading the charter by eye, ask *its* slots,
   and file under its name.

   ```bash
   uv run python scripts/cowork_setup.py --owner <the find's primary path>
   uv run python scripts/cowork_setup.py --proposal-slots <owner>
   ```

   `--owner` answers `workstream: null` when no charter claims the path, when two claim it equally,
   or when it is the constitution — **never a guess**, the same rule `--proposal-slots` applies to a
   queue it could not read. A find nothing can route is one to leave in front of a person: file it
   under your own name and say in the run log that it is unrouted. If the owner has no slot,
   **drop it** — the ordinary rule, unchanged. Your own slots are untouched either way; a find you
   routed away is not one you asked about.

   Each filed find goes to `cowork-scribe` (`standard`), one GitHub issue each (`cowork:proposal` +
   `workstream:<owner>` + `type:<type>`, where `<owner>` is yours unless the find was routed).
   **No Linear ticket, and no Slack post.** The issue is the queue; the digest is the only thing
   that talks to Slack about proposals; and Linear is opened at approval, not at proposal — see
   [definition-of-done.md](definition-of-done.md).

7. **Stop.** Do not follow interesting threads outside your charter. File them as proposals for the
   owning workstream and move on.

8. **Check in.** Close the run by following [check-in.md](check-in.md). A sweep that filed nothing
   still checks in, and reports `ok` with `nothing to do` — that green line is the only thing
   separating a quiet charter from a sweep that never fired, and the fleet had no way to tell those
   apart. It changes nothing else: a sweep still posts no channel message and still files only
   GitHub issues.

## Stop conditions

Abort the run and report if: `main` cannot be fetched; `make test` fails on a clean checkout (that is
a `platform` problem, not yours — file an issue); or the scout returns more than 10 finds (something
is wrong with the charter's scope, and filing 10 issues would bury the digest).

**A full proposal queue is not one of these.** `slots: 0` ends step 6 and nothing else: the auto
lane in step 5 still runs, and a run that ships a PR and files no proposal is a good run. Exit
quietly, the way step 2 does when a PR is already open — no issue, no channel message, no
explanation posted anywhere. The digest is where the fleet's held workstreams are reported, once, in
one place.

"Quietly" has meant "nothing at all" since this file was written, and now means "nothing in the
channel": step 8 still runs, because a sweep that exits early is exactly the run somebody needs to
be able to tell apart from one that never started. It reports 🟢 with what it did and did not do —
the check-in is a thread reply and says nothing about the queue, which stays the digest's to
report.

**And a queue with work in it is not one either.** `cowork:queued` items change what step 5 builds
and nothing else: step 6 still files, and a run that ships a queued item *and* files a proposal is a
normal run. The queue is reported by the digest, once, in one place — a sweep says nothing about it.
