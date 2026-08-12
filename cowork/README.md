# cowork

yeaboi run as standing engineering teams. Each workstream scouts its own area on a schedule,
**proposes** high-impact work for a human to choose from, and ships approved work against one shared
[Definition of Done](definition-of-done.md).

## The loop

```
scout routine (per workstream, own cron)
      │
      ├─ auto lane ─── builder ─── PR ─── you merge ──┐
      │  (narrow allowlist — see house-rules.md)      │
      │                                               │
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

Everything the fleet says goes to one channel, `#yeaboi-claude`. **Steady state is one to three
channel messages a day**, worst case about five, plus thread replies. Every message opens with a
title line carrying a fixed emoji, so a message is identifiable from its notification preview
before it is opened.

| When | What arrives | Stays silent when |
|---|---|---|
| Weekdays 06:15 UTC | 🧭 **Agents** — what the AI agents shipped, spent and left open | never — a quiet day still posts one line |
| Daily 08:15 UTC | 🗳️ **Decisions** — proposals waiting on your ✅/❌, in its thread, and ⏸️ **Held** — the workstreams at their proposal cap | nothing is waiting |
| Daily 18:00 UTC | 🚢 **Shipped** — what merged, what proved it, which pre-release | nothing shipped, building or stuck |
| Mondays 09:00 UTC | 🏷️ **Promote X.Y.Z?** — the weekly release ask | nothing is promotable |
| A release is published | 🎉 **X.Y.Z is out** — what changed, PyPI and GitHub links | pre-releases never announce |
| A deploy reconciles the fleet | 🚀 **cd-deploy** — every field that changed | the plan was empty, which is most runs |
| A deploy is blocked | 🚨 **cd-deploy** — what is blocked, and the one thing you can do | the same cause on the same commit already posted today |
| A disclosure-class security find | 🔐 **Security** — that one exists, and its ticket | rare by construction |
| Hourly 07:00–23:00 UTC | relay acks — **thread replies only, never the channel** | nothing to relay, which is the common case |
| The 13 maintenance sweeps | **nothing, ever** | always — a sweep files a GitHub issue and exits |

Three things follow from that table, and they are the whole design:

- **Silence is the default and it is load-bearing.** Every routine but the agents standup is
  allowed to say nothing, and most of them say nothing most days. A routine that reports every
  morning is a routine nobody reads by Thursday, and a muted channel is worse than no channel —
  the one day it matters, nobody looks.
- **Asking and telling never mix.** 🗳️ is the only message that wants something from you, and it
  is the only one with a thread. ✅ and ❌ mean approve and reject, they are never decoration, and
  they only work **on a thread reply** — a reaction on a parent message resolves to nothing.
- **🤖 is not decoration either.** The relay reacts 🤖 onto a message to record that it is handled,
  and reads it back the same way. Reacting 🤖 to a digest item yourself hides that item from every
  future run.

## Where things live

