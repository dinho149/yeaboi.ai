# House rules

Binding on every cowork routine and every crew agent. Read this before your charter.

## The auto lane

Security, bugs and chores ship without asking. That is the point of the lane: the maintainer's
attention is a scarce resource and none of those three needs it. What replaces the approval is not
trust — it is a gate that a machine cannot open on its own (see **The gate** below).

A find may skip the proposal step and go straight to a PR only when **every** condition holds:

- confined to the paths your charter declares
- no public API, schema, or state-field change
- no prompt change (`src/yeaboi/prompts/`)
- no new capability — i.e. it needs no `test_surface_parity.py` registry edit
- no change to user-facing wording or labels

That last one is the line, and it is narrower than it looks: **behaviour may change, copy may
not.** Correcting a wrong total, guarding a crash, or wiring up a button that does nothing are all
inside the lane even though a user would notice. Rewording the label above that total is not. The
distinction is that behaviour has a right answer the tests can hold, and wording is a matter of
taste — which is exactly the kind of judgement the proposal queue exists to put in front of a
human.

…**and** it falls in one of these categories, which is the complete list:

1. security patch to a known guardrail gap, or a `pip-audit` CVE — see the carve-out below
2. a **bug**, with a regression test that fails before the fix and passes after — see below
3. a flaky or outright broken test
4. dead code removal, or a refactor with no behaviour delta
5. documentation drift (docs that contradict the code)
6. lint / type-annotation cleanup
7. a CodeQL alert whose rule id is on the `auto` list in
   [`.github/codeql/triage-policy.yml`](../.github/codeql/triage-policy.yml) — belongs to
   `codeql-triage.yml`, not to a routine; see the carve-out below

**A bug enters the lane on a failing test, not on an argument.** Write the regression test first,
run it against unfixed `main` and watch it fail, then fix and watch it pass, and paste both runs
into the PR body. A bug you cannot reproduce that way is one whose shape you do not yet understand
well enough to fix unwatched — file it as a proposal. This is the whole admission ticket for
category 2, and it is what makes "is this really a bug?" a mechanical question instead of a
judgement.

**Everything else proposes.** If you are arguing with yourself about whether something qualifies,
it does not.

**A sweep cannot propose a `feature` or an `improvement` either — it cannot produce one at all.**
`cowork-scout.md`'s type vocabulary is four words wide (`bug`, `chore`, `docs`, `security`) and
those two are not in it. Capability work has exactly one home, **the campaign lane** below, where a
human approves a *provider* rather than a find. The `type:feature` and `type:improvement` labels
still exist on the repo, because `src/yeaboi/feedback.py` files in-app user feedback under the same
vocabulary; no routine may apply them. A surface that plainly lacks something is not a find.

**Routine dependency bumps are not on this list**, though they look like they belong. Dependabot
opens those PRs and `dependabot-auto.yml` verifies and merges them; a builder bumping the same
dependency in its own branch produces a second PR that conflicts with the first. Nothing to
consolidate — the producer is a GitHub service, not a workflow. Leave the bump; a *usage* fix the
bump requires is ordinary work and rides category 2 like any other bug.

**A `pip-audit` CVE is the one exception**, because waiting for Dependabot's next scheduled run is
not an acceptable posture on a known exploitable version. It stays auto-lane, with one precondition:
check `gh pr list --author "app/dependabot" --state open` first, and if a PR already bumps that
dependency, **drive that PR instead of opening your own**. Two PRs raising the same pin is the exact
collision the paragraph above exists to prevent, and a CVE is not a reason to cause it.

**CodeQL alerts belong to a workflow, not a routine, and category 7 takes three exemptions from the
guardrails below.** They are listed here rather than assumed, because each one is a rule this file
otherwise enforces:

- *One coherent change per run* — a batch of same-rule mechanical fixes **is** one coherent change.
  Twenty-five `uses:` lines pinned to their current SHAs is one review, read once; splitting it
  across twenty-five runs is twenty-five reviews of the same decision. The batch is capped
  (`max_batch` in the policy file) and anything over the cap is reported by number, never dropped
  silently.
