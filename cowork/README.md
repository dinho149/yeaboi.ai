# cowork

yeaboi run as standing engineering teams. Each workstream scouts its own area on a schedule,
**proposes** high-impact work for a human to choose from, and ships approved work against one shared
[Definition of Done](definition-of-done.md).

## The loop

```
scout routine (per workstream, own cron)
      │
      ├─ auto lane ─── builder ─── PR ─── ruleset merges it ──┐
      │  (narrow allowlist — see house-rules.md)              │
      │        ▲                                              │
      │        └── the `cowork:queued` queue: an issue a rule │
      │            already covered, so nobody is asked. Built │
      │            one per run, oldest first; a merge closes  │
      │            it. Drain-only — nothing files into it.    │
      │                                               ┌───────┘
      └─ propose lane ─ scribe files a GitHub issue   │
                        `cowork:proposal` + `workstream:X`
                        (no Linear ticket yet)        │
                              │                       │
        daily digest routine buckets open proposals   │
        by type → top 3 each → ONE Slack post         │
                              │                       │
     YOU add `claude-implement` (on GitHub, or ✅ on the digest
     thread — `cron/slack-relay.md`), or close / ❌ to reject
                              │                       │
        .github/workflows/claude.yml `implement` job ─┘
        Linear ticket → build → code-reviewer → PR ───▶
                                                       │
                    pr-merged-close-loop ──────────────┘
                    merge closes Linear via `Closes YEA-NN`;
                    scribe: verify Done, Notion page; the standup reports it
```

A proposal is a question, so it costs one GitHub issue and nothing else. The Linear ticket opens when
the answer is yes — at approval for the propose lane, before the builder starts for the auto lane.

Unapproved proposals are closed by the digest routine after 14 days, and closing one yourself is how
you say no sooner. GitHub issues *are* the queue — there is no other shared state between routine
runs.

**A proposal is a question; a `cowork:queued` issue is not.** The two labels are mutually exclusive,
and which one an issue carries decides who answers it — a human, or the next sweep. Only a sweep
moves an issue between them, one at a time, having read it; the one-time backfill of an existing
backlog is `scripts/cowork_setup.py --migrate-proposals`, which no routine can run. Being queued
grants nothing: the sweep re-checks the allowlist before building, and bounces what fails.
See [house-rules.md](house-rules.md), **The queue**.

**The queue is depth-bounded: two open proposals per workstream.** A sweep fills whatever slots are
free with its best finds and drops the rest — silently, and losslessly, because the next sweep
surveys the same surface and re-ranks. Answering one reopens a slot, which is the only thing that
does. The exception is a `critical` find — an exploitable vulnerability, data loss, a broken `main`,
or a safety gate that stopped working — which is filed whatever the count says. Before this, one
sweep could file nine issues in a morning and the fleet's queue ran to forty-one; the digest that
exists to put a short list in front of a human had a backlog behind it that nobody could clear. The
rule is in [house-rules.md](house-rules.md); the arithmetic is
`scripts/cowork_setup.py --proposal-slots`, never a routine counting by eye.

## What arrives in Slack, and what does not

Everything the fleet says goes to one channel, `#yeaboi-claude`. **Steady state is three to six
channel messages a day**, worst case about eight, plus thread replies. Three of them always arrive
— 📅, 🧭 and the daily 🐹 — and the rest are exceptions reporting themselves. **The evening post
is not one of the three**: an area that did nothing says nothing, which is why the fan-out costs
so much less than one message per area per day would. Every message opens with a title line
carrying a fixed emoji, so a message is identifiable from its notification preview before it is
opened.

**Every message about the work is about one area.** When a workstream ships, opens or gets stuck,
that arrives as a message naming that workstream in its title line and containing nothing else.
That was not true until 2026-08-16: the evening post was one roll-up grouped by *type*, so a fix in
`analysis/` arrived as a `[bug]` line between a go-migration wave and a platform chore, and the
twelve areas with no other voice had no voice at all.

**The messages that are not about the work are not about an area, and could not be.** 🗳️ spans all
seventeen because it is the daily digest and the one message that *asks* rather than tells — a
reader answering it wants everything waiting on them in one place. 📅 is a schedule, 🩺 reports the
routines that never ran (a no-show has no area), and 🏷️/🎉/🚀/🚨 are about the release and the fleet
itself. The rule is not "one area per message" but **one subject per message**, and for anything a
workstream did, the subject is that workstream.

