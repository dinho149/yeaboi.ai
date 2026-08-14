# integrations campaign

**Trigger** — cron `20 7 * * 1-5` (weekdays 07:20 UTC)
**Summary** — advances this week's provider integration by one angle, or shortlists the next one
**Workstream** — [`workstreams/integrations.md`](../../workstreams/integrations.md)
**Model** — `deep` ([models.md](../../models.md))

The fleet's only building lane. Everything else maintains what exists; this makes one new provider
real — a client, a credential, a way to connect it, a way to see it still works, and a place in every
mode that has a question it answers.

Read [`house-rules.md`](../../house-rules.md) (**The campaign lane**),
[`integration-campaign.md`](../../integration-campaign.md), the charter, and
[`models.md`](../../models.md) before anything else. `20 7` and not `0 7` on purpose: four of the
five weekdays already carry a `0 7` sweep, and `slack-relay` fires on the hour.

## Run

1. **Work in flight.** `gh pr list --label "workstream:integrations" --state open`. If one is open,
   drive it to green on **both** halves — CI via `gh pr checks`, review via
   `make pr-feedback PR=<n>` — and **stop**. That is the whole run. `sweep-procedure.md` step 2
   applies verbatim, including commenting once on a PR green and unmerged for more than 7 days.

2. **Is a campaign running?**
   `gh issue list --label integration:approved --state open --limit 2 --json number,title,body,url`.
   One open → go to **4**. Two open is a bug: comment on the newer one saying it is superseded,
   close it, and continue with the older. Never two campaigns at once.

3. **Monday only**, and only when step 2 found nothing. On Tue–Fri, skip to **5**.

   a. Close every open `integration:candidate` issue, with a comment saying this week's shortlist
      supersedes it. Superseding is closing, never commenting — the same rule
      `release-promote-ask.md` follows, and it is what makes a late ✅ on last week's Slack line
      resolve to *ask* rather than approving a pick nobody meant.

   b. **Reason three providers fresh.** Not from a stored backlog — there is none, deliberately. Work
      from the charter's four families (`ticketing`, `docs`, `code`, `ops`), the six mode questions,
      and the ops admission test. Dedupe against:
      - closed `integration:candidate` issues — a provider you were told no about stays a no;
      - `integrations-map.md`'s *Recorded gaps* — an answered question is not re-asked.

   c. File one issue per candidate, labelled `integration:candidate` + `workstream:integrations`,
      via `cowork-scribe`. **Not `cowork:proposal`, and no `type:` label** — a candidate must eat no
      proposal slot and must never age out on the digest's 14-day clock, because it is superseded by
      next Monday rather than expiring on one.

      Title: `[integration][<family>] <provider> — <what it brings>`. Body names, in this order:
      which of the four families; for an `ops` provider, which of the three admission conditions it
      satisfies; **which mode consumes it first, and the question it answers there**; and **the
      dependency it would add — package, licence, maintainer**, because the ✅ that picks the
      provider also picks the dependency.

   d. **Post nothing to Slack.** `cron/digest.md` collects these at 08:15 and re-lists them every
      morning until one is answered — which is why they reach you at all if you react on Wednesday.
      A once-a-week post gets one 48-hour relay window and no second chance.

      The digest renders each as one thread reply in the parsed contract, plain text:

      ```slack-reply
      #<issue> — integration candidate: <provider> — <issue link>
      ```

      `scripts/cowork_relay.py` parses that line (`CANDIDATE_RE`) before any human reads it, and a ✅
      from an allowlisted human applies `integration:approved` — **not** `claude-implement`, which
      would fire `claude.yml`'s implement job against a whole-provider issue. The shape is a
      contract, not a style.

   e. Exit. Nothing else runs on a shortlist morning.

4. **Advance one angle.**

   a. **Derive the next unmet angle from the repo**, using the table in `integration-campaign.md`.
      Never from the issue body — a truncated run leaves that half-written, and the filesystem
      cannot lie about whether `_verify_<provider>` exists.

   b. **Collision guard.** For every `Extends` path the angle touches, check whether the owning
      workstream has an open PR touching that file (`gh pr list --label "workstream:<owner>"
      --state open`, then `gh pr diff <n> --name-only`). If it does, take a different angle this run
      and say so in the run log. If every angle collides, stop — that is a stop condition, not a
      reason to edit around somebody.

   c. `cowork-scribe` opens the Linear ticket. `cowork-builder` at `deep` implements it on
      `cowork/integrations-<provider>-<angle>`, inside `**Owns**` plus the `**Extends**` sites, and
      **appending only**. Then **you** spawn an independent `code-reviewer` at `deep` — the builder
      never reviews its own work — and every blocker and should-fix is fixed before the PR opens.

   d. The PR is titled `integration(<provider>): <angle>` and labelled `cowork`,
      `workstream:integrations` and its `type:`. The title prefix is a **corroborating** signal for
      `scripts/release_channel.py`'s track split; the changed paths are the primary one, so a
      forgotten prefix costs a redundant checklist row and never a wrong release.

   e. Probe the ruleset before arming anything:

      ```bash
      gh api repos/$REPO/rules/branches/main --jq '[.[]|select(.type=="required_status_checks")|.parameters.required_status_checks[].context]|index("pr-feedback")'
      ```

      Non-null → `gh pr merge --auto --squash`. Null → **do not arm it**, and say plainly in the run
      log that `pr-feedback` is not a required check, so this campaign advances one angle per human
      merge rather than one per day. That setting is the difference between a five-day campaign and
      a five-week one, and it fails silently: every PR opens, goes green, arms nothing and waits.
      It is the single highest-value manual prerequisite in this design, and it is not this
      routine's to fix — report it, do not work around it.

   f. `cowork-scribe` attaches the PR and moves the ticket to In Review, then comments the angle and
      its PR on the campaign issue — **a record, never the state**.

   g. If that was the last angle, the same PR closes the `integrations-map.md` row and the campaign
      issue.

5. **Fallback.** No approved campaign, and not a shortlist morning: run an **Edge-axis maintenance
   sweep** over the providers that already exist, per [`sweep-procedure.md`](../../sweep-procedure.md)
   with `workstream = integrations` — cassette drift against current provider docs, pagination and
   truncation guards, auth failure paths, `jira_sync.py` vs `azdevops_sync.py` capability drift.
   Rotate to the provider whose cassette is oldest. This is ordinary auto-lane work and files
   ordinary proposals. Nothing found is a normal outcome: exit silently.

6. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- **An open PR on `workstream:integrations`** → drive it, stop. One open PR per workstream holds
  here exactly as everywhere else.
- **Two campaigns open** → close the newer, continue with the older, never run both.
- **Every angle blocked by a collision** → say so and exit. Two agents in one file is what the
  narrow grant exists to prevent.
- **Never apply `claude-implement`**, to a candidate issue or anything else. A ✅ applies
  `integration:approved` through the relay; `claude-implement` is a workflow trigger and applying it
  here would spend the approval on a 110-turn build against an issue that describes a whole week.
- **Never post to the channel.** The digest is the only routine that puts a decision in Slack, and a
  second Monday message is how a reaction gets missed.
- **Never edit outside `**Owns**` and the `**Extends**` sites**, and never anything but an append at
  the latter. `src/yeaboi/ui/mode_select/__init__.py` is outside the grant on every angle.
- **Never add to `[project.dependencies]`.** Optional extras behind a lazy import, or the angle is
  not done.
- **A campaign overrunning its week is not a failure** and must not be abandoned or restarted. It
  runs until the map row is complete or a human closes the issue.
