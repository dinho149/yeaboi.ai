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
                    scribe: verify Done, Slack ship note, Notion page
```

A proposal is a question, so it costs one GitHub issue and nothing else. The Linear ticket opens when
the answer is yes — at approval for the propose lane, before the builder starts for the auto lane.

Unapproved proposals are closed by the digest routine after 14 days, and closing one yourself is how
you say no sooner. GitHub issues *are* the queue — there is no other shared state between routine
runs.

## Where things live

| File | What it is |
|---|---|
| [definition-of-done.md](definition-of-done.md) | The ten-item contract. Binding on routines **and** on `/ship`. |
| [house-rules.md](house-rules.md) | Guardrails + the closed auto-lane allowlist. |
| [models.md](models.md) | The tier table. **The only file in `cowork/` that names a model.** |
| [sweep-procedure.md](sweep-procedure.md) | The shared cron run, written once. |
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

> You are the `<name>` workstream for yeaboi. Read `cowork/routines/cron/<file>.md` in this repo and
> follow it exactly.

Behaviour is changed by editing this folder in a PR — never by re-typing a prompt in a web form.

## Adding a routine

1. Write `routines/cron/<name>.md` (or `routines/events/<name>.md`) — schedule, workstream, focus,
   stop conditions. Everything shared belongs in `sweep-procedure.md`, not here.
2. Add or extend a charter in `workstreams/`.
3. Give it a tier in [models.md](models.md) — sweeps inherit theirs from `sweep-procedure.md`;
   anything that does its own model-worthy work needs a row.
4. Add the row to the table below — cron, workstream and tier. This is what gets registered; a
   routine file that is not in the table fails `make cowork-check`.
5. Run `/cowork deploy`. It registers the new routine from that row and leaves the others alone. (By
   hand it would be: claude.ai/code/routines, the thin prompt above, the Model dropdown set to the
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
| `cron/integrations-sweep.md` | `30 6 * * 2` Tue | integrations | `deep` | https://claude.ai/code/routines/trig_01YKiiD5aUn4AtoCyUjZaFLR |
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
| `cron/marketing-weekly.md` | `0 8 * * 6` Sat | marketing | `deep` | https://claude.ai/code/routines/trig_011f1J2fUGPhDQKSmjEMEiGs |
| `cron/digest.md` | `15 8 * * *` | — | `standard` | https://claude.ai/code/routines/trig_01VY1hbAZKeGuKA1GLyVhbow |
| `cron/slack-relay.md` | `0 7-23 * * *` hourly | — | `fast` | https://claude.ai/code/routines/trig_01X18LBBBZ1FWEtx2Cmffyow |
| `events/pr-opened-dod-audit.md` | PR opened / synchronized | — | `standard` | |
| `events/pr-merged-close-loop.md` | PR closed (merged) | — | `fast` | |
| `events/release-published-announce.md` | Release published | — | `standard` | |

> **Cron trap.** The fortnightly and monthly slots restrict **day-of-month only**. Standard cron
> *ORs* day-of-month with day-of-week when both are restricted, so `30 7 1-7,15-21 * 2` fires every
> day 1–7 *and* every Tuesday — a fortnightly routine silently running near-daily. Never restrict
> both fields.

## Setting this up

Two commands, in this order. Both are reconciles — safe to re-run after adding a routine, changing a
tier, or when someone new joins.

```bash
make cowork-setup   # GitHub labels + model repository variables
/cowork deploy      # in a Claude session: the cron routines + the Linear labels
```

Nothing is retyped. `scripts/cowork_setup.py` derives the labels from `workstreams/`, the variables
from [models.md](models.md#workflows), and the routines from the table above — so the schedule a
routine actually runs on is the one written down here. `tests/unit/test_cowork_setup.py` fails on the
same drift in `make test-fast`.

**What each command covers.** `make cowork-setup` does the twenty-six GitHub labels (`cowork`,
`cowork:proposal`, `claude-implement`, `feedback-override`, `workstream:<name>` for each of the
fifteen, and the seven `type:*` labels shared with the feedback system) and the four
`YEABOI_MODEL_*` repository variables — the workflows read their model from a variable because a YAML
file cannot read a markdown table. `/cowork deploy` does the seventeen cron routines and mirrors the
workstream labels onto the Linear `Yeaboi` team; both need a Claude session, since a routine is
account-scoped and has no CLI behind it.

**What neither can do**, and both report: connecting Linear/Slack/Notion at
[claude.ai/customize/connectors](https://claude.ai/customize/connectors), installing the Claude GitHub
App, setting the `AUTO_VERSION_PAT` secret, the three **event** routines — the routines API takes
a cron expression only, so those are added by hand — the **`pr-feedback` status context** on the
`main-branch` ruleset's required checks (DoD item 10; without it `.github/workflows/pr-feedback.yml`
still computes and posts a red status, and GitHub simply lets the PR merge anyway — see
`.claude/skills/ci-and-release/SKILL.md` for the `gh api` that verifies it) — and the **Linear GitHub
integration** on this repo (with issue-status automation on), which is what turns a PR body's
`Closes YEA-NN` into the attach and the Done-on-merge transition. If that integration is off, every ticket silently stalls at
In Review, and the only thing that would notice is `pr-merged-close-loop` — one of the hand-added
event routines above.

## Running it afterwards

A fleet you can only start is a fleet you cannot correct. `/cowork` is the same reconcile with the
rest of the verbs on it:

| | |
|---|---|
| `/cowork status` | what is running, against what this folder says — drift, orphans, paused routines. Read-only. |
| `/cowork deploy` | register what is missing, update what has drifted, fill the URL column. |
| `/cowork run <name>` | fire one routine now, instead of waiting for its cron. Not a dry run — it files issues and posts to Slack. |
| `/cowork pause` / `resume` | stop and restart the fleet without removing anything. |
| `/cowork teardown` | take it down (see below). |
| `make cowork-check` | the repo half of `status`, with no session needed. |
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