**Every run also checks in, and the budget above is unchanged, because a check-in is a reply.**
Each routine closes with two lines under that morning's 📅 message — worked or not, what it did,
what it spent, and a link to its log ([check-in.md](check-in.md)). That is eight to a dozen replies
on a weekday: seven or eight timed runs, whatever GitHub events fire, and one each from the two
routines that do *not* check in on every fire. `slack-relay` polls seventeen times a day, so it
reports on its first fire and on any fire that acted — sixteen more lines saying "nothing to relay"
would bury the two that mattered, and prove nothing the first did not. `cd-deploy` is woken by a
push to *any* branch, which on a busy afternoon is once a minute; it passes `--quiet-repeat`, so
the first check-in of the day at a given status posts and the rest record to the ledger silently.
Both exemptions buy the same thing: a heartbeat is worth one line a day, and the count of firings
belongs in `make cowork-metrics`, which reads the ledger.

Any of those in the channel would mute the channel; under 📅 they cost a reader nothing and land
where they mean the most, since 📅 already listed what would run today and each reply closes out one
of its lines.

| When | What arrives | Stays silent when |
|---|---|---|
| Daily 05:45 UTC | 📅 **Today** — what runs today and when, in local time | never — a schedule that goes quiet is a schedule you cannot trust |
| Weekdays 06:15 UTC | 🧭 **Agents** — what the AI agents shipped, spent and left open | never — a quiet day still posts one line |
| Daily 08:15 UTC | 🗳️ **Decisions** — proposals waiting on your ✅/❌, in its thread, plus ⏸️ **Held** and 🛠️ **Queued** — what the fleet owes you, which asks nothing | nothing is waiting, and neither fault fired |
| Daily 18:00 UTC | **one message per area that moved today** — what merged there, what proved it, what is building, what is stuck | that area merged nothing, opened nothing and got stuck on nothing today |
| Daily 18:00 UTC | 🩺 **Fleet health** — the routines that were due and never checked in | the schedule and the check-ins agree, which is most days |
| Mondays 09:00 UTC | 🏷️ **Release batch waiting** — the gate-green fleet PRs awaiting your `make batch-assemble` | nothing is waiting and no batch is open |
| A release is published | 🎉 **X.Y.Z is out** — what changed, PyPI and GitHub links | pre-releases never announce |
| A deploy reconciles the fleet | 🚀 **cd-deploy** — every field that changed | the plan was empty, which is most runs |
| A deploy is blocked | 🚨 **cd-deploy** — what is blocked, and the one thing you can do | the same cause already has an open `[blocked]` issue, which is every firing after the first |
| A disclosure-class security find | 🔐 **Security** — that one exists, its linked ticket, and the call it wants | rare by construction |
| Hourly 07:00–23:00 UTC | relay acks — **thread replies only, never the channel** | nothing to relay, which is the common case |
| Daily 17:00 UTC | 🐹 **Go Migration** — what landed, what is moving, and how to test it | never — a bar that only appears when it grows cannot be trusted |
| A migration wave merges | 🌊 **Go Migration** — the wave, the new bar, the core version shipped | non-wave merges say nothing here |
| The 13 maintenance sweeps | **nothing in the channel, ever** | always — a sweep files a GitHub issue and exits |
| The end of every run | a check-in — **thread reply under 📅 only** | it fired before 📅 went up (overnight merges, GitHub events), or it is `slack-relay` on a quiet repeat fire, or `cd-deploy` firing again the same day at the same status. Finding nothing is *not* one: that posts 🟢 `nothing to do`, which is the only thing that is not silence |

Three things follow from that table, and they are the whole design:

- **Silence is the default and it is load-bearing.** Every routine but three — 📅 at 05:45, 🧭 at
  06:15 and 🐹 at 17:00 — is allowed to say nothing, and most of them say nothing most days. A
  routine that reports every morning is a routine nobody reads by Thursday, and a muted channel is
  worse than no channel — the one day it matters, nobody looks. The three exceptions earn it by
  being the ones you *wait* for, and each is a report on a **standing** thing rather than a
  findings run: silence from a findings routine means it found nothing, while silence from a
  schedule, a daily agent digest or a progress bar is ambiguous — a bar that only appears when it
  grows cannot be trusted, which is the same argument the other two make.
- **Asking and telling never mix.** 🗳️ is the only message that wants something from you, and the
  only one whose thread does — 📅's thread is the day's run ledger and asks nothing, which is why
  check-ins carry none of the glyphs an answer is spelled with. ✅ and ❌ mean approve and reject, they are never decoration, and
  they work **on a thread reply** — a reaction on a parent message resolves to nothing, with one
  named exception: 🔐's disclosure post, which has no thread and no GitHub issue by construction,
  where ✅ applies `security:approved` in Linear and the next security sweep drains it. That is a
  lane, not a loophole — a top-level message can reach *that* verb and no other, so a `#<number>`
  posted at the top level is still not an approval of anything.
