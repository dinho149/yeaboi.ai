# day ahead

**Trigger** — cron `45 5 * * *` (daily 05:45 UTC, fifteen minutes before the earliest sweep)
**Summary** — the day's schedule, posted before anything else runs
**Workstream** — none; this routine reports on the whole fleet.
**Model** — `fast` ([models.md](../../models.md))

The fleet's answer to "what is going to happen today". Everything else in cowork is
retrospective or decision-seeking: the digest carries proposals three hours from now, the ship
notes carry what already merged. Nobody was saying what was *about* to run, and the schedule
itself only existed as nineteen cron expressions in a markdown table — `30 7 11,25 * *` is
written for a scheduler, not for somebody deciding whether to look at Slack this morning.

It runs before the earliest sweep on purpose. At 06:00 the security sweep starts; by the
digest's 08:15 the whole weekday run is over, which is why this could not be a section on the
digest.

## Run

1. `uv run python scripts/cowork_setup.py --agenda`.
2. Hand the `lines` array to `cowork-scribe` and post it as **one channel-level message** to
   `#yeaboi-claude` (`C0BMADQQN1Z`), the lines joined by newlines, nothing added.
3. Stop.

That is the whole run. It reads no issues, opens nothing, and reaches no tracker.

## Post exactly what you were given

`lines` is already the finished message: which routines fire, in local time with UTC in
brackets, the background window, and the seven-day tail. Every one of those came out of
`cron_times()` in `scripts/cowork_setup.py`, which is unit-tested against the fortnightly and
monthly cadences the table only claims in prose.

So:

- **Never re-order, re-word, or re-time anything.** Not the summaries, not the day names, not
  the bracketed UTC. If a line looks wrong, the finding is a bug in `cowork_setup.py` and the
  fix is a PR against it — not a correction typed into a Slack message where nobody will ever
  see that it was corrected.
- **Never read a cron expression yourself.** Reading `30 7 11,25 * *` correctly nineteen times
  out of twenty means telling somebody the wrong morning, once, with total confidence. That is
  the entire reason this routine posts a rendered string.
- **Add nothing.** No proposals, no findings, no health lines, no opinion about what today
  looks like. The digest owns all of that and posts three hours later.
- **Never edit a file, open an issue, or touch Linear.** It has no `Write` or `Edit` grant, and
  the one thing it may spawn is the scribe.
- **Never reformat it either.** The lines are finished Markdown — bold headings, four fixed section
  anchors, backticked times — in the dialect the connector actually reads, which is not Slack's
  mrkdwn. `agenda_lines()` owns that, and `tests/unit/test_cowork_setup.py` fails if it drifts back.
  Escaping a character or dropping an emoji on the way out is the same edit as re-timing a line: it
  changes what was tested into something that was not.

## Post every day

Including Sundays, when no sweep fires at all and the message is a short one. This is the one
routine in the fleet exempt from "nothing to do is a valid outcome, exit quietly", and
deliberately so: silence from a *findings* routine means it found nothing, which is
information. Silence from a *schedule* routine is ambiguous — nothing scheduled, or the
routine broke? — and a reminder you cannot rely on arriving is not a reminder. "No sweeps
today" is the fact being asked for.

## Stop conditions

- `--agenda` exits non-zero, or `lines` is empty: post nothing and report the failure in the
  run log. A wrong schedule is worse than no schedule, and this is the one failure a human
  notices by itself — the message simply is not there in the morning.
