# cd deploy

**Trigger** — cron `0 4 * * *` (daily 04:00 UTC), plus a GitHub `push` webhook (every branch)
**Summary** — ships a merged change to cowork/ into the live fleet, within a minute of the merge
**Workstream** — none; this routine is how every other routine gets deployed.
**Model** — `standard` ([models.md](../../models.md))

```json webhook
{"source": "github", "events": ["push"], "filter": {}}
```

`cowork/` is the versioned source of truth for a fleet of routines that live in an account, not in
this repo. Nothing used to read it: a change merged to `main` sat there until a human ran
`/cowork deploy`, and until they did, the repo said one thing and the fleet did another with nothing
reporting the gap. This routine closes it. The cron is the safety net; the webhook is what makes it
feel immediate.

**The webhook is not scoped to `main`, and cannot be.** The routines API rejects every ref and branch
key its filter might have taken (`filter.ref`, `filter.branches` — see
`tests/fixtures/cowork_webhook_live.json`), so what is registered fires on a push to *any* branch in
the repo, and this repo runs many parallel worktrees. Step 1 is therefore the whole filter, and it
reads `origin/main` rather than whatever ref happened to fire. Most firings exit there having done
one fetch and one `git log`. A firing that gets past it and finds nothing to change exits at step 4
having posted nothing — the plan is a diff, so a second run over an already-deployed fleet is a
no-op, not a repeat.

**It composes nothing.** `scripts/cowork_setup.py` owns every comparison, every request body and the
README edit; this routine calls it, POSTs what it is handed, and reports. Where a step below says
"verbatim", it means the bytes from the plan, unedited — a body assembled here by hand is a body no
test has ever seen.

## Run

1. **Decide whether there is anything to do, from `origin/main` only.** `git fetch origin main`, then
   `git log -1 --name-only --format= FETCH_HEAD`. If nothing under `cowork/` or
   `scripts/cowork_setup.py` changed in that commit, **exit silently**.

   Deploy what is on `origin/main`, never what the checkout happens to hold: a push webhook fires for
   every branch, so the ref you are standing on is not necessarily the one that changed, and a
   feature branch's `cowork/` edits are not deployable — they have not been reviewed. If the checkout
   is not already at `FETCH_HEAD`, reset it there before step 2. The daily cron lands in this same
   step and exits the same way.

2. **`make cowork-check`** — abort on non-zero, reporting to Slack. This is not redundant with CI: it
   is what stops step 4 composing a prompt that points at a file which does not exist. A README row
   naming a missing routine file registers a routine that wakes up, cannot read its own instructions,
   and does something unpredictable — and it would be *this* run that did it.

3. **`uv run python scripts/cowork_setup.py --strict`** — the GitHub labels and the four
   `YEABOI_MODEL_*` repository variables. `--strict` is the point: without it a rejected `gh` call is
   a note on a stream nobody is reading, and "created no labels, exited 0" reads exactly like
   success. If it fails, report and stop — do not proceed to the routines with a half-applied repo.

4. **Reconcile the routines.** `RemoteTrigger` `action: "list"`, save the response **verbatim** to a
   scratch file, then
   `uv run python scripts/cowork_setup.py --plan --strict --triggers <file>`.
   - Exit 2 means the plan refused itself — a suspicious snapshot, an unresolved `needs`, or more
     updates than `MASS_UPDATE_LIMIT`. Report the stderr to Slack and stop. **Never** re-run it
     without `--strict` to get past this; the flag exists because there is no human here to ask.
   - Otherwise POST each `create` body verbatim (`action: "create"`), then each `update` body with
     its `trigger_id` (`action: "update"`). Leave `ok` alone. **Never delete, and never touch an
     orphan** — name them in the Slack post and stop there.
   - Keep the `trigger_name` of every routine you created and got an id back for.

5. **Wire the webhooks for what you just created.** Re-`list` (ids only exist after a create), save,
   then `--plan --triggers <new file> --created "cowork: <name>"` once per name from step 4. For every
   `webhooks[]` entry whose `blocked` is `null`, call the **`RemoteTrigger` tool** with
   `action: "create_webhook_trigger"` and its `body` verbatim — the same tool as step 4, a different
   action, never a `curl`. **Ignore every other entry** — a blocked one carries an empty body, so the
   rule is safe to follow without thinking about it.

   **Never post a webhook for a routine you did not create in this run.** Nothing can read back
   whether a routine already has one, the API does not dedup, and there is no delete — so a
   second POST means that routine fires twice for every event, permanently. `webhooks_blocked`
   naming a routine is the normal steady state, not a problem to solve.

6. **Record the URLs.** `--urls --triggers <new file>` — the script edits `cowork/README.md` itself.
   If the file changed, open a PR for it; **never push to `main`**. This only happens when a routine
   was created, so most runs skip it. The house rules apply to this PR like any other:

   - `gh pr list --label "workstream:platform" --state open` **first**. If one is already open,
     push the URL fix onto that branch instead of opening a second — one open PR per workstream.
   - Label it `cowork`, `workstream:platform`, `type:chore`. An unlabelled PR is one
     `claude-review.yml` may skip entirely, because it skips `github-actions[bot]` authors.
   - Body: one line saying the fleet was deployed and this records the ids, and one line recording
     the DoD exemptions — items 1, 5, 7 and 8 do not apply to a table of URLs. Say so; silence is
     not an exemption.
   - Merging it is itself a `cowork/` change on `main`, so it fires this routine again. That run
     finds a plan with nothing to create and a README with nothing to fill, and exits silently at
     step 4. Converging on the second pass is expected, not a loop.

7. **Report.** Spawn `cowork-scribe` for the Linear `workstream:*` label mirroring and one
   `#yeaboi-claude` post: what was created, what was updated (field by field), any orphans, any
   blocked webhooks, and the README PR link. **If `self_update` in the plan is not null, say so
   explicitly with both the live and the wanted value** — that is this routine changing itself, and
   it is the one change that must never land quietly.

## Stop conditions

- **Nothing to do is the common outcome.** An empty plan posts nothing to Slack at all. A routine
  that reports every morning is a routine nobody reads by Thursday.
- **Never compose a request body.** Only bodies that came out of `--plan` are ever POSTed. In
  particular never send `{"enabled": …}`: the plan never carries it, because `pause` is a supported
  verb and a deploy that silently un-paused the fleet would undo a human's decision with nothing
  said. If the fleet looks wrongly paused, that is a sentence in the Slack post, not an action.
- **Never delete anything, and never apply `claude-implement`.** The house rules apply here as they
  do to every routine.
- **You cannot fix yourself.** If a bad `cd-deploy` prompt or tool grant lands on `main`, this
  routine is the thing that would have to repair it, and it is the thing that is broken. There is no
  automatic recovery: the escape hatch is `/cowork deploy` from a local session, and step 2 exists to
  make the case rare. Say this in the Slack post if `self_update` ever fires — the human wants to
  know before the next merge, not after.
