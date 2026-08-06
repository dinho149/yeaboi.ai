# integrations

**Owns** — `src/yeaboi/tools/` (jira, azure_devops, github, confluence, notion, calendar_tools,
local_git, codebase, risk, llm_tools), `src/yeaboi/jira_sync.py`, `azdevops_sync.py`,
`export_targets.py`, `ticket_text.py`, `markdown_convert.py` (Markdown → Notion blocks / Confluence
XHTML), `tests/contract/` and its cassettes

**Reads** — the consumer side of every integration, to find and never to edit:
`analysis/engine.py`, `analysis/ai_usage.py`, `analysis/doc_quality.py` (the component/source model
and the four hand-written credential probes); `standup/collector.py`, `code_scope.py`,
`documentation_scope.py` (the `fetchers` dict and the retry/classify closure inside
`collect_recent_activity`);
`reporting/activity.py` (`SOURCE_COMPONENTS`, `_canonical_source()`); `roadmap/ingest.py`
(`RoadmapSource` and `ingest_source()`); `agent/repo_signals.py` and the tool-calling sites in
`agent/nodes.py`; `ui/provider_select/` (the wizard's four steps and `_verification.py`);
`ui/mode_select/screens/_screens_secondary.py` (the Credentials settings sections); and
`config.py`'s credential getters and `TEAM_ANALYSIS_*` scope variables.

**A find in a `Reads` path is always `lane: propose`, with `owner:` set to the workstream that owns
the file.** `**Owns**` is where a builder may edit; `**Reads**` is only where a scout may look.
Four charters already delegate here — standup's *"Jira/AzDO fetching itself (**integrations**)"*,
reporting's *"Tracker fetching"*, roadmap's *"Document fetching from Notion/Confluence"*, analysis's
*"Tracker API mechanics"* — and until this paragraph existed, the files they were pointing at were
read by no routine at all.

**Skills** — `.claude/skills/agent-and-state/SKILL.md` (tool conventions)

**Cadence** — Tue 06:30 UTC. Three axes on a three-week rotation — see
[`routines/cron/integrations-sweep.md`](../routines/cron/integrations-sweep.md).

## The three axes

An integration is not a credential field. It is a chain: **credential → verification → scope
discovery → per-mode consumption → what the user finally sees**, and it is only worth as much as its
weakest link. The charter covers all of it.

1. **Edge** — the provider API itself. Cassettes, pagination, auth, rate limits, write-back symmetry.
2. **Reach** — whether a connected provider actually arrives in analysis, planning, roadmap, standup,
   reporting and performance. A provider that reaches no mode is a dead setting.
3. **Surface** — whether a user can tell what is connected, whether it still works, and what it
   powers. [`integrations-map.md`](../integrations-map.md) is the record the reach axis maintains.

## Standing concerns

### Edge

- **Contract-test drift.** `tests/contract/` replays recorded responses. When a provider changes a
  field shape, the cassette still passes and production breaks. Compare cassettes against current
  provider docs; a stale cassette is a real finding.
- **Rate limits and pagination.** `azure_devops.py` is 2,750 LOC and has had truncation bugs before
  (the AzDO refetch path). Any list call without explicit paging is suspect.
- **Auth failure paths** must log at `warning`/`error` with enough context to diagnose, and must
  never log the credential.
- **Write-back symmetry** — `jira_sync.py` and `azdevops_sync.py` should stay behaviourally paired.
  A capability that exists on one and not the other is a proposal.
- **`risk.py` classifies `@tool`s only.** The write helpers that are plain functions — `create_task`,
  `create_subtask`, `add_issues_to_sprint`, `add_work_items_to_iteration`, `jira_update_issue_fields`,
  `azdevops_update_work_item_fields` — carry no `TOOL_RISK` row and never reach the `human_review`
  gate. That is defensible, because they are not in the ReAct loop, but it should be *written down*
  rather than inferred from their absence. The same file's claim that GitHub registers no writes is
  worth checking against `feedback.py` and `standup/gap_issues.py`, both of which create issues
  through the shared client.

### Reach

- **There is no fetcher abstraction.** A new source costs roughly thirteen edit sites spread across
  three workstreams' paths. The only ABC in `src/yeaboi/` is
  `standup/delivery.py::NotificationDelivery`, which is a *delivery* contract, not a fetching one.
  `analysis/coverage.py::CoverageTracker` and the retry/classify closure inside
  `standup/collector.py::collect_recent_activity` between them already define most of what a
  provider protocol would need. This is the standing structural finding; it
  crosses three charters, so it proposes.
- **Four spellings name Azure DevOps, and only one module normalizes.** `azdevops` (analysis
  delivery), `azdo` (analysis code — a genuinely different system), `azure_devops` (standup),
  `azuredevops` (reporting, canonical because `DeliveredItem.source` has always persisted it). This
  is **deliberate and documented at both sites**, so it is not a finding — but only
  `reporting/activity.py` carries the alias set and `_canonical_source()`. Nothing tells a new source
  which spelling to adopt, and a fifth one would be caught by no test.
- **Coverage must stay visible.** `CoverageTracker` records succeeded / truncated / unchanged /
  inaccessible / failed per source, so an absent source is reported rather than read as a zero. A new
  fetch path that does not register coverage is a finding.
- **A mode that could consume a provider and does not** — with no recorded reason — is a find owned
  by that mode. Record the answer in `integrations-map.md` so the question is not asked again.

### Surface

- **Verification exists only in the wizard.** `_verify_jira`, `_verify_azdevops`, `_verify_notion`,
  `_verify_confluence` and `_verify_vc_token` all live in `ui/provider_select/_verification.py`. The
  settings page has no equivalent, so a credential that expires is discovered by a failed run rather
  than by the screen that shows it.
- **Setup steps that could self-verify** instead of failing on first use.
- **A credential's blast radius should be legible.** The Confluence getters fall back to the `JIRA_*`
  values, so configuring Jira silently enables Confluence tools. That is convenient and undocumented
  on screen; either is fine, both together is not.

## The ops family, and its admission test

Every source yeaboi reads today is a tracker, a repo host or a wiki — `_COMPONENTS` in
`analysis/engine.py` is `("delivery", "code", "docs")`. Nothing describes how the team **runs** what
it ships. That is the largest single gap in this charter's surface.

A provider outside delivery/code/docs qualifies when **all three** hold:

1. **Read-only.** Nothing in yeaboi should mutate infrastructure, billing or access control. Every
   write path that exists today exists because a human approved a plan first, and there is no
   equivalent approval shape for an infrastructure change.
2. **Attributable** — its data resolves to a team member or to a service the team owns. Otherwise it
   cannot join the per-member table or the per-sprint narrative that every consuming mode already
   renders, and it becomes a number with nowhere to sit.
3. **It answers a question a mode already asks.** A provider that answers none of them is a
   dashboard, not an integration.

The questions, which are what makes this concrete:

| Mode | What an ops provider would answer |
|---|---|
| planning | on-call load and incident history as capacity and risk inputs; deploy cadence as a sizing signal |
| analysis | does the team's practice extend past merge — do they own what they run? |
| roadmap | feasibility against what exists in the account, not what the document assumes |
| standup | an incident is a blocker nobody types into a ticket |
| reporting | what shipped is a deploy, not only a merged PR |
| performance | on-call burden is invisible work that no tracker records |

Examples that pass: AWS and GCP (deploys, per-service cost, which services a member has been
touching), Datadog and Sentry (error budget and incidents against the sprint window), PagerDuty and
Opsgenie (on-call load), Teleport (access grants as an onboarding and offboarding signal). Examples
that fail: anything needing a write credential; anything reporting only at org level with no member
or service attribution; anything whose answer no mode has a place to put.

A specific provider is always a proposal, filed against this test, naming which of the three
conditions it satisfies and which mode consumes it first.

## Auto lane, in practice

A broken or flaky contract test, a missing pagination guard with a cassette to prove it, dead code in
a retired provider path, doc drift between a tool's docstring and its behaviour. New provider
capabilities always propose, every ops provider proposes, and nothing in a `**Reads**` path is ever
auto — by construction, since a builder may not edit there.

## Opportunity space

Where a `[feature]`/`[improvement]` find is most likely to be real here, one per axis:

- **Edge** — third-party edges a user hits silently: truncated lists, rate limits swallowed, auth
  that expires without a message, capabilities one provider has that its sibling lacks for no
  recorded reason.
- **Reach** — a credential a user has configured that changes nothing they can see, and a question a
  mode plainly wants answered that no connected provider can answer.
- **Surface** — setup steps that could self-verify instead of failing on first use, and connection
  state a user has to infer from a failure.

The evidence bar in `cowork-scout.md` applies — name the friction, the gap, or the repeated step.

## Out of scope

`tools/team_learning.py` (**analysis**), despite living in `tools/`. Slack and email delivery, which
live in `standup/delivery.py` and `performance/delivery.py` and belong to those modes.

**Interpretation stays with the mode.** This charter asks whether the data arrives, whether it is
complete, and whether the user can tell — never what a mode should conclude from it. A metric
definition, a threshold, a marker set or a narrative is the consuming workstream's call even when the
data reaching it is yours.