- *One open PR per workstream* — the triage PR does not count against `security`'s. It carries its
  own single-PR guard: the survey step exits early when a `security/codeql-triage` branch already
  has one open, so the mechanism is preserved, just not shared with the sweep's.
- *Stay in your paths* — alerts land wherever the code is, so a per-charter boundary would leave
  most of them unowned by anyone. What bounds the risk instead is the **rule id**: only rules whose
  fix is mechanical, local and provably behaviour-preserving are on the `auto` list, and the policy
  file records the prescribed fix for each.

None of that removes a gate: the triage PR goes through **The gate** below like everything else,
and merges via `gh pr merge --auto` so the ruleset is what decides. A wrong fix does not merge; it
sits red.

## The campaign lane

One workstream builds rather than maintains, and it needs its own lane because the auto lane above
forbids everything it does. **integrations** runs a *campaign*: one provider — a tracker, a
documentation tool, a code host, or an ops system the agent can scan — built across every angle in
one week, and wired into every mode that has a question it answers. The procedure is
[`integration-campaign.md`](integration-campaign.md).

**What approves it is the provider, not the change.** Monday's shortlist reaches you through the
digest as three `integration:candidate` issues; a ✅ on one applies `integration:approved`, and the
week's work then ships unattended. That is the whole human step, and it is deliberately upstream:
approving each PR of a week-long build is three approvals a week for decisions you already made when
you picked the provider.

**It is exempt from exactly four auto-lane conditions**, each because the provider approval already
covered it:

1. **no new capability** — a provider *is* the capability; the campaign registers it in
   `test_surface_parity.py` like any other feature;
2. **no user-facing wording** — a wizard step and a settings section are wording, and there is no
   way to connect a provider without them;
3. **no public API or config surface** — a credential getter in `config.py` is one;
4. **stay in your paths** — see **Extends** below.

**Everything else holds, unchanged.** An independent `code-reviewer` reads the diff before the PR
opens. `claude-review.yml` reviews it once CI is green. `scripts/pr_feedback.py` still refuses an
`<!-- addressed: … -->` marker from the PR's own author, so a campaign may fix a finding and never
dismiss one. One open PR at a time. The ruleset decides the merge. And nothing reaches a user on
merge: a campaign publishes a pre-release you hand-test against its own checklist before it is
promoted (`release-signoff.md`).

### Extends — appending a provider to another workstream's file

Wiring a provider into every mode means editing six other workstreams' files. A blanket grant would
be wrong, so the grant is by **site** and by **operation**: the campaign may *append a provider* at
the registration sites named in
[`workstreams/integrations.md`](workstreams/integrations.md)'s `**Extends**` paragraph, and may do
nothing else in those files, ever. Changing existing behaviour there is a proposal for the owner,
exactly as `**Reads**` always was.

Three things make that safe rather than a hole:

- **It is declared on both sides.** Each owning charter names integrations as an appender to that
  site. `tests/unit/test_cowork_setup.py` asserts the reciprocity, because a one-sided grant is one
  somebody deleted half of.
- **There is a collision guard.** Before opening a PR that touches an `Extends` path, run
  `gh pr list --label "workstream:<owner>" --state open` and `gh pr diff <n> --name-only`. If the
  owner has an open PR touching that file, take a different angle this run.
- **`src/yeaboi/ui/mode_select/__init__.py` is not on the list and never is.** That file — 14k LOC,
  the repo's worst merge surface — is the reason *Stay in your paths* exists, and the campaign never
  touches it. The grant is narrow enough to leave the rule's actual purpose intact.

**Two rules the campaign carries that no other lane needs.** Every provider today is a vendor SDK,
so a campaign adds a dependency to a published package, unattended: it may only add under
`[project.optional-dependencies]` behind a lazy import, never to `dependencies`, and the shortlist
issue names the package, its licence and its maintainer so the ✅ is an informed one. And a cassette
for a provider nobody has an account with tests the author's *belief* about the API — a closed loop
that goes green and means nothing — so a campaign's contract test cites the doc URL it was written
from and the map records it as never recorded live.

