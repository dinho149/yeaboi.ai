# fleet

**Owns** — `cowork/` **except the constitution: `cowork/house-rules.md`,
`cowork/definition-of-done.md`, `cowork/sweep-procedure.md`, `cowork/models.md` and
`cowork/crew.md`** — plus `scripts/cowork_*.py`, `scripts/hygiene_lens.py`, `scripts/tui_fuzz.py`,
`.github/hygiene/`, `tests/unit/test_cowork_*.py` **except the guards over the constitution:
`tests/unit/test_cowork_models.py` and `tests/unit/test_cowork_retune.py`** and
`tests/unit/test_hygiene_lens.py`

**Skills** — none; this charter's subject is `cowork/` itself

**Cadence** — Sun 08:00 UTC, weekly ([`cron/retune.md`](../routines/cron/retune.md)). Not a sweep —
it surveys the fleet's *outcomes* rather than a surface of code.

The sixteenth workstream, and the only one whose subject is the fleet. Every other charter asks
"is this code right"; this one asks **"was what the fleet filed last week worth filing"**. That
question has been reported since `cron/digest.md` was written and acted on by nothing.

## The rule everything here rests on

> **A routine may tighten itself unattended. Only a human may loosen it.**

An agent that can edit its own instructions can quietly lower its own bar, and no amount of care in
a prompt fixes that — so the asymmetry is structural rather than behavioural:

- **Tightening** — recording a rejection in `calibration.md` so it is never re-proposed; adding an
  exclusion to `.github/hygiene/lens-policy.yml` with the false positive that motivated it. Both can
  only ever make the fleet **quieter**, so there is no incentive gradient pointing at them and
  nothing to game. Auto lane.
- **Loosening or re-aiming** — raising a threshold, adding a focus area, changing what qualifies,
  deleting a lens, editing what a workstream is *for*. These change judgement or increase output.
  **Always proposes**, whatever the evidence behind them.

This is the same shape as house-rules' "behaviour may change, wording may not": one direction is
mechanical and needs no judgement, the other is judgement and needs a human.

## The constitution is never automated

`house-rules.md`, `definition-of-done.md`, `sweep-procedure.md`, `models.md`, `crew.md`, and the
three crew agents — `cowork-scout.md`, `cowork-scribe.md`, `cowork-builder.md` — are **human-only**. They are excluded from this
charter's `**Owns**` by name and `tests/unit/test_cowork_retune.py` asserts it against the resolved
paths rather than against the prose — a fleet that can edit the rules constraining it has no rules,
and that is not a property to leave in a sentence somebody could reword.

The exclusion is what makes the rest of `cowork/` safe to own. Everything the old prohibition in
`platform.md` was protecting — judgement, re-aiming, the rules themselves — still requires a human;
what changed is that recording *what already went wrong* no longer does.

## Auto lane, in practice

**Exactly two operations, both append-only, both onto files that exist to accumulate mistakes:**

1. a row in [`calibration.md`](../calibration.md) recording a rejection, a bounce or a stale close,
   with the issue number behind it;
2. an entry under `excludes:` in `.github/hygiene/lens-policy.yml`, quoting the false positive that
   motivated it in its `why:`.

**Everything else proposes.** A charter re-aim — which is the fix the digest's own calibration line
has been describing for months — is a proposal, because deciding what a workstream is *for* is
exactly the judgement a human owns. So is deleting a lens, changing a cadence, editing another
routine, and touching anything under `scripts/`.

## Standing concerns

- **The run ledger must never become an input.** `fleet-ledger` issues are written by
  `cowork_checkin.py --record` and read by `cowork_metrics.py` on a human's terminal. **No routine
  reads it**, because the moment one does, a run's behaviour depends on another run's state and the
  fleet's statelessness — "GitHub issues are the queue; there is no other shared state between
  runs" — is gone. A test asserts no file under `routines/` mentions it.
  [`calibration.md`](../calibration.md) is deliberately the opposite: it is *read by scouts*, which
  is why it is a separate, small, human-readable file rather than a second ledger.
- **A record with no issue number behind it is not a record.** Every calibration row cites the
  issue, PR or marker it came from. The value of the file is that nobody re-litigates a decision
  from scratch; a row nobody can check is a rumour that has learned to look like a fact.
- **Do not tune on one week.** A rejection rate that moves by one issue is noise, and the honest
  answer to "is this workstream pointed wrong" needs several cycles — most of these charters sweep
  fortnightly or monthly. Say "not enough signal yet" and stop; that is a good run.
- **A quiet week is the expected outcome.** This routine exists to catch a *pattern*, and patterns
  are rare. One that finds something to tighten every single Sunday is inventing work in the one
  place where inventing work compounds.

## Out of scope

The constitution above. `.claude/agents/` — `cowork-scout.md` holds the type vocabulary and the
ten-find cap, `cowork-builder.md` decides what an unattended build may touch, and `cowork-scribe.md`
is the only writer of outbound comms. Anything under `src/yeaboi/`
(**every other charter**). `.github/workflows/` (**platform**) and `.github/codeql/`
(**security**), including `codeql-triage.yml`, whose policy file is deliberately *not* this
charter's even though `lens-policy.yml` is: one is an unattended job's approval gate, the other is
a detector's false-positive list.

Nothing here may change a `type:` vocabulary, a lane, a cap, or a tier — those live in the
constitution by design.
