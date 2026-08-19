# retune

**Trigger** — cron `0 8 * * 0` (Sun 08:00 UTC)
**Summary** — what the fleet got wrong last week, recorded so it stops getting it wrong
**Workstream** — [`workstreams/fleet.md`](../../workstreams/fleet.md)
**Model** — `standard`

Not a sweep. Every other routine asks whether some code is right; this one asks whether **what the
fleet filed was worth filing**. `cron/digest.md` has reported the answer every Monday since it was
written — *"a workstream rejected twenty times running is not healthy because it was busy"* — and
nothing has ever acted on it. This is the thing that acts.

**Read [`workstreams/fleet.md`](../../workstreams/fleet.md) first, in full.** The rule it carries is
the only thing standing between this routine and an agent that can lower its own bar:

> **A routine may tighten itself unattended. Only a human may loosen it.**

## Run

1. **Read** [house-rules.md](../../house-rules.md), [`workstreams/fleet.md`](../../workstreams/fleet.md),
   and [`calibration.md`](../../calibration.md) in full. You are about to append to the last one;
   read what is already there so you do not record the same pattern twice in different words.

2. **Gather the week's failure signals.** One command carries most of them:

   ```bash
   uv run python scripts/cowork_metrics.py --window 30 --no-runs --json
   ```

   **`--no-runs` is not optional and is not a performance flag.** It skips the fleet's monthly run-ledger
   issues. No routine reads the run ledger — outcomes are durable records of decisions and reading
   them is what every sweep already does, but run telemetry is what the fleet *spent*, and a
   routine that decides anything from it makes the fleet's behaviour a function of its own
   resource consumption. A test asserts this file passes the flag and never names the ledger.

   Read `reasons_by_workstream` — that is the whole point of the call. It splits, per charter:

   - **`bounced`** — a queued item pushed back out of the queue. A **misclassification**: the scout
     called something `auto` it could not carry. `no-repro` says it claimed a bug with no
     reproduction; `user-facing-wording` says it read copy as behaviour; `outside-owns` says it
     scouted outside its charter.
   - **`rejected`** — a find that was real and unwanted. `slack-veto` is a human's ❌;
     `aged-out` is a proposal that lapsed unanswered and then went thirty days without any sweep
     bringing it back, which is a fact about the *find* having stopped mattering.

     **A lapse is not in this split, and must not be read into it.** `cron/digest.md` step 4 strips
     `cowork:proposal` at fourteen days and leaves the issue open, so a lapsed find is neither a
     misclassification nor a rejection — it is a question nobody answered, which is a fact about
     the *human's* attention and not about the charter. Calibrating on it would re-aim a workstream
     for being ignored.

   Two more, read directly:

   ```bash
   gh pr list --label cowork --state merged --limit 100 --json number,labels,title
   gh issue list --label implement-blocked --state all --limit 50 --json number,labels,title
   ```

   `review-capped` on a merged PR is a finding the review ran out of rounds to pursue;
   `implement-blocked` is an approved item the implement job could not build. Both are the fleet
   failing at the *build* end rather than the *find* end, and they belong to different fixes.

3. **Decide whether there is a pattern — the bar is three.** Three occurrences of the same failure,
   in the same workstream, for the same reason. Two is a coincidence with a witness. One is the
   system working.

   **If nothing clears the bar, say so and go to step 6.** That is the expected outcome most weeks
   and it is a good run. A routine that finds something to tighten every single Sunday is inventing
   work in the one place where inventing work compounds.

   **And do not tune on one week.** Most of these charters sweep fortnightly or monthly, so three
   occurrences may span two months — which is fine, that is what the 30-day window plus
   `calibration.md`'s existing rows are for. What is not fine is calling a rate that moved by one
   issue a trend. When the signal is thin, the answer is "not enough yet", and it is a complete
   answer.

4. **Auto lane — exactly two operations, and you may do one per run.** Both are append-only, onto
   files that exist to accumulate mistakes. Neither can make the fleet louder, which is the whole
   reason they need no approval.

   1. **A row in [`calibration.md`](../../calibration.md)** under the failing workstream's heading,
      in the four-column format that file specifies. **Every row cites its issue numbers** — the
      file is read by something that cannot tell a fact from a rumour. The `Instead` column must be
      a rule a scout can apply on the spot; if you cannot write it, you have an observation rather
      than a calibration, and it goes to step 5.
   2. **An entry under `excludes:` in `.github/hygiene/lens-policy.yml`**, quoting in its `why:` the
      false positive that motivated it, with the file and symbol it fired on. A lens exclusion is a
      claim that a detector is wrong about a specific shape — name the shape.

   Then the ordinary auto-lane machinery, unchanged: `cowork-scribe` (`standard`) opens the Linear
   ticket, `cowork-builder` (`deep`) makes the edit in a branch off `main` and runs the DoD gate,
   **you** spawn `code-reviewer` (`deep`) on the diff, and the PR opens labelled `cowork` +
   `workstream:fleet` + `type:chore` + `semver:none`. Leave it open — never merge, never arm
   auto-merge; it ships with the next release batch, exactly as
   [sweep-procedure.md](../../sweep-procedure.md) step 5 says.

5. **Propose lane — everything else, and "everything else" is most of it.**

   ```bash
   uv run python scripts/cowork_setup.py --proposal-slots fleet
   ```

   A **charter re-aim** is the fix the digest has been describing, and it is a proposal: deciding
   what a workstream is *for* is a human's call. So is raising a threshold, adding a focus area,
   deleting a lens, changing a cadence, editing another routine, and touching anything under
   `scripts/`. File through `cowork-scribe` with the evidence in the body — the issue numbers, the
   reason counts, and the specific edit you are asking for. **A proposal that does not name the
   edit is a complaint.**

   `slots: 0` ends this step and nothing else, exactly as it does for a sweep.

6. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). A retune that found no pattern reports `ok` with
   `nothing to tune`, and that line is the only thing separating a quiet week from a routine that
   never fired.

## Stop conditions

- **Anything that would make the fleet find *more* is not yours, in either lane.** Loosening is a
  human's, and the proposal is how you ask.
- **The constitution is untouchable.** `house-rules.md`, `definition-of-done.md`,
  `sweep-procedure.md`, `models.md`, `crew.md` and `.claude/agents/cowork-*.md` are excluded from
  this workstream's paths by name, and a test asserts it against the resolved paths rather than
  against this sentence. If a pattern's only fix lives in one of those, that is a proposal and it
  is the most important one you will ever file — say so in the title.
- **Never delete or rewrite an existing `calibration.md` row.** A pattern that stopped being true
  earns a *second* row saying so. A record that can be edited away is not a record.
- **`reasons_by_workstream` empty is not the same as no failures.** The markers started landing on
  2026-08-14; anything closed before that reads `unrecorded`. An empty split early on means the
  history is not there yet, not that the fleet was perfect — say which one you are looking at.
