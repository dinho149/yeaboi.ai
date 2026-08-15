# Calibration

**What each workstream keeps getting wrong, and what to do about it instead.**

Every scout reads its own section before surveying ([`sweep-procedure.md`](sweep-procedure.md),
step 1). That is the whole self-healing mechanism, and it is deliberately lighter than rewriting a
prompt: accumulated failure patterns become *context*, so a scout stops re-making a mistake without
anything editing the rules it runs under. It is also self-limiting by construction — this file can
only ever **add** constraints, never remove one.

Written by [`cron/retune.md`](routines/cron/retune.md) on Sundays, and by a human whenever they have
a reason. Appending a row is the auto lane; nothing here is ever deleted or rewritten unattended,
because a record that can be edited away is not a record.

This is the same job [`integrations-map.md`](integrations-map.md)'s **Recorded gaps** does for
providers: it turns a decision somebody already made into something nobody has to make twice.

## How a row is written

One table per workstream, under a `### <workstream>` heading. Sections appear when there is
something to say — **a workstream with no heading has nothing recorded, and that is the normal
state.**

```markdown
### tui-ux

| Recorded | The pattern | Evidence | Instead |
|---|---|---|---|
| 2026-08-16 | Proposes copy changes as `auto` because the fix looks one-line | #241, #248, #259 — all bounced `user-facing-wording` | Wording is `propose` however small the diff. The line is behaviour vs copy, not size. |
```

Four columns, and each earns its place:

- **Recorded** — the date, absolute. A pattern that stopped being true deserves a second row saying
  so, not a silent edit of the first.
- **The pattern** — what the workstream keeps doing, in the shape a scout can recognise itself in.
  Not "files bad proposals": *what kind*, and *why it looked right at the time*.
- **Evidence** — **issue, PR or marker numbers.** A row with no number behind it is a rumour that
  has learned to look like a fact, and this file is read by something that cannot tell the
  difference. Three occurrences is the floor for calling something a pattern; two is a coincidence
  with a witness.
- **Instead** — the thing to do differently, stated as a rule the scout can apply on the spot. If
  you cannot write this column, you have found an observation rather than a calibration, and it
  belongs in a proposal against the charter.

## What does not go here

- **A one-off.** A single rejection is the system working, not a pattern.
- **A charter re-aim.** "This workstream is pointed at the wrong thing" is the right conclusion
  sometimes, and it is a **proposal** against `workstreams/<name>.md` — deciding what a workstream
  is *for* is a human's call ([`workstreams/fleet.md`](workstreams/fleet.md), the tighten/loosen
  rule).
- **Anything that would make a scout find *more*.** This file only narrows.
- **Run telemetry.** Cost, duration and token counts live in the monthly `fleet-ledger` issue and
  are read by `make cowork-metrics` on a human's terminal. Nothing in the fleet reads that; this
  file is the one thing that is deliberately read back.

## Recorded patterns

*Nothing recorded yet.* The markers this file is written from —
`<!-- bounced: reason=… -->`, `<!-- rejected: reason=… -->` — started landing on 2026-08-14, and
the first rows will come from `cron/retune.md` once there are three of anything.