## The migration lane

A second workstream builds rather than maintains. **go-migration** executes one program:
rewrite the Python codebase in Go, as thirteen wave-PRs, each gated by byte parity —
[`cowork/migration/program.md`](migration/program.md) is the program of record. The auto lane
above forbids everything it does (a port is not on the seven-category list, and no scout
vocabulary spells it), so like the campaign it needs its own lane. The procedure is
[`routines/cron/go-migration-campaign.md`](routines/cron/go-migration-campaign.md).

**What approves it is the program, not the wave.** The merged program of record — its 13-row
table in §3 — is the standing approval, given once, by the human who committed it. No per-wave
✅, no proposal issue, no `claude-implement`: the decisions a wave asks were all made when the
program merged, and re-asking them thirteen times is the campaign lane's
three-approvals-a-week problem at triple the length. What replaces the per-wave approval is
the gate: a wave PR — the campaign's `cowork/migration-w<N>` branch, labelled
`workstream:go-migration`; both halves, because the label alone also lands on this
workstream's ordinary maintenance PRs, whose diffs never schedule the Go jobs — cannot go
`pr-feedback`-green until the `Go core` and `Python ↔ Go parity` checks ran unskipped and
passed on its head commit (`scripts/pr_feedback.py` enforces it), on top of everything
**The gate** below already requires.

**The safety asymmetry**, same shape as fleet's *tighten unattended, loosen by hand*:

- **Unattended** — mechanical porting behind a green byte-parity gate; flipping the wave's own
  status checkbox in the program doc, in the wave's own PR; appending the wave's
  `## PR N — Wave X` spec section (drafting is planning inside the approved program); the
  freeze-table entry for the previous wave; the lockstep bumps at the `**Extends**` sites the
  two charters declare.
- **Always a human** — changing a contract (`contracts/` beyond the additive method-per-wave
  the program describes); deleting a Python twin outside the program doc's plan; weakening,
  skipping, or re-scoping a parity gate; and altering the program doc itself — the 13-row
  table, the decisions, the conventions — beyond the two appends named above. Those propose,
  whatever the evidence behind them.

**Everything else holds, unchanged.** An independent `code-reviewer` reads the diff before the
PR opens. One open PR per workstream — a wave is many sessions, and the open PR is what
serialises them. `scripts/pr_feedback.py` still refuses an `<!-- addressed: … -->` marker from
the PR's own author. The ruleset decides the merge (`gh pr merge --auto`, armed only after
probing that `pr-feedback` is still required). Wave PRs carry `cowork`,
`workstream:go-migration` and `type:chore` — a port with no observable behaviour change is the
one thing `chore` names exactly — and W19, the wave that *does* change what users install, is
flagged in the program doc as the wave a human drives. One deviation from "nothing ships on
merge" is deliberate and bounded: a wave that bumps `binaryVersion` publishes a **final**
`yeaboi-core` wheel on merge (`publish-core.yml` is version-triggered), but that wheel reaches
only the opt-in `[core]` extra of a sidecar behind an always-complete Python fallback — the
product's own release path, pre-release per merge and human promotion weekly, is untouched
until W19.

## The gate

The lane is wide; what keeps it safe is the merge path, and none of it is discretionary.

- **`make test` and `make lint` pass before the commit** — `make test-fast` is not enough at PR
  time. The full contract is in [definition-of-done.md](definition-of-done.md), items 2–7.
- **An independent `code-reviewer` reads the diff** before the PR opens. The builder never reviews
  its own work.
- **`claude-review.yml` reviews the PR** once CI is green, and the `pr-feedback` status counts what
  it found.