- **🤖 is not decoration either.** The relay reacts 🤖 onto a message to record that it is handled,
  and reads it back the same way. Reacting 🤖 to a digest item yourself hides that item from every
  future run.

## The area glyphs

One glyph per workstream, and it is the same glyph in every message that speaks for that area. This
table is the source of truth: `scripts/cowork_setup.py --glyphs` parses it, `make cowork-check`
fails if it and `workstreams/` disagree, and `.claude/agents/cowork-scribe.md` reads it rather than
choosing. The point is the notification preview — 🔬 tells you the message is about team analysis
before you open it, and a glyph that moved would silently retrain a reader who had learnt it.

| Glyph | Workstream | In a title line |
|---|---|---|
| 📋 | `planning` | Planning |
| 🌅 | `standup` | Standup |
| 🔬 | `analysis` | Analysis |
| 📈 | `reporting` | Reporting |
| 🃏 | `poker` | Poker |
| 🪞 | `retro` | Retro |
| 🎯 | `performance` | Performance |
| 🔭 | `roadmap` | Roadmap |
| 🧭 | `agents` | Agents |
| 🔗 | `artifacts-sharing` | Artifacts |
| 🐚 | `tui-ux` | Terminal UI |
| 🌐 | `web-ux` | Web UI |
| 🧰 | `platform` | Platform |
| 🦺 | `security` | Security |
| 🧩 | `integrations` | Integrations |
| 🐹 | `go-migration` | Go Migration |
| 🪛 | `fleet` | Fleet |

The third column is there because the slug is not a name: title-casing `tui-ux`
gives "Tui Ux", which is what a reader would have met in the channel every week.
It is the display name and nothing else — the *label* stays `workstream:tui-ux`,
and no code joins on the third column.

**Two are grandfathered, and that is the rule working rather than an exception to it.** 🧭 and 🐹
already led the title lines of `cron/agents-standup.md` and `cron/go-migration-daily.md` before
this table existed, and both of those messages speak for exactly the workstream the glyph now
names. So an area can post twice in a day under one glyph — 🧭 at 06:15 about what other agents
shipped, 🧭 in the evening about what the fleet changed in `agentwatch/` — and both are about
agents, which is what the glyph promises. The clause after the em-dash is what tells them apart.

**One carve-out: 🔐 is not security's area glyph.** It stays reserved for the disclosure lane, which
is an ALERT that wants a decision and is answerable with ✅ at the top level — the one message in
the fleet where that works. Security's area glyph is 🦺 — the guardrails, not the lock, because 🔐
is that disclosure and 🔒 heads the digest's Security *section*, and a routine TELL that looked like
either in a preview is the one confusion here that costs something.

Three rules bound the rest, and each one has already been paid for elsewhere:

- an area glyph may coincide with a **title-line** emoji only when that emoji already belongs to
  the same workstream, and may never coincide with a **section** emoji. That is why `integrations`
  is 🧩 rather than the obvious 🔌 — 🔌 heads the digest's Integration *section*, and a glyph meaning
  "area" on one line and "section" on the next means neither;
- **no variation sequences.** Every glyph here is a single codepoint with no trailing U+FE0F, the
  same rule `SECTION_EMOJI` follows in `scripts/cowork_setup.py` and for the same reason: a
  presentation selector one client honours and another drops is a title line that renders two ways.
  It is why `tui-ux` is 🐚 and not ⌨️, `platform` 🧰 and not ⚙️;
- **a glyph is never reused for a second meaning**, including against ✅ / ❌ / 🤖, which are verbs,
  and 🟢 / 🟡 / 🔴, which are check-in statuses.

## Where things live

