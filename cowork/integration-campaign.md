# Integration campaign

The procedure [`routines/cron/integrations-campaign.md`](routines/cron/integrations-campaign.md)
follows, five weekday mornings a week. It is the fleet's only building lane; everything else
maintains. Read [`house-rules.md`](house-rules.md) — **The campaign lane** — first, then
[`workstreams/integrations.md`](workstreams/integrations.md).

A campaign takes **one provider** and makes it real everywhere: a client, a credential, a way to
connect it, a way to see it is still working, and a place in every mode that has a question it
answers. The unit of human approval is the provider. The unit of work is the angle.

## What "done" means, and where it is written

`integrations-map.md` is the record, not a report. A campaign is finished when the provider has a
complete row in its reach matrix — every mode either **consumes** it or carries a line in *Recorded
gaps* saying why it does not — and a Per-provider section naming its credential, its verification
probe and its scope discovery.

That is deliberately the only definition. "The client works" is not done; a provider that reaches no
mode is a dead setting, and this repo has had one bottom matrix row of dashes for its whole life to
prove it.

## Three PRs, not seven angles

The guardrail is unchanged: **one open PR per workstream**. Five runs a week and one open PR means a
run whose predecessor is still red is spent driving that PR to green — `sweep-procedure.md` step 2,
inherited verbatim. Realistic throughput is three PRs a week, so a campaign is three coherent
layers rather than seven thin ones.

| # | Angle | What lands | Paths |
|---|---|---|---|
| 1 | **Edge** | the provider client, its contract cassette, and the credential getters | `tools/<provider>.py`, `tests/contract/`, `config.py` *(Extends)* |
| 2 | **Connect** | the wizard step, `_verify_<provider>`, and the settings Credentials section | `ui/provider_select/`, `_screens_secondary.py` *(both Extends)* |
| 3 | **Reach** | per-mode wiring, the `integrations-map.md` row, and a recorded gap for every mode left unwired | `standup/collector.py`, `analysis/engine.py`, `reporting/activity.py`, `roadmap/ingest.py` *(all Extends)* |

Angle 2 is one PR and not two because the probe and its only caller ship together — split, it lands
a `_verify_*` nothing calls, which is the shape of the gap this whole design exists to close.

### Angle 2 has a prerequisite, and it is not the campaign's to build

**Verification exists only in the wizard.** `_verify_jira`, `_verify_azdevops`, `_verify_notion`,
`_verify_confluence` and `_verify_vc_token` all live in `ui/provider_select/_verification.py`, and
the settings Credentials tab calls none of them — so a credential that expires is discovered by a
failed run rather than by the screen that shows it. `integrations-map.md` records that as an open
gap owned by **tui-ux**, and it stays theirs: it is one surface every provider then reuses, so
building it inside a campaign would leave the second campaign with nothing to do for that angle and
saddle the first with a cross-charter UI refactor on top of a new provider.

Until it lands, **angle 2 degrades honestly**: it ships the wizard step, the `_verify_<provider>`
probe and the Credentials section, and the PR body says in one line that the probe is reachable from
setup only and that steady-state health is blocked on the tui-ux gap. It does not build a health
surface, and it does not pretend to have one. Once tui-ux ships it, the angle becomes what it should
always have been — registering `_verify_<provider>` with a health check that already exists, which is
a genuine append and fits the `Extends` grant exactly.

Each angle is one review of one decision, which is the same argument `house-rules.md` already makes
for the CodeQL same-rule batch. **One coherent change per run** still holds; a grab-bag across two
angles does not.

## The state is the repo

Never read the next angle off the campaign issue's body. An issue body is state a truncated run can
leave half-written, and "GitHub issues are the queue, and there is no other shared state" is the
strongest property this fleet has. Every angle has a filesystem answer:

| Angle | Met when |
|---|---|
| 1 | `src/yeaboi/tools/<provider>.py` exists and `tests/contract/test_<provider>_contract.py` passes |
| 1 | `config.py` has the credential getters, and `pyproject.toml` carries the extra |
| 2 | `_verification.py` has `_verify_<provider>`, and `ui/provider_select/` has its step |
| 2 | `_screens_secondary.py`'s `_SETTINGS_TAB_SECTIONS["Credentials"]` names it |
| 3 | `integrations-map.md` has a `**<Provider>**` matrix row with no bare `—`, and a Per-provider section |

