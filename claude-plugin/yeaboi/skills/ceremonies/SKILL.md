---
name: ceremonies
description: "Inspect the team's scheduled yeaboi ceremonies — which modes run on a cadence, when, where the output lands, and what each run actually did (including the runs that were skipped and why). Use when the user asks whether their standup or report posted, why a scheduled run went quiet, or what yeaboi is set to run automatically."
---

# Scheduled ceremonies with yeaboi

A **ceremony** is one of yeaboi's modes declared to run on a cadence: the daily
standup at 09:00, the delivery report on Monday morning, the agent cost digest
weekly. The operating system fires it (launchd or crontab), so it happens whether
or not anyone opens yeaboi, and the output lands in the channels it was declared
with — terminal, desktop notification, Slack, email.

## Reading what is set up

1. **`ceremonies_list`** — every declared ceremony for the session, with its
   mode, cadence, channels, and how its last run went.

   Read `installed_jobs` alongside it. That field comes from the **operating
   system**, not the database, and the two can disagree:
   - declared but missing from `installed_jobs` → **it will not fire.** Tell the
     user to re-add it (`yeaboi ceremonies add …`).
   - in `installed_jobs` but not declared → an orphaned job from a ceremony that
     was deleted by hand.

   This gap is invisible until a morning goes quiet, so surface it rather than
   just listing what is declared.

2. **`ceremonies_history`** — what actually fired, newest first. Pass `ceremony`
   to narrow to one.

## Reading an outcome honestly

Four of the five outcomes are not failures, and saying "it failed" for any of
them is wrong:

| Outcome | What to tell the user |
|---|---|
| `ok` | It ran and was delivered. Check the `delivery` field — a run can succeed while a channel (a dead Slack webhook) does not. |
| `failed` | The mode itself errored. `error` has the reason; quote it. |
| `skipped_stale` | The job fired long after its slot — almost always a sleeping laptop. Nothing was posted **on purpose**: a five-hour-old standup misleads. |
| `skipped_over_cap` | This month's spend on that ceremony hit its `monthly_cap_usd`. Raising the cap is the fix, if they want it. |
| `skipped_paused` | A job fired for a paused ceremony — the declaration and the OS have drifted. Suggest pausing it again, which removes the job. |

## What this skill cannot do

**Declaring, editing, pausing or removing a ceremony is not available here, and
that is deliberate.** Each of those installs or removes a scheduled job on the
user's own machine — something that outlives the session, survives reboots, and
can spend money unattended. It belongs at the terminal that will run it:

```
yeaboi ceremonies modes                       # what can run on a cadence, and what cannot
yeaboi ceremonies add weekly-report --mode report --at 08:00 --weekdays 1 --channels slack
yeaboi ceremonies pause weekly-report
yeaboi ceremonies run weekly-report           # run it now, without waiting
```

Offer the exact command; never imply you have run it.

Not every mode can be scheduled — the retro and poker boards need people in a
room, and `ship` has a human approval gate. `yeaboi ceremonies modes` lists both
halves with the reason for each refusal.

## Cost

Each scheduled run makes the mode's own LLM calls, and the ledger records what
it cost. Costs are **estimates** from token counts and public rate tables, not a
bill. If the user is worried about unattended spend, point them at
`--monthly-cap`, which skips scheduled runs once the month's total is reached
(a manual `ceremonies run` is never capped).
