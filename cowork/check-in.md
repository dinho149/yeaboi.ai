# Check-in

How every run in the fleet closes, written once. Your routine supplies three facts; everything
else is measured here. It is the last thing a run does, and no routine is exempt.

The fleet ran twenty-four routines a day and reported nothing about its own running. On
2026-08-06 the security sweep died on `Authentication error` after one turn; nothing said so, in
Slack or anywhere else, and the next thing anybody knew was the following Thursday. That is not a
gap in the reporting — it is what the reporting was *for*: [README.md](README.md) makes silence
load-bearing, and silence is a fine answer to "did you find anything" and no answer at all to "are
you alive". Nothing carried what a run cost, either. `cron/agents-standup.md` reports what every
*other* agent spends and is forbidden from reporting its own, for good reason — it once claimed "1
session · ~$0.10" about itself while the machine it was describing held seventy-four sessions and
about $521.

So: one reply per run, in the thread under the day's 📅 message, never in the channel.

**Why a reply and not a message.** The channel budget is two to four a day, and it is the reason
the channel is read at all — a dozen check-ins in it would mute the thing they are reporting on.
Under 📅 they cost nothing and land somewhere better than a channel: `cron/day-ahead.md` already
posted, at 05:45, the list of what would run today. Each check-in closes out one of its lines. The
schedule becomes the ledger, and "did everything run" is answered by reading one thread.

## Run

1. **Compose it.** `.venv/bin/python scripts/cowork_checkin.py --line`, with the facts on stdin:

   ```json
   {"name": "security-sweep", "status": "ok", "note": "1 PR (#261), 2 proposals filed"}
   ```

   Four keys, and you supply three of them. `name` is your routine's name as
   [README.md](README.md)'s table spells it. `status` is `ok`, `degraded` or `failed` — see below.
   `note` is one clause on what happened, in the past tense, naming numbers and issues rather than
   describing effort: *"nothing to do"*, *"1 PR (#261), 2 proposals filed"*, *"digest posted"*,
   *"blocked at step 3"*. `url` is optional and you will not need it — the script finds this run's
   own log link by itself.

   Use `uv run` unless your run already built a venv, in which case
   `./.venv/bin/python` does the same thing and leaves `uv.lock` alone. Either is fine
   here and nowhere else: this is the last step, so a lockfile dirtied at this point
   cannot reach a commit that has already been made. What it must *not* be is a bare
   `python3` — `yeaboi` would not be importable, and the check-in would post with its
   token figure quietly missing, which is the one number nothing can recover later.

2. **Find the parent.** Read `#yeaboi-claude` (`C0BMADQQN1Z`) and take the `ts` of today's message
   whose text begins `📅 **Today** — `. That is the `thread_ts`.

3. **Post it, verbatim, as a thread reply.** Two lines, exactly what the script printed. Do not
   reword it, do not add a sign-off, do not reflow it, and do not "fix" it against what the channel
   looks like.

4. **Stop.** The check-in is the end of the run.

## Post exactly what you were given

`cron/day-ahead.md` set this rule and it holds here for the same reason: everything numeric in the
message is measured, and a number retyped by a model is a number nobody can diff. The script reads
this run's own transcript through `agentwatch.collector` — the same reader the product ships — and
prices it through `yeaboi.pricing`, so a check-in and `yeaboi agents cost` cannot disagree without
one of them being broken. If a figure looks wrong, the finding is a bug in `cowork_checkin.py` and
the fix is a PR against it, not a correction typed into Slack where nobody will see it was
corrected.

The result looks like this:

```
`07:00` **security-sweep** 🟢 4m · 1 PR (#261), 2 proposals filed
~263k tok ≈ $0.98 · [log](https://claude.ai/code/session_01DBM5LwdWwgpUydtanGHuAt)
```

- **The time is the run's start, in local time**, spelled exactly the way `--agenda` spells it,
  because the reply is read against the 📅 line it closes out.
- **🟢 / 🟡 / 🔴, never ✅ / ❌.** Those two are the human approval verbs and 🤖 is the relay's
  handled-marker; a check-in carrying one invites somebody to answer a heartbeat. The script strips
  all three out of `note` whatever you pass it.
- **`~` and `≈` are true and stay.** The transcript is still being written while it is read, so the
  closing turn and the check-in's own tokens are not in the total. It is a floor. That is said here,
  once, and never re-derived in a message.
- **The token figure is everything the run put through a model**, cache reads included — they are
  consumption, and omitting them would make the number disagree with the cost beside it.
- **Content never leaves.** Token counts, model ids and filenames only.

## Status

| | When |
|---|---|
| 🟢 `ok` | The run did what it exists to do, **including doing nothing**. A sweep that surveyed its charter and found nothing worth filing is a good run and reports `ok` with `nothing to do`. |
| 🟡 `degraded` | It finished, but something it needed was missing and it worked around it — a tool absent from the session, a query it could not make, a slot count it could not read. Say which in the `note`. |
| 🔴 `failed` | It stopped early on one of its own stop conditions. Say which. |

**A no-op still checks in.** That is the whole point: it is the only thing that separates "ran,
found nothing" from "never fired", and the fleet had no way to tell those apart. A quiet sweep
posts a green line and nothing else — no issue, no channel message, nothing changed about what it
files.

**One routine is exempt from that, and only partly.** `cron/slack-relay.md` polls seventeen times a
day. It checks in on its first fire and on any fire that acted or degraded; a quiet repeat fire
posts nothing. One line a morning proves the poller is alive, which is all a no-op check-in is for,
and sixteen repeats of it would bury the ones that mattered. The exemption is written into that
routine's own stop conditions, not here — nothing else in the fleet fires often enough to earn it.

## Stop conditions

- **No 📅 message yet** — post nothing, and say so in the run log. A check-in never falls back to a
  channel message; the placement is the whole reason this is affordable.

  This is not only a failure case. 📅 goes up at 05:45 UTC, and some runs are *earlier by design*:
  `cron/cd-deploy.md` fires at 04:00, its push webhook fires whenever somebody merges, and the three
  event routines fire whenever GitHub says so. Those runs have no thread to reply to and check in to
  the run log alone. That is why `cron/shipped-standup.md` counts a routine as a no-show only if it
  was due **after** 📅 — anything earlier is silent for a reason, and reporting it every morning
  would make the one section that names real faults the least trustworthy thing in the message.

  When 📅 is missing outright, that is the louder alarm anyway: `day-ahead` is the one routine in
  the fleet that may never stay silent.
- **No Slack tool in the session** — post nothing, and say so in the run log. Do not hunt for a
  credential and do not hand-roll a request.
- **`cowork_checkin.py` fails** — post nothing rather than an improvised line. A check-in nobody
  can trust is worse than a missing one, and `cron/shipped-standup.md` names the day's no-shows at
  18:00 either way.
- **Never post a second check-in for the same run**, however the run ends. One firing, one reply.