The issue answers two things and nothing else: **which provider**, and **did a human say yes**.
A re-run, a duplicate firing and `/cowork run integrations-campaign` then all converge on the same
next angle instead of diverging.

## Appending at an `Extends` site

Angles 1–3 all touch files another workstream owns. The grant is by site and by operation
(`house-rules.md`, **Extends**), and three things keep it honest:

1. **Append only.** A dict entry, a tuple member, an alias, a getter, one screen section. Changing
   what is already there — a threshold, a metric, a message, a layout — is a proposal for the owner,
   even when your provider would benefit.
2. **Collision guard, before you open the PR.** For every `Extends` path the angle touches:

   ```bash
   gh pr list --label "workstream:<owner>" --state open --json number,title
   gh pr diff <n> --name-only          # for each open one
   ```

   If the owner has an open PR touching that file, **take a different angle this run** and say so in
   the run log. Do not wait, and do not edit around them.
3. **Never `ui/mode_select/__init__.py`.** It is not on the grant and no angle needs it.

## Two rules no other lane carries

**Dependencies.** Every provider is a vendor SDK, so a campaign adds a package to a published
distribution, unattended. It goes under `[project.optional-dependencies]` behind a lazy import,
never into `dependencies`, and the module degrades to a named Notice when the extra is absent — the
same shape `pdf`, `docs` and `bedrock` already use. The shortlist issue names the package, its
licence and its maintainer, so the ✅ that picked the provider also picked the dependency.

**Cassette honesty.** `tests/contract/` cassettes are hand-crafted and re-recorded with `make record`
against a real instance. Nobody will ever run that for a provider they have no account with, so a
campaign's cassette tests the *author's belief* about the API — a closed loop that goes green and
means nothing, against a charter whose first standing concern is that a stale cassette is a real
finding. So: cite the doc URL and the response example you wrote it from in the PR body, and record
`cassette hand-crafted from docs <date>, never recorded live` in the map's Per-provider section. The
first person with an account runs `make record` and deletes that line.

## Definition of Done

Every item of [`definition-of-done.md`](definition-of-done.md) applies unchanged. Four are worth
naming because a campaign is where they bite:

- **Surface parity** (item 5) — a provider is a capability. Angle 3 adds or extends the
  `CAPABILITIES` row and the `FeatureTip`, or records an `Exempt("reason")`. `make test` fails until
  it does, and names the edit.
- **Observability** (item 6) — every fetch logs start and result, every auth failure logs at
  `warning`/`error` with enough context to diagnose, and **never the credential**.
- **Coverage** (charter) — every new fetch path registers with `CoverageTracker` or the standup
  collector's error/skip classification. A source that fails silently reads as a zero, and a zero is
  a number someone will believe.
- **Review feedback** (item 10) — unchanged, and it is the gate that makes the whole lane safe:
  `pr_feedback.py` refuses an `<!-- addressed: … -->` marker from the PR's own author, so a campaign
  may fix a finding and can never dismiss one.

## What this procedure does *not* inherit from `sweep-procedure.md`

It inherits step 2 (work in flight) and step 5's gate — scribe → builder → independent
`code-reviewer` → fix every blocker/should-fix → labelled PR → In Review → arm `--auto` only if
`pr-feedback` is genuinely required on the ruleset.

It does **not** inherit steps 3, 4 and 6. A campaign works from a plan, not a survey: there is no
scout, nothing to deduplicate against the proposal queue, and no propose lane — a candidate issue
carries neither `cowork:proposal` nor a `type:` label and eats no proposal slot.

## Stop conditions

- **`main` unfetchable, or `make test` failing on a clean checkout.** Same as any sweep.
- **An open PR on `workstream:integrations`.** Drive it and stop; that is the run.
- **No approved campaign and it is not Monday.** Fall back to an Edge-axis maintenance sweep of the
  providers that already exist, or exit quietly.
- **A collision on every angle's `Extends` paths.** Say so in the run log and exit; two agents in one
  file is the thing the grant is narrow to avoid.
- **A campaign that overruns its week is not a failure.** It runs until its map row is complete or a
  human closes the issue. The week is the *picking* cadence, not a deadline, and an abandoned
  half-wired provider is worse than a campaign that takes nine days. Never two campaigns open at once.
