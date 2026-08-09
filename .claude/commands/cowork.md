---
description: Run the cowork fleet — status, today's schedule, deploy, run one now, pause, resume, teardown
---

Drive the standing workstreams described in `cowork/`. Verb (optional): $ARGUMENTS — defaults to
`status`.

| Verb | Does |
|---|---|
| `status` | what is running, against what the repo says. Read-only. |
| `deploy` | register what is missing, update what has drifted, wire webhooks for what it created, fill the README URL column. |
| `today` | what runs today and over the next week. Read-only, and the one verb needing no API call. |
| `run <name>` | fire one routine immediately, instead of waiting for its cron. |
| `pause [name…]` | stop routines firing without removing them. No names means all of them. |
| `resume [name…]` | undo a pause. |
| `teardown [--labels] [--variables] [--all]` | take the fleet down. |

**Nothing here re-authors data, and nothing here compares two things by eye.** `cowork/README.md` is
the routine table, `cowork/models.md` the tier table, `cowork/workstreams/` the label list,
`cowork/definition-of-done.md` the Linear/Slack/Notion target ids — and `scripts/cowork_setup.py` has
already parsed all four. Read its output; do not read those tables yourself, and do not assemble a
request body or diff two routines in your head. Twenty-two routines × six fields is exactly the work
that goes right most of the time, and "most of the time" here is a sweep silently running last
month's prompt.

## Every verb starts the same way

1. `RemoteTrigger` with `action: "list"`.
2. Save that response verbatim to a scratch file, e.g. `<scratchpad>/cowork-triggers.json`.
3. Pass it to the script with `--triggers <file>`.

If a step fails, report it and continue to the next — they are independent, and a missing Linear
connector should not stop the routines from being read.

---

## `status` (the default)

```bash
uv run python scripts/cowork_setup.py --check --triggers <file>
```

Report exactly what it prints: routines that are missing, routines that have drifted (with the field
and both values), orphans, paused routines, blank README URLs, missing labels and variables. Add
nothing and soften nothing — a clean run and a run with three problems must not read alike.

Then say which verb fixes what it found.

## `deploy`

The reconcile. Safe to re-run: after adding a routine, changing a tier in `cowork/models.md`, editing
a routine file, or when someone new joins.

1. **Repo first.** `make cowork-check`. If it reports a *repo* inconsistency — a routine file missing
   from the table, a cron mismatch, a tier that does not resolve — **stop and fix that**. Registering
   from a table that disagrees with its own file is how a sweep ends up on the wrong schedule.
2. **Labels and variables.** `make cowork-setup`. Report the counts.
3. **Linear labels.** Take `targets.linear` from `--json` for the team id, `list_issue_labels` for
   that team, then `create_issue_label` for each `workstream:<name>` in the manifest's `labels` that
   is missing. The non-workstream labels (`cowork`, `cowork:proposal`, `claude-implement`, and the
   `type:*` set) are GitHub-only — the proposal queue lives in GitHub issues, and Linear only ever
   carries the workstream dimension.
4. **Routines.** The account-scoped half, and the reason this is a command and not a make target.
   - Load the `schedule` skill first (Skill tool, `schedule`) for the current `RemoteTrigger`
     contract and how an `environment_id` is resolved. Do not work from memory: it is an account-side
     API that can move, and a stale shape fails in a way that looks like a permissions problem.
   - `uv run python scripts/cowork_setup.py --plan --triggers <file>`.
   - For each entry in `create`, call `RemoteTrigger` `action: "create"` with its `body` **verbatim**.
   - For each entry in `update`, call `action: "update"` with its `trigger_id` and its `body`
     verbatim — the body is already the minimal patch. Report the `fields` it lists, both values.
   - Everything in `ok` is left alone. Say how many.
   - `orphans` are **not** deleted (see `teardown`): report each with its URL and ask.
   - If the plan sets `suspicious`, **create nothing**. It means the snapshot reports a routine
     missing *and* an unfamiliar one, and the likeliest cause is that a name was damaged while the
     response was being written to the file — in which case creating would register a second copy of
     a routine that is already firing. Re-`list`, re-save, re-plan. If it persists, it is a real
     rename; say so and ask.
   - **If the plan lists anything under `needs`, fill it in before posting a single body.** Three
     values come from the account rather than the repo, and on a first-ever deploy there is no live
     routine to read them off:
     - `connectors` — resolve the Linear, Slack and Notion connectors for this session. Every
       connector is attached by default, so setting these three is a *removal*: a scout that can
       reach mail is a scout that can mail somebody.
     - `environment_id` — from the `schedule` skill's environment discovery. It can also be passed
       to the script as `--environment <id>`.
     - `repo_url` — the script resolves this from `gh` on its own; if it still appears here, `gh` is
       not authenticated.

     A body carrying an empty string for any of these is one the API will happily accept. Twenty-two
     routines then register pointing at no repository, and it looks like it worked until the first
     Monday.
   - **Keep the `trigger_name` of every routine you created** and got an id back for. Step 5 needs
     them, and they are the only evidence that exists.
