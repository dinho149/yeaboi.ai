# integrations

**Owns** — `src/yeaboi/tools/` (jira, azure_devops, github, confluence, notion, calendar_tools,
local_git, codebase, risk, llm_tools), `src/yeaboi/jira_sync.py`, `azdevops_sync.py`,
`sync_naming.py` (board-aware sprint numbering shared by both syncs), `export_targets.py`,
`ticket_text.py`, `markdown_convert.py` (Markdown → Notion blocks / Confluence
XHTML), `tests/contract/` and its cassettes

**Reads** — the consumer side of every integration, to find and never to edit. Some of these files
also appear under `**Extends**` below; there the two paragraphs divide by *operation*, not by file —
a campaign may append a provider, and a sweep may only look:
`analysis/engine.py`, `analysis/ai_usage.py`, `analysis/doc_quality.py` (the component/source model
and the four hand-written credential probes); `standup/collector.py`, `code_scope.py`,
`documentation_scope.py` (the `fetchers` dict and the retry/classify closure inside
`collect_recent_activity`);
`reporting/activity.py` (`SOURCE_COMPONENTS`, `_canonical_source()`); `roadmap/ingest.py`
(`RoadmapSource` and `ingest_source()`); `agent/repo_signals.py` and the tool-calling sites in
`agent/nodes.py`; `ui/provider_select/` (the wizard's four steps and `_verification.py`);
`ui/mode_select/screens/_screens_secondary.py` (the Credentials settings sections); and
`config.py`'s credential getters and `TEAM_ANALYSIS_*` scope variables.

**Extends** — registration sites in other workstreams' files, editable **only** from a campaign run
([`../integration-campaign.md`](../integration-campaign.md)) and **only to append a provider**:
`config.py` (credential getters — **platform**); `standup/collector.py` (the `fetchers` dict —
**standup**); `analysis/engine.py` (`_COMPONENTS` and the `_available_*_sources` probes —
**analysis**); `reporting/activity.py` (`SOURCE_COMPONENTS`, `_canonical_source()` —
**reporting**); `roadmap/ingest.py` (`RoadmapSource`, `ingest_source()` — **roadmap**);
`ui/provider_select/` (one wizard step and one `_verify_*` probe — **tui-ux**);
`ui/mode_select/screens/_screens_secondary.py` (one Credentials section — **tui-ux**). Nothing else
in those files, ever, and nothing at all outside a campaign. Altering existing behaviour at one of
those sites is a proposal for the owner, exactly as `**Reads**` always was.

Each of those six charters names this grant in its own **Out of scope** section. That reciprocity is
asserted by `tests/unit/test_cowork_setup.py`, because a grant written down on one side only is a
grant somebody deleted half of. `src/yeaboi/ui/mode_select/__init__.py` is deliberately **not** on
the list: it is the file *Stay in your paths* was written for, and no angle needs it.

**A find in a `Reads` path is always `lane: propose`, with `owner:` set to the workstream that owns
the file.** `**Owns**` is where a builder may edit; `**Reads**` is only where a scout may look.
Four charters already delegate here — standup's *"Jira/AzDO fetching itself (**integrations**)"*,
reporting's *"Tracker fetching"*, roadmap's *"Document fetching from Notion/Confluence"*, analysis's
*"Tracker API mechanics"* — and until this paragraph existed, the files they were pointing at were
read by no routine at all.

**Skills** — `.claude/skills/agent-and-state/SKILL.md` (tool conventions)

**Cadence** — weekdays 07:20 UTC, five runs a week, one campaign at a time. This is the fleet's only
building workstream and the only one that runs daily; see
[`routines/cron/integrations-campaign.md`](../routines/cron/integrations-campaign.md) for the run and
[`../integration-campaign.md`](../integration-campaign.md) for the procedure.

## What an integration is

An integration is not a credential field. It is a chain: **credential → verification → scope
discovery → per-mode consumption → what the user finally sees**, and it is only worth as much as its
weakest link. A campaign builds the whole chain for one provider, and a provider is not done because
it has a client.

The four families, all of them in scope for a campaign:

| Family | Examples | What it is |
|---|---|---|
| `ticketing` | Linear, Trello, Shortcut, YouTrack, Asana | where the work is tracked |
| `docs` | Google Docs, Slite, Coda, GitBook | where the team writes things down |
| `code` | GitLab, Bitbucket, Gitea | where the code and its reviews live |
| `ops` | AWS, GCP, Datadog, Sentry, PagerDuty, Teleport | what the team **runs** — see the admission test below |

The first three extend surfaces yeaboi already has (`_COMPONENTS` in `analysis/engine.py` is
`("delivery", "code", "docs")`). The fourth adds one, which is why it alone has an admission test.

Three axes run through every campaign, and none of them is optional:

1. **Edge** — the provider API itself. Cassettes, pagination, auth, rate limits, write-back symmetry.
2. **Reach** — whether the provider actually arrives in planning, analysis, standup, reporting,
   roadmap, poker and performance. A provider that reaches no mode is a dead setting, and a campaign
   is not finished until every mode either consumes it or carries a recorded gap saying why not.
3. **Surface** — whether a user can tell what is connected, whether it still works, and what it
   powers. [`integrations-map.md`](../integrations-map.md) is the record, and its completeness for
   the provider is the campaign's definition of done.

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

An ops provider is **shortlist-eligible like any other**, not a special case — but a candidate
issue for one must name which of the three conditions it satisfies and which mode consumes it
first, and one that fails any condition is a dashboard rather than an integration and is never
shortlisted. Everything else about it is ordinary campaign work.

## The two lanes, in practice

The campaign lane, five runs a week when a provider is approved: everything in
[`../integration-campaign.md`](../integration-campaign.md) — the client, the cassette, the
credential, the wizard step, the settings section, the per-mode wiring, the map row.

The **auto lane**, which is what the fallback branch runs when no campaign is approved, and which is
unchanged: a broken or flaky contract test, a missing pagination guard with a cassette to prove it,
dead code in a retired provider path, doc drift between a tool's docstring and its behaviour — over
the providers that already exist. Nothing in a `**Reads**` path is ever auto, by construction, since
no builder may edit there; and an `**Extends**` path is campaign-only, so it is never auto either.

## Out of scope

`tools/team_learning.py` (**analysis**), despite living in `tools/`. Slack and email delivery, which
live in `standup/delivery.py` and `performance/delivery.py` and belong to those modes.

**Interpretation stays with the mode.** This charter asks whether the data arrives, whether it is
complete, and whether the user can tell — never what a mode should conclude from it. A metric
definition, a threshold, a marker set or a narrative is the consuming workstream's call even when the
data reaching it is yours.