| File | What it is |
|---|---|
| [definition-of-done.md](definition-of-done.md) | The ten-item contract. Binding on routines **and** on `/ship`. |
| [house-rules.md](house-rules.md) | Guardrails + the closed auto-lane allowlist. |
| [models.md](models.md) | The tier table. **The only file in `cowork/` that names a model.** |
| [sweep-procedure.md](sweep-procedure.md) | The shared cron run, written once. |
| [release-signoff.md](release-signoff.md) | The weekly human ritual: test a pre-release, promote it. |
| [crew.md](crew.md) | scout / scribe / builder — who does what. |
| [integrations-map.md](integrations-map.md) | Which provider reaches which mode, and every deliberate gap. Maintained by the integrations sweep's reach week. |
| `workstreams/*.md` | Fifteen charters: owned paths, standing concerns, what is out of scope. Every `CAPABILITIES` row maps to exactly one; ownership never overlaps. |
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
| `cron/integrations-campaign.md` | `20 7 * * 1-5` weekdays | integrations | `deep` | https://claude.ai/code/routines/trig_019w6RJqz8aWJ13TkXmPUgtX |
| `cron/agents-standup.md` | `15 6 * * 1-5` weekdays | agents | `fast` | https://claude.ai/code/routines/trig_013tsooGjdnEMLRQcm7ZKU57 |
| `cron/shipped-standup.md` | `0 18 * * *` daily | — | `standard` | https://claude.ai/code/routines/trig_0118jEhPuaKrCaUWCYQtVgEv |
| `cron/digest.md` | `15 8 * * *` | — | `standard` | https://claude.ai/code/routines/trig_01VY1hbAZKeGuKA1GLyVhbow |
| `cron/slack-relay.md` | `0 7-23 * * *` hourly | — | `fast` | https://claude.ai/code/routines/trig_01X18LBBBZ1FWEtx2Cmffyow |
| `cron/release-promote-ask.md` | `0 9 * * 1` Mon | — | `fast` | https://claude.ai/code/routines/trig_01G4TuU1wYY7GXJ1cEXZUNSu |
| `cron/cd-deploy.md` | `0 4 * * *` daily + push (any branch) | — | `standard` | https://claude.ai/code/routines/trig_01AkW6ojpjKcra8H64R3Astr |
| `events/pr-opened-dod-audit.md` | PR opened / synchronized | — | `standard` | https://claude.ai/code/routines/trig_01Egz2NXy4GwzJzRRC7Z4Zm3 |
| `events/pr-merged-close-loop.md` | PR closed (merged) | — | `fast` | https://claude.ai/code/routines/trig_019gLyX5qWx7g5rXZkUKaDAo |
| `events/release-published-announce.md` | Release published | — | `standard` | https://claude.ai/code/routines/trig_01VXdR2FbPJUsMqVWghA7C5T |

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
`make test` + `make lint` + `make security` and a `code-reviewer` pass, and merges via
`gh pr merge --auto` so the main-branch ruleset decides — including the `pr-feedback` status, which
means Claude Review ran and every blocking finding was answered. It never dismisses an alert, and it
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

**And nothing ships to users on merge.** A merge publishes a PyPI pre-release; the accumulated batch
becomes an official version only when a human ✅s
[`cron/release-promote-ask.md`](routines/cron/release-promote-ask.md)'s weekly question. That is the
last backstop, and the only one involving somebody who has actually been running the code.

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
`tests/fixtures/cowork_github_access_live.json` — 15 of 19 operations served, and the four refusals
each close off an approach that looks obvious:

| Refused there | Consequence |
|---|---|
| `POST /graphql` — *"only the pinned set of PR-review operations is served. Use REST … instead"* | Installing `gh` fixes nothing: `gh pr list --json` and `gh issue list --json` are GraphQL underneath, so the reads the sweeps and the digest are built on are exactly what would still fail. And `pr_feedback.py` cannot answer at all there — `reviewDecision` and thread resolution are v4-only — so it says so rather than reporting a PR it never read. |
| `GET`/`PATCH /repos/…/actions/variables` | The `YEABOI_MODEL_*` half of `cd-deploy` step 3 can never succeed in-session. It moved to `.github/workflows/cowork-repo-setup.yml`, where a runner has no proxy. |
| `POST /repos/…/statuses/{sha}` | The `pr-feedback` commit status can only be posted from CI — which is where `.github/workflows/pr-feedback.yml` already posts it. |

The labels half, by contrast, is genuinely repaired by the REST path: `gh label list` is GraphQL and
was refused, while `GET /repos/{slug}/labels` is served. Re-derive any of this by re-running
`scripts/probe_github_access.py`; never edit the fixture by feel.

**What each command covers.** `make cowork-setup` does the thirty-one GitHub labels (`cowork`,
`cowork:proposal`, `claude-implement`, `feedback-override`, the `release:promotion`/`release:promote`
pair the promotion path fires on, the `integration:candidate`/`integration:approved` pair the
campaign lane fires on, `workstream:<name>` for each of the fifteen, and the seven `type:*` labels
shared with the feedback system — of which a scout may emit only four) and the four
`YEABOI_MODEL_*` repository variables — the workflows read their model from a variable because a YAML
file cannot read a markdown table. `/cowork deploy` does all twenty-three routines, the webhook triggers
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