- **A machine may fix a finding; it may never dismiss one.** `scripts/pr_feedback.py` refuses an
  `<!-- addressed: … -->` marker from the PR's own author on an unattended PR, and refuses a
  `feedback-override` label applied by that same author — the override is the stronger lever, since
  it clears every finding, every unresolved thread and a requested-changes review at once, and
  `gh pr edit --add-label` sits inside the sweeps' own grant. So the only way to clear a finding is
  to push a fix and let the reviewer report `open=0` itself. A finding you disagree
  with ends the auto lane for that find: file it as a proposal and let a human answer. Before this
  was enforced, the routine that opened the PR could also declare the review of it answered — the
  applicant holding the key.
- **The merge waits on the ruleset**, which decides, not on any routine. `pr-feedback` must be a
  required status check on the `main-branch` ruleset for that sentence to be true; it is a manual
  setup step. Every workflow that would arm `--auto` checks for it first and refuses when it is
  absent, rather than merging on CI alone and calling it reviewed.
- **Nothing the fleet merges ships to users on merge.** An *unattended* merge to `main` — this
  lane — publishes a PyPI *pre-release*; `pip install yeaboi` is unaffected. The accumulated batch
  becomes an official version only when a human promotes it — see
  [`routines/cron/release-promote-ask.md`](routines/cron/release-promote-ask.md).
  `scripts/release_lane.py` is what draws the line, over this file's own label and branch prefixes.
  Note what it does not buy: a human merging anything cuts an official release from `main`, which
  carries whatever the fleet merged below it. The backstop is against the fleet shipping *by
  itself*, not against its work reaching users — a wrong fix that survives everything above still
  has to get past a
  person who has been running it.

## Merges are already bounded — do not throttle the auto lane

One `auto` find per run and one open PR per workstream (see Guardrails) together cap a heavy
weekday at roughly eight merges and a typical one at two to four. That is the intended volume, and
it is bounded by structure rather than by a counter someone has to tune. A new limit on top would
mostly express nervousness, and the honest answer to nervousness here is the gate above.

**That sentence was about merges, and it was read as being about everything.** The propose lane had
no bound at all: a scout returns up to ten finds, the auto lane consumes at most one, so a single
sweep could open nine issues, and seventeen workstreams run on overlapping crons. Nothing looked at
how many were already open — only whether *this* find restated one. The queue drained on a
fortnightly clock instead of on anybody deciding anything, and the digest, whose whole job is to put
a short list in front of a human, had forty-one items behind it. Nine issues filed in one morning is
not throughput. It is a queue nobody can clear, and the section below is the bound it never had.

## The proposal cap

**Two open `cowork:proposal` issues per workstream** (`PROPOSAL_CAP = 2` in
`scripts/cowork_setup.py`, which is where the number actually lives). A run may fill whatever slots remain with its
highest-ranked `propose` finds and files nothing beyond that. Answer one — approve it with
`claude-implement`, or close it — and the slot reopens on the next sweep.

**A `cowork:queued` issue holds no slot.** It is work waiting on the fleet, not a question waiting
on a human, so counting it would mean a work item occupying a slot that only a human verb could
release — on an issue no human is ever going to be shown. `--proposal-slots` filters it out and
reports the depth separately as `queued`. See **The queue** below.

Do not count them by eye. `uv run python scripts/cowork_setup.py --proposal-slots <workstream>`
returns the number, the same way `--triggers` returns the reconcile plan: a model asked to count
seventeen queues will eventually miscount one, and nothing downstream would notice.

Three consequences, all deliberate:

- **A held find is dropped, not deferred.** No issue, no comment, no Slack, no note anywhere. It is
  not lost — the next sweep surveys the same surface and re-ranks, so a find that still matters
  comes back, and one that stopped mattering does not. There is no shared state between runs and
  this does not invent any.
- **A full queue is a quiet outcome, not an abort.** It reads exactly like the one-open-PR guard:
  the run did its work, there was nowhere to put it, and it exits saying nothing. `cron/digest.md`
  reports which workstreams are held and which issues are holding them, so the silence is legible
  in one place rather than seventeen.