5. **Webhooks.** What fires the event routines and `cd-deploy`. Re-`list` (ids only exist after a
   create), save it, then
   `uv run python scripts/cowork_setup.py --plan --triggers <file> --created "cowork: <name>" …`,
   once per name from step 4. POST `create_webhook_trigger` with the `body` of every `webhooks[]`
   entry whose `blocked` is **null**, and ignore every other entry — a blocked one carries an empty
   body, so the rule is safe to apply mechanically.

   **Never post a webhook for a routine this run did not create.** Four things are true of that
   endpoint and each is recorded in `tests/fixtures/cowork_webhook_live.json`: nothing reports the
   webhooks already attached to a routine, an identical POST is not deduped, there is no delete, and
   an unknown event name is accepted with a 200. So a second POST makes the routine fire twice for
   every event, permanently, and nothing will tell you. Entries under `webhooks_blocked` are the
   normal steady state — report the count and move on.

   A pre-existing routine that genuinely lost its webhook is repaired by hand at
   <https://claude.ai/code/routines>, after checking there what is attached.
6. **URLs.** `uv run python scripts/cowork_setup.py --urls --triggers <file>` on the same fresh
   snapshot. This edits `cowork/README.md` for you — do not edit the table by hand.
7. **Remainder.** Report what no API reaches: the connectors at
   <https://claude.ai/customize/connectors>, the Claude GitHub App, and the `AUTO_VERSION_PAT` secret
   (without it Claude Review never receives `workflow_run` events).
8. **Confirm.** Re-run `status` and give a one-line summary of what this run changed.

After the first deploy, most of this happens on its own: `cron/cd-deploy.md` is fired by a push
webhook on `main` and runs steps 2–7 for every merge that touches `cowork/`. This command stays the
escape hatch, and remains the only place a webhook for a pre-existing routine can be wired.

## `today`

```bash
uv run python scripts/cowork_setup.py --agenda --text
```

Print exactly what it prints. This is the same text `cron/day-ahead.md` posts to Slack at 05:45
UTC each morning, which is the point: the local answer and the posted one come from one renderer,
so they cannot disagree.

That is why the terminal shows Markdown source — `**cd-deploy**`, backticked times — rather than a
terminal's own idea of bold. Rendering it here would mean a second renderer, and a second renderer
is a second thing that can be wrong about a Tuesday.

`--date YYYY-MM-DD` answers "what is on next Monday?". Nothing here touches the account — the
schedule lives in `cowork/README.md`, not in the routines API, so this verb skips the
`RemoteTrigger list` every other verb starts with. What it *cannot* tell you is whether the fleet
is paused or drifted; that is `status`.

## `run <name>`

`RemoteTrigger` `action: "run"` against the routine whose `trigger_name` is `cowork: <name>`. Use it
to test a routine you just edited rather than waiting until Thursday.

**Confirm with the user before firing.** This is not a dry run: a sweep opens GitHub issues, writes
Linear tickets and posts to Slack under their name. Name the routine back and say what it will touch,
then wait. If the name matches no routine, list the ones that exist rather than guessing at the
closest.

## `pause` / `resume`

`RemoteTrigger` `action: "update"` with a body of `{"enabled": false}` or `{"enabled": true}`, one
call per routine. With no names, every `cowork: *` routine — **including `cowork: slack-relay`**, so
say in the report that Slack control is off too and resuming is a local verb from here. (The relay's
own `pause all` from Slack excludes itself for exactly this reason; a local session pausing
everything is fine because a local session can also resume.) Fully reversible, and nothing else
about the routine changes.

`deploy` deliberately does **not** re-enable a paused routine — otherwise a pause would end at the
next deploy with nothing to say it had. `status` reports paused routines every run so a fleet that is
off never looks like a fleet that is fine.

## `teardown`

Destructive. **Confirm before the first write**, listing what will go and what will not.

Order:

1. **Routines** (always). `update` each one with `{"enabled": false}`, then print every routine's
   `https://claude.ai/code/routines/<id>` URL.

   Be exact about what happened: `RemoteTrigger` has **no delete action**. The routines are switched
   off and still exist; removing them is a click each at claude.ai. Do not report them as deleted.
2. **The GitHub half** (only if asked). `--labels` / `--variables` / `--all` map to
   `make cowork-teardown`, which prompts, or to
   `uv run python scripts/cowork_setup.py --teardown [--labels] [--variables] --yes` when the user has
   already confirmed here. `claude-implement` is never deleted — it predates cowork and gates the
   `claude.yml` implement job. The `type:*` labels are never deleted either — the feedback system
   shares them.
3. **Linear labels** (only with `--all`). Delete the `workstream:*` labels on the Linear team.

Deleting a label strips it off every issue that carries it, and no re-run puts that back. If the user
asked only for "delete the cowork setup" without naming a scope, do step 1 and *ask* before steps 2
and 3.

---

**Name tiers, never models.** A model id, when you need one, comes from the plan or the manifest,
which read it from `cowork/models.md`. That file being the only place a model is written down is the
contract `tests/unit/test_cowork_models.py` enforces — and this command is one of the files it checks.