| File | What it is |
|---|---|
| [definition-of-done.md](definition-of-done.md) | The ten-item contract. Binding on routines **and** on `/ship`. |
| [house-rules.md](house-rules.md) | Guardrails + the closed auto-lane allowlist. |
| [models.md](models.md) | The tier table. **The only file in `cowork/` that names a model.** |
| [sweep-procedure.md](sweep-procedure.md) | The shared cron run, written once. |
| [calibration.md](calibration.md) | What each workstream keeps getting wrong. Appended by `cron/retune.md`, **read by every scout before it surveys**. The only file the fleet writes for its own future runs. |
| [hygiene-lenses.md](hygiene-lenses.md) | The six standing detectors a sweep runs before scouting, each with a command behind it. Their exclusions live in `.github/hygiene/lens-policy.yml`; `crash-fuzz` is driven by `scripts/tui_fuzz.py`. |
| [check-in.md](check-in.md) | How every run closes: one thread reply under 📅, composed by `scripts/cowork_checkin.py` and posted verbatim. |
| [release-signoff.md](release-signoff.md) | The human ritual: assemble the batch, test the build, merge it. |
| [crew.md](crew.md) | scout / scribe / builder — who does what. |
| [integrations-map.md](integrations-map.md) | Which provider reaches which mode, and every deliberate gap. Maintained by the integrations sweep's reach week. |
| `workstreams/*.md` | Seventeen charters — fifteen over the code, **go-migration** over the Go rewrite program, plus **fleet** over `cowork/` itself: owned paths, standing concerns, what is out of scope. Every `CAPABILITIES` row maps to exactly one of the fifteen; ownership never overlaps. |
| `routines/cron/*.md` | One per scheduled routine. |
| `routines/events/*.md` | GitHub-event triggered. |

## How routines actually work