- **An unreadable count is zero slots, never two.** `--proposal-slots` answers `slots: null` when
  the query failed rather than guessing, and a failed query is never spoken as a clean answer —
  the same rule `cron/digest.md` applies to a PR it could not read.

## The queue

`cowork:proposal` is a **question waiting on a human**. `cowork:queued` is a **work item waiting on
the fleet** — a find already covered by the auto-lane allowlist above, so there is nobody to ask.
Same write-up, same everything else; what differs is who answers it.

The two are **mutually exclusive**, and that is load-bearing rather than tidy. Every consumer in the
repo asks GitHub for `labels=cowork:proposal,…`, and that parameter is AND-only — there is no way
to spell "and not queued". Exclusivity is what keeps `cron/digest.md`'s three queries,
`codeql-triage.yml` and `flaky-test-hunter.yml` all correct without a single edit.

Four rules, and they are the whole contract:

- **Only a sweep's step 4 puts an issue in the queue**, one at a time, having read it, and only for
  a find it classified `auto` itself that run. Not a scout (read-only), not the scribe at filing
  time, not the relay, not `claude.yml`. The one-time backfill of an existing backlog is
  `scripts/cowork_setup.py --migrate-proposals`, which a human runs and which refuses to run
  unattended.
- **The queue is drain-only.** Nothing *files* into it — a passed-over `auto` find is still dropped
  (**A held find is dropped, not deferred**, above), so the queue is seeded from issues that
  already exist and thereafter only shrinks. That is what stops it becoming a second unbounded
  backlog behind the first one.
- **Being queued grants nothing.** It records that a rule already covered this find; it is not an
  approval and not a review. Step 5 re-checks the full allowlist before building, bounces what
  fails back to `cowork:proposal` with the failing condition named, and closes what no longer
  reproduces. A wrongly-queued item costs one comment. Nothing downstream trusts the label, which
  is exactly why a mechanical backfill is safe.
- **A `codeql:` issue is never queued.** `codeql-triage.yml` files one only for a rule whose
  `propose` entry in `.github/codeql/triage-policy.yml` records why a human must decide it — and it
  dedupes by searching `--label cowork:proposal` for the rule id, so an issue moved to
  `cowork:queued` is one that workflow can no longer see. Next week's run then opens a second
  **public** issue re-asking a question already answered. This is the one consumer for which the
  two labels being exclusive is a hazard rather than a convenience, so it is carved out in both
  places work enters the queue: `sweep-procedure.md` step 4, and `--migrate-proposals`.
- **A merge closes a queue entry, and nothing else does.** `Closes #<n>` in the PR body. In
  particular `cron/digest.md` must never age one out: both dedupe passes read a closing as a
  human's rejection, so closing a queued item would destroy the write-up *and* suppress the find
  permanently.

**One bounded batch, on the precedent already set.** CodeQL triage is exempted from *One coherent
change per run* on the reasoning that a batch of same-rule mechanical fixes **is** one coherent
change. The same grant applies here, tightly: a sweep may put **at most three queued items in one
PR** when all three carry the same `type:`, that type is `docs` or `chore`, and every path is inside
the charter's `Owns`. Never `bug`, never `security`, and never a mix of types — those two carry a
regression test and a disclosure judgement respectively, and neither survives being read as part of
a batch. The PR body lists every issue number with its own `Closes #<n>`.

A routine may apply `cowork:queued`. It may never apply `claude-implement` — which is why this had
to be its own label rather than a reuse of that one. `claude.yml` fires an unattended 110-turn
implement job on anything receiving `claude-implement`, and the closed list of labels a machine may
apply must not widen just so a sweep can pick up its own backlog.

## Critical

One find in a hundred cannot wait for a slot. `critical: true` on a scout's find bypasses the cap.
It is permitted **only** when the find is one of these four, and this list is closed:

1. an exploitable vulnerability, or a secret exposed in the repo or in a published artifact
2. data loss or database corruption
3. `main` crashing, or the published package failing to install
4. a safety gate that has silently stopped working — CI not running, `pr-feedback` no longer
   required, a guard that no longer guards

Everything else is `critical: false`, including every find whose worst outcome is that a user is
annoyed. This is a new axis and not a louder `impact`: `impact` is a 1–5 score the scout gives
itself, and the moment a self-assigned score became the key to a gate it would start climbing. The
four cases above are questions of fact, in the same way "name the regression test" is a question of
fact rather than a matter of conviction.

**It bypasses the cap and nothing else.** A critical find still dedupes against the open queue,
still respects `Owns` versus `Reads`, still proposes if it is not on the auto-lane allowlist, and
still goes through the gate. It is a reason to jump a queue, never a reason to skip a check.

One collision, because it is the case where the two rules point opposite ways: a critical
**security** find that would require disclosure — an exploitable path in a shipped release — takes
the carve-out in [`routines/cron/security-sweep.md`](routines/cron/security-sweep.md) and goes to
the Linear ticket and `#yeaboi-claude`, **never** to a public GitHub issue. The cap bypass must not
become the route by which an exploit gets published.

## Guardrails

- **One open PR per workstream.** Start every run with
  `gh pr list --label "workstream:<name>" --state open`. If one is open, drive *that* PR to green
  and stop. Do not open a second.
- **Stay in your paths.** A find outside your charter's paths becomes a proposal issue labelled for
  the owning workstream. Never edit another workstream's files — this is what keeps two routines off
  `src/yeaboi/ui/mode_select/__init__.py` (14k LOC, the repo's worst merge surface). The single
  exception is a campaign run appending a provider at an `**Extends**` site — bounded by site and by
  operation, declared on both sides, and never on that file. See **The campaign lane**.
- **Never apply `feedback-override`.** It is the escape hatch for a gate that has genuinely gone
  wrong — a review that errored, a producer that changed format — and it is a human's call, recorded
  on the PR. A routine applying it to its own PR is the applicant clearing its own review with the
  largest lever available, and `pr_feedback.py` now refuses exactly that; this rule is here so the
  closed allowlist covers the label rather than leaving it to the code alone.
- **Never apply `claude-implement`.** Only a human does. That label is the approval gate; a routine
  applying it would be approving its own work. The one carve-out is `routines/cron/slack-relay.md`,
  which applies the label (or closes an issue) **only** as the relay of a ✅/❌ reaction from a human
  on its written allowlist, and records who and the message link on the issue. The human is
  approving; the relay is transport — it never applies the label to anything no allowlisted human
  reacted to.
- **Never push to `main`, never `--force`, and never merge a PR yourself.** `gh pr merge --auto`
  is not a merge — it hands the decision to the ruleset, which merges only once every required
  check is green. Arming it is allowed; clicking merge, bypassing a check, or pushing to `main`
  is not, and no widening of the auto lane changes that.
- **One coherent change per run.** No grab-bags.
- **Nothing to do is a valid, common outcome.** Exit quietly — no issue, no channel message. A
  routine that always finds something is a routine inventing work. It still checks in
  ([check-in.md](check-in.md)): a 🟢 thread reply saying `nothing to do` is the only thing that
  separates a quiet run from one that never fired, and the fleet could not tell those apart at all.
- **Label every PR** `cowork`, `workstream:<name>`, and the find's `type:<type>` — the daily
  standup takes its `[type]` tag from the PR's labels. The `cowork` label is also what guarantees
  `claude-review.yml` reviews it: that workflow skips `dependabot[bot]` and `github-actions[bot]`,
  and an unattended job's PR may carry one of those authors. A truncated run that pushes without
  labelling no longer goes unreviewed — both that workflow and `scripts/pr_feedback.py` also
  recognise the `cowork/` branch namespace — but it does ship untagged in the standup, so label it.

## Verification is not optional

`make test` and `make lint` must pass before you commit — `make test-fast` is not enough at PR time.
The full contract is in [definition-of-done.md](definition-of-done.md).