Cowork routines are **account-scoped, not repo files** — there is no `.claude/routines/` format. A
routine lives at [claude.ai/code/routines](https://claude.ai/code/routines) and stores its prompt,
repo, connectors, and triggers in the account. Repo-committed `CLAUDE.md`, `.claude/skills/`,
`.claude/agents/`, and `.claude/commands/` all carry into a routine run.

So the registered prompt is deliberately thin, and this folder is the real source of truth:

> You are the `<name>` workstream for yeaboi. Read `cowork/routines/<kind>/<file>.md` in this repo and
> follow it exactly.

Behaviour is changed by editing this folder in a PR — never by re-typing a prompt in a web form.

**What fires a routine** is a second, separate thing. A routine may carry a cron expression, or a
webhook trigger, or both. A webhook trigger is its own object (`create_webhook_trigger`), attached to
a routine and naming a source, an event list and a filter; the three `events/` routines and
`cron/cd-deploy.md` declare theirs in a ```json webhook block in their own file. Four properties of
that endpoint shape everything built on it, and none of them are guesses — see
`tests/fixtures/cowork_webhook_live.json`, captured from a real call:

- it **never reads back** — no response reports the webhooks attached to a routine, and a stored
  filter is not echoed even by the call that created it;
- it **does not dedup** — an identical POST creates a second webhook, and the routine then fires
  twice for every event;
- there is **no delete**;
- it **does not validate the event name** — `zzz_not_an_event` was accepted with a 200, so a typo
  registers a webhook that silently never fires. `WEBHOOK_EVENTS` in `scripts/cowork_setup.py` is the
  only thing that catches it, and `make cowork-check` is where you find out.

Together those mean a webhook can only be posted at the one moment its routine provably has none:
immediately after that routine was created. Everything else is reported as blocked and left alone,
which is the normal steady state and not a fault.

**And a merge to `main` deploys itself.** `cron/cd-deploy.md` is fired by a push webhook (with a daily
cron as the safety net) and runs the same reconcile `/cowork deploy` does, from `origin/main`. So
*editing* a routine is the whole workflow: merge it, and the fleet catches up within a minute.
One deliberate cost of the batch model rides here: a routine edit the *fleet* authors reaches
`main` only with the release batch, so it deploys weekly rather than on merge — pointing deploy at
unmerged fleet branches would let the fleet loosen its own rules past the sign-off
([workstreams/fleet.md](workstreams/fleet.md)). An urgent routine fix is a human's PR, which
merges and deploys immediately.

*Adding* one still needs the slash command. `cd-deploy` runs with `--no-create`, because two runs
fired seconds apart would both see the same routine missing and both register it — there is no lock,
there is no delete, and both copies would then fire. It reports them instead. The slash command is
also the only place a webhook can be wired, for the same reason: the one moment a routine provably
holds none is just after it was created.

## Adding a routine

1. Write `routines/cron/<name>.md` (or `routines/events/<name>.md`) — schedule, workstream, focus,
   stop conditions. Everything shared belongs in `sweep-procedure.md`, not here.

   An `events/` routine must also carry a ```json webhook``` block naming what fires it — without one
   it registers as a routine nothing ever wakes, and `make cowork-check` fails. Its `events` must be
   names from `WEBHOOK_EVENTS` in `scripts/cowork_setup.py`: the API accepts any string and fires on
   none of the ones it does not know, so a typo is only ever caught here.
2. Add or extend a charter in `workstreams/`.
3. Give it a tier in [models.md](models.md) — sweeps inherit theirs from `sweep-procedure.md`;
   anything that does its own model-worthy work needs a row.
4. Add the row to the table below — cron, workstream and tier. This is what gets registered; a
   routine file that is not in the table fails `make cowork-check`.
5. Merge it, then run `/cowork deploy`. Registering a *new* routine is the one step `cd-deploy` does
   not do unattended (see above) — it will report the routine as needing you. The command registers
   it from that row, wires its webhook if it declared one, and leaves the others alone. Editing an
   existing routine later needs none of this: merging is enough. (By hand it would be: claude.ai/code/routines, the thin prompt above, the Model dropdown set to the
   label `models.md` gives for that tier, and every connector removed except **Linear, Slack,
   Notion** — all connectors are attached by default.)
6. `/cowork run <name>` fires it once, so you find out whether it works now rather than on Thursday.

## Registered routines

`/cowork deploy` fills the URL column in as it registers each one, so this table is a record of what
is actually running rather than a promise. **Tier** is the Model dropdown — [models.md](models.md)
maps it to a label.

Cadence is tiered to surface size — a 1.2k-LOC mode asked for findings weekly will invent them.

**Weekly** (large surfaces)

| Routine | Cron (UTC) | Workstream | Tier | URL |
|---|---|---|---|---|
| `cron/security-sweep.md` | `0 6 * * 1,4` Mon + Thu | security | `deep` | https://claude.ai/code/routines/trig_015JVLHWzF8urG7nDq9J4wsN |
| `cron/planning-sweep.md` | `0 7 * * 1` Mon | planning | `standard` | https://claude.ai/code/routines/trig_01UR5H8AL5CzSydGMHCzD4aw |
| `cron/standup-sweep.md` | `30 6 * * 3` Wed | standup | `standard` | https://claude.ai/code/routines/trig_01BcyJ4pNnPVPcDok47L2f9N |
| `cron/tui-ux-sweep.md` | `0 7 * * 3` Wed | tui-ux | `standard` | https://claude.ai/code/routines/trig_01AxZUGZPkv86sbdxCuX2tep |
| `cron/analysis-sweep.md` | `30 6 * * 4` Thu | analysis | `standard` | https://claude.ai/code/routines/trig_01YPy18KkR5qApYcGBSzr2ZA |
| `cron/web-ux-sweep.md` | `0 7 * * 4` Thu | web-ux | `standard` | https://claude.ai/code/routines/trig_01BcNuqRENtmhTxsGJ4b3So3 |
| `cron/platform-sweep.md` | `0 7 * * 5` Fri | platform | `standard` | https://claude.ai/code/routines/trig_01SbJLSTm7PeFNc9HitzAZb2 |

**Fortnightly and monthly** (mid and small surfaces)

| Routine | Cron (UTC) | Workstream | Tier | URL |
|---|---|---|---|---|
| `cron/reporting-sweep.md` | `30 7 3,17 * *` | reporting | `standard` | https://claude.ai/code/routines/trig_01Xcu22S3aTXgGg51PDR3bGf |
| `cron/poker-sweep.md` | `30 7 4,18 * *` | poker | `standard` | https://claude.ai/code/routines/trig_01HgKz6NMa9MdTMna7z52Y9F |
| `cron/retro-sweep.md` | `30 7 5,19 * *` | retro | `standard` | https://claude.ai/code/routines/trig_01Jsz1yV9GdSAnzyr2NvCqL7 |
| `cron/performance-sweep.md` | `30 7 10,24 * *` | performance | `standard` | https://claude.ai/code/routines/trig_01VHQB9Tay45yyCZZPospPbY |
| `cron/artifacts-sharing-sweep.md` | `30 7 11,25 * *` | artifacts-sharing | `standard` | https://claude.ai/code/routines/trig_015ePKNpPQA489nHYYEC7PMp |
| `cron/roadmap-sweep.md` | `30 7 12 * *` | roadmap | `standard` | https://claude.ai/code/routines/trig_01KGntRSahscuEFg66ojoZTP |

**Cross-cutting and event-driven**

| Routine | Trigger | Workstream | Tier | URL |
|---|---|---|---|---|
| `cron/day-ahead.md` | `45 5 * * *` daily | — | `fast` | https://claude.ai/code/routines/trig_01EPy41L2yD7YLKzuRXVfxw1 |
| `cron/integrations-campaign.md` | `20 7 * * 1-5` weekdays | integrations | `deep` | https://claude.ai/code/routines/trig_019w6RJqz8aWJ13TkXmPUgtX |
| `cron/agents-standup.md` | `15 6 * * 1-5` weekdays | agents | `fast` | https://claude.ai/code/routines/trig_013tsooGjdnEMLRQcm7ZKU57 |
| `cron/shipped-standup.md` | `0 18 * * *` daily | — | `standard` | https://claude.ai/code/routines/trig_0118jEhPuaKrCaUWCYQtVgEv |
| `cron/digest.md` | `15 8 * * *` | — | `standard` | https://claude.ai/code/routines/trig_01VY1hbAZKeGuKA1GLyVhbow |
| `cron/slack-relay.md` | `0 7-23 * * *` hourly | — | `fast` | https://claude.ai/code/routines/trig_01X18LBBBZ1FWEtx2Cmffyow |
| `cron/release-promote-ask.md` | `0 9 * * 1` Mon | — | `fast` | https://claude.ai/code/routines/trig_01G4TuU1wYY7GXJ1cEXZUNSu |
| `cron/cd-deploy.md` | `0 4 * * *` daily + push (any branch) | — | `standard` | https://claude.ai/code/routines/trig_01AkW6ojpjKcra8H64R3Astr |
| `cron/retune.md` | `0 8 * * 0` Sun | fleet | `standard` | https://claude.ai/code/routines/trig_01KYYfRyy1kKCYXq8EFn6ac6 |
| `cron/go-migration-campaign.md` | `54 * * * *` hourly | go-migration | `heavy` | https://claude.ai/code/routines/trig_01M9VRz8rvNTusXLuxBwzwSB |
| `cron/go-migration-daily.md` | `0 17 * * *` daily | go-migration | `fast` | https://claude.ai/code/routines/trig_01A9NbWuCDoS137MH3u3scsn |
| `events/pr-opened-dod-audit.md` | PR opened / synchronized | — | `standard` | https://claude.ai/code/routines/trig_01Egz2NXy4GwzJzRRC7Z4Zm3 |
| `events/pr-merged-close-loop.md` | PR closed (merged) | — | `fast` | https://claude.ai/code/routines/trig_019gLyX5qWx7g5rXZkUKaDAo |
| `events/release-published-announce.md` | Release published | — | `standard` | https://claude.ai/code/routines/trig_01VXdR2FbPJUsMqVWghA7C5T |
| `events/go-migration-wave-merged.md` | PR closed (merged wave) | go-migration | `fast` | https://claude.ai/code/routines/trig_0158sWhFEhPhZy28nfMFu3iK |

> **Cron trap.** The fortnightly and monthly slots restrict **day-of-month only**. Standard cron
> *ORs* day-of-month with day-of-week when both are restricted, so `30 7 1-7,15-21 * 2` fires every
> day 1–7 *and* every Tuesday — a fortnightly routine silently running near-daily. Never restrict
> both fields.

## Detectors that are workflows, not routines

Two things the fleet needs are invisible to it. A scout reads files; these read **state that only
GitHub holds**, so they live in `.github/workflows/` and feed their output back into the same queue
rather than starting a second one. They take no row in the table above and no tier in `models.md`
(they read `vars.YEABOI_MODEL_*` directly, like every other workflow). Nor do they need
`/cowork deploy` at any point — `cd-deploy` reconciles routines on merge but still cannot *create*
one, whereas a workflow is only a repo file, so merging really is the whole deployment.

| Workflow | Reads | Writes |
|---|---|---|
| [`flaky-test-hunter.yml`](../.github/workflows/flaky-test-hunter.yml) | CI run history + repeated suite execution | `cowork:proposal` issues |
| [`codeql-triage.yml`](../.github/workflows/codeql-triage.yml) | the code-scanning alerts | **one PR** for allowlisted rules, proposal issues for the rest |

`codeql-triage` is the only unattended job in the fleet that writes code, so it is worth knowing what
holds it. It fixes only rule ids listed under `auto` in
[`.github/codeql/triage-policy.yml`](../.github/codeql/triage-policy.yml) — the test being "can CI
catch a wrong fix", not "is it low severity". It opens at most one PR at a time, gated on
`make test` + `make lint` + `make security` and a `code-reviewer` pass, and the PR then waits —
gate-green, `pr-feedback` answered — for the next release batch a human assembles and merges. It never dismisses an alert, and it
never applies `claude-implement`. The three guardrail exemptions it takes are written down in
[house-rules.md](house-rules.md), not assumed.

**The whole unattended lane needs one thing this repo does not have yet**, and it is one click:
`pr-feedback` on the ruleset's required checks, the manual step listed under *What neither can do*
below. Without it `--auto` merges as soon as the five CI contexts go green, with no review in the
loop — so every workflow that would arm auto-merge checks for the context first and leaves it
**disarmed** when it is absent, warning in the run log. Add the context once and the loop closes;
until then the PR opens green and waits for a click.

This matters more now than it did. Security, bugs and chores go straight to a PR
([house-rules.md](house-rules.md)), and the gate is what makes that safe rather than reckless: an
independent `code-reviewer` before the PR opens, `claude-review.yml` after CI, and
`scripts/pr_feedback.py` refusing an `<!-- addressed: … -->` marker from the PR's own author on an
unattended branch — so a machine may *fix* a finding and never *dismiss* one. Before that refusal
existed, the routine that opened a PR could also declare the review of it answered.

**And nothing the fleet builds merges at all until a human ships it.** Fleet PRs wait open,
gate-green, and reach `main` only inside the release batch a human assembles with
`make batch-assemble`, hand-tests, and merges ([release-signoff.md](release-signoff.md)).
[`cron/release-promote-ask.md`](routines/cron/release-promote-ask.md) is the Monday reminder that
work is waiting. That is the last backstop, and the only one involving somebody who has actually
been running the code.

## Setting this up

Two commands, in this order. Both are reconciles — safe to re-run after adding a routine, changing a
tier, or when someone new joins.

```bash
make cowork-setup   # GitHub labels + model repository variables
/cowork deploy      # in a Claude session: the routines, their webhooks + the Linear labels
```

Nothing is retyped. `scripts/cowork_setup.py` derives the labels from `workstreams/`, the variables
from [models.md](models.md#workflows), and the routines from the table above — so the schedule a
routine actually runs on is the one written down here. `tests/unit/test_cowork_setup.py` fails on the
same drift in `make test-fast`.

`make cowork-check` additionally probes the **`pr-feedback` required status check** on the
`main-branch` ruleset, because that one setting decides whether the auto lane merges anything
and the workflows that depend on it fail *quietly* — declining to arm `--auto` looks exactly
like a lane that had nothing to do. `cron/cd-deploy.md` reports the same probe on every merge to
`main` — from its apply step, not its check step, which is deliberately `--local` there — so
removing the context later is noticed rather than silently absorbed. No transport, or a
failed query, is reported as a note: an unanswerable question is not the same as a missing gate.

**How the script reaches GitHub.** `gh` when it is installed and authenticated; the REST API with
`GH_TOKEN`/`GITHUB_TOKEN` when it is not. The second path is not a convenience — a cloud routine
session is handed a token and no CLI, so `cron/cd-deploy.md` had no way to apply a label at all, and
its own stop condition turned that into a halted deploy on every firing. Only when *neither* answers
is it a degradation, and under `--strict` that is what exits non-zero.

**And a token is not the same as access.** That session's egress goes through a proxy with its own
allowlist, which was probed on 2026-08-11 and written down in
`tests/fixtures/cowork_github_access_live.json` — 15 of 19 operations served. Those four refusals,
plus a fifth met in the wild since and added to the probe rather than typed into the fixture, each
close off an approach that looks obvious:

| Refused there | Consequence |
|---|---|
| `POST /graphql` — *"only the pinned set of PR-review operations is served. Use REST … instead"* | Installing `gh` fixes nothing: `gh pr list --json` and `gh issue list --json` are GraphQL underneath, so the reads the sweeps and the digest are built on are exactly what would still fail. And `pr_feedback.py` cannot answer at all there — `reviewDecision` and thread resolution are v4-only — so it says so rather than reporting a PR it never read. |
| `GET`/`PATCH /repos/…/actions/variables` | The `YEABOI_MODEL_*` half of `cd-deploy` step 4 can never succeed in-session. It moved to `.github/workflows/cowork-repo-setup.yml`, where a runner has no proxy. |
| `POST /repos/…/statuses/{sha}` | The `pr-feedback` commit status can only be posted from CI — which is where `.github/workflows/pr-feedback.yml` already posts it. |
| Anything not repository-scoped — `GET /users/{owner}/repos` and its `/orgs/…`, `/search/…` neighbours — *"sessions are bound to their configured repositories"* (observed 2026-08-17, probed from the next re-derivation on) | A routine can read repositories it is told about and can never *discover* them. `cron/agents-standup.md` names a repository rather than an owner for exactly this reason; anything that expands an estate first belongs on a runner. |

The labels half, by contrast, is genuinely repaired by the REST path: `gh label list` is GraphQL and
was refused, while `GET /repos/{slug}/labels` is served. Re-derive any of this by re-running
`scripts/probe_github_access.py`; never edit the fixture by feel.

**What each command covers.** `make cowork-setup` does the thirty-six GitHub labels (`cowork`,
`cowork:proposal`, `cowork:queued`, `claude-implement`, `feedback-override`,
`release:promotion` marking the live batch PR, `semver:none` on every fleet PR so
`auto-version.yml` bumps only the batch, the `integration:candidate`/`integration:approved` pair the
campaign lane fires on, `workstream:<name>` for each of the seventeen, and the seven `type:*` labels
shared with the feedback system — of which a scout may emit only four) and the four
`YEABOI_MODEL_*` repository variables — the workflows read their model from a variable because a YAML
file cannot read a markdown table. `/cowork deploy` does all twenty-eight routines, the webhook triggers
that fire the event-driven ones, and mirrors the workstream labels onto the Linear `Yeaboi` team; both
need a Claude session, since a routine is account-scoped and has no CLI behind it.

**What neither can do**, and both report: connecting Linear/Slack/Notion at
[claude.ai/customize/connectors](https://claude.ai/customize/connectors), installing the Claude GitHub
App, setting the `AUTO_VERSION_PAT` secret, a **second PyPI trusted publisher** for
`publish-beta.yml` (workflow `publish-beta.yml`, environment `pypi`; without it every merge's
pre-release dies at upload with `invalid-publisher`, and the official channel stays untouched —
which is why that trigger lives in its own file), the **`pr-feedback` status context** on the
`main-branch` ruleset's required checks (DoD item 10; without it `.github/workflows/pr-feedback.yml`
still computes and posts a red status, and GitHub simply lets the PR merge anyway — see
`.claude/skills/ci-and-release/SKILL.md` for the `gh api` that verifies it) — and the **Linear GitHub
integration** on this repo (with issue-status automation on), which is what turns a PR body's
`Closes YEA-NN` into the attach and the Done-on-merge transition. If that integration is off, every ticket silently stalls at
In Review, and the only thing that would notice is `pr-merged-close-loop`.

## Running it afterwards

A fleet you can only start is a fleet you cannot correct. `/cowork` is the same reconcile with the
rest of the verbs on it:

| | |
|---|---|
| `/cowork status` | what is running, against what this folder says — drift, orphans, paused routines. Read-only. |
| `/cowork deploy` | register what is missing, update what has drifted, wire webhooks for what it created, fill the URL column. |
| `/cowork run <name>` | fire one routine now, instead of waiting for its cron. Not a dry run — it files issues and posts to Slack. |
| `/cowork pause` / `resume` | stop and restart the fleet without removing anything. |
| `/cowork teardown` | take it down (see below). |
| `/cowork today` | what runs today and over the next week, in one message shape. Read-only. |
| `make cowork-check` | the repo half of `status`, with no session needed. |
| `make cowork-slots` | how full each workstream's proposal queue is, and which issues are holding it. Read-only. |
| `make cowork-queue` | what each workstream's sweep should build next, in build order. The counterpart of `cowork-slots`: that one answers "may I file?", this one answers "what do I owe?". Read-only. |
| `make cowork-migrate` | one-off, human-only: reclassify auto-lane-eligible `cowork:proposal` issues as `cowork:queued`. Prints the plan and changes nothing without `YES=1`; refuses under `--strict`, so no routine can run it. |
| `make cowork-blocked` | whether a standing fault has already been reported, so a routine says it once. Read-only. |
| `make cowork-teardown` | the GitHub half of teardown, prompting first. |

Two things are worth knowing before you rely on any of it.

**A routine can be switched off but not deleted from a session.** The routines API has `list`, `get`,
`create`, `update` and `run` — and no delete. So `teardown` sets `enabled: false` and prints each
routine's URL for you to remove by hand. It says so rather than reporting a deletion that did not
happen; a fleet you believe is gone and is merely paused is worse than one you know is paused.

**`deploy` will not un-pause anything.** `enabled` is reported, never reconciled — otherwise a pause
would quietly end at the next deploy. `resume` is the only thing that turns the fleet back on, and
`status` names every paused routine on every run.

Teardown is tiered, because the pieces are not equally reversible: routines by default, `--labels` and
`--variables` opt in, `--all` adds the Linear labels. Deleting a `workstream:` label strips it off
every issue carrying it and nothing puts those back. Two labels are never deleted, and neither belongs
to cowork: `claude-implement` predates it and gates the `claude.yml` implement job, and
`feedback-override` is the only way past the `pr-feedback` merge gate — an escape hatch you have to
hand-create during the emergency it exists for is not an escape hatch. The `type:*` labels are never
deleted either — the feedback system shares them, and user-filed feedback issues carry them.
