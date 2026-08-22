# Integrations map

Which provider reaches which mode, through what, and where a mode deliberately does not consume one.
Maintained by [`routines/cron/integrations-campaign.md`](routines/cron/integrations-campaign.md),
whose angle 3 closes a provider's row; the charter is
[`workstreams/integrations.md`](workstreams/integrations.md), and the procedure is
[`integration-campaign.md`](integration-campaign.md).

**This file is the campaign's definition of done.** A provider is finished when it has a matrix row
with no bare `—` — every mode either consumes it or carries a line in *Recorded gaps* saying why not
— and a Per-provider section naming its credential, its verification probe and its scope discovery.

This file exists because "connected" and "used" are different states and only one of them is visible.
A credential can pass its wizard probe, be stored in `~/.yeaboi/.env`, and change nothing a user ever
sees. The **Recorded gaps** section is the point of the file: it turns a silent absence into an
answered question, the same job `Exempt("reason")` does in `CAPABILITIES`.

**Verified 2026-08-06.** Line numbers rot; function names are the durable reference.

## The chain

Every provider is worth what its weakest link is worth:

```
credential (config.py getter)
   → verification (ui/provider_select/_verification.py — wizard only)
      → scope discovery (code_scope.py, TEAM_ANALYSIS_* vars, _available_*_sources)
         → per-mode consumption (the matrix below)
            → what the user finally sees
```

## Reach matrix

`R` read · `W` write · `—` not consumed · `·` not applicable

**Inbound only.** A cell says whether a mode *reads a provider as a source*. Publishing an export
to Notion or Confluence is a destination, not reach — every mode with an export picker
(`ui/shared/_export_picker.py::_MODE_STYLES`: planning, analysis, standup, retro, performance,
reporting) can publish to both whenever the credentials resolve, and that is not recorded here.
A `W` cell means writing back into the *source of record* — sprint creation, points, gap issues.

| Provider | planning | analysis | standup | reporting | roadmap | poker | performance | retro |
|---|---|---|---|---|---|---|---|---|
| **Jira** | R + W | R | R | R | — | R + W | R | · |
| **Azure DevOps (Boards)** | R + W | R | R | R | — | R + W | R | · |
| **Azure DevOps (Repos)** | — | R | R | R | — | · | — | · |
| **GitHub** | R | R | R + W | R | — | · | — | · |
| **Confluence** | R + W | R | R | R | R | · | — | · |
| **Notion** | R + W | R | R | R | R | · | — | · |
| **Slack** | — | — | R + W | — | — | · | — | · |
| **Local git** | — | — | R | — | — | · | — | · |
| **Local filesystem** | R | — | — | — | R | · | — | · |
| **`holidays` package** | R | — | — | — | — | · | — | · |
| **Ops / infra** | — | — | — | — | — | — | — | — |

The bottom row is the charter's largest gap and is a row of dashes by construction, not by decision.
See *The ops family, and its admission test* in the charter.

## Per provider

### Jira — `tools/jira.py`, `jira_sync.py`

Credential `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_PROJECT_KEY` · verified by
`_verify_jira` (`GET /rest/api/3/myself`) · scope from `JIRA_PROJECT_KEY`.

- planning — `jira_fetch_velocity`, `jira_fetch_active_sprint` in `agent/nodes.py`; writes through the
  three `@tool`s (`jira_create_epic`/`_story`/`_sprint`, all `ToolRisk.WRITE`, all gated by
  `human_review`) and the batch `jira_sync.sync_all_to_jira` behind the TUI review screen
- analysis — `team_learning._fetch_jira_history` / `_fetch_jira_actuals` / `_fetch_jira_story_extras`
- standup — `jira_recent_activity`, `jira_open_tickets`, `jira_active_sprint_progress`
- reporting — `jira_recent_activity`, `jira_list_sprints`
- poker — `jira_sprint_issues`, `jira_backlog_issues`, and `jira_update_issue_fields` writes points back
- performance — `jira_recent_activity`
- shared — `team_roster.py` uses `jira_assignee_roster`

### Azure DevOps — `tools/azure_devops.py` (2.7k LOC), `azdevops_sync.py`

Credential `AZURE_DEVOPS_TOKEN` / `_ORG_URL` / `_PROJECT` / `_TEAM` · verified by `_verify_azdevops`
(`GET /{project}/_apis/wit/workitemtypes`) · scope from `TEAM_ANALYSIS_AZDO_PROJECTS` and the repo
allowlist vars.

Boards and Repos are **separate reach paths** and fail independently — standup keeps
`SOURCE_AZDO_REPOS` distinct from `SOURCE_AZDO` precisely so a repo-API failure never hides work
items. Mirrors Jira across planning / analysis / standup / reporting / poker / performance;
`azdevops_sync` is the write-back pair to `jira_sync` and the two are held behaviourally symmetric by
the charter.

### GitHub — `tools/github.py`

Credential `GITHUB_TOKEN` (optional — public repos work unauthenticated) · verified by
`_verify_vc_token` (`GET api.github.com/user`) · scope from `TEAM_ANALYSIS_GITHUB_OWNERS` and
`discover_github_repositories`.

- planning — `github_read_repo` and `github_read_file` via `agent/repo_signals.py`; `github_read_readme`
  only as a registered `@tool` the planning graph may choose, never from repo_signals
- analysis — `github_analysis_inventory`, `github_recent_commits` / `_prs`, `github_changed_files`
- standup — `github_recent_commits` / `_prs` / `_reviews`; **writes** gap issues via
  `standup/gap_issues.py`
- reporting — the same standup collector, driven by `reporting/context.py::_code_signals` with
  `SOURCE_GITHUB` / `SOURCE_AZDO_REPOS`; merged PRs are the `code` component of
  `activity.py::SOURCE_COMPONENTS`
- also — `feedback.py` creates issues on `FEEDBACK_REPO` through the same shared client. Neither write
  path is a registered `@tool`, so neither carries a `TOOL_RISK` row.

### Confluence — `tools/confluence.py`

Credential `CONFLUENCE_BASE_URL` / `_EMAIL` / `_API_TOKEN`, **each falling back to its `JIRA_*`
equivalent**, plus `CONFLUENCE_SPACE_KEY` · verified by `_verify_confluence`.

Read by planning (`confluence_search_docs`, `confluence_read_page`), analysis
(`analysis/doc_quality.py`), standup (`confluence_recent_pages`), reporting
(`context.py::_doc_signals`, which calls the same `collect_doc_pages`) and roadmap
(`ingest_source`). Separately a publish *destination* for every mode's export picker
(`export_targets.publish_to_confluence`) — see the matrix legend on why that is not a reach cell.

The credential fallback means **configuring Jira silently enables Confluence tools**. Convenient, and
stated nowhere on screen.

### Notion — `tools/notion.py`

Credential `NOTION_TOKEN` / `NOTION_ROOT_PAGE_ID` / `NOTION_EXPORT_PARENT_PAGE_ID` · verified by
`_verify_notion` (`GET /v1/users/me`). Same five readers as Confluence, and the same separate
publish destination via `export_targets.publish_to_notion`.

### Slack — `tools/slack.py`, `slack/`

Credential `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` / `SLACK_ALLOWED_MEMBER_IDS` (the webhook
`SLACK_WEBHOOK_URL` stays, and stays sufficient for delivery) · verified by `yeaboi slack check`
(`auth.test` for identity and scopes, then a `conversations.history limit=1` for readability) ·
scope is one channel by id, never workspace-wide.

**The only provider on this map that became a source by growing a second credential.** For its whole
life Slack was a destination and could not have been anything else: an incoming webhook answers a
POST with the literal body `ok` and no message id, so yeaboi could never identify its own message and
a reaction on it was unreadable *by construction*. A bot token buys `chat.postMessage`, which returns
`{channel, ts}` — and that ts is the anchor everything inbound resolves through.

- standup — `R`: a 👍/👎 on a threaded signal reply writes `standup_practice_feedback` through
  `practice_feedback.apply_verdict`, and a typed reply becomes an `OP_NOTE` on the run through
  `artifacts/engine.py::apply_artifact_edits`. `W`: both of those write back into the stored run,
  which is the source of record for that day.
- ceremonies — ⏸/▶️/🚫 on the post calls `set_enabled` / `set_skip_next`. Not a matrix column;
  ceremonies is the clock, not a mode.
- shared — `slack/identity.py` binds a Slack member id to a roster name, used only to choose the
  author string on a correction. It never gates an act.

Two things about it are unlike every other provider here and are deliberate. **It exposes no
`@tool`** — an LLM-callable `slack_post_message` would let prompt-injected text in a Jira title
reach a team channel — and `slack poll` is likewise never an MCP tool. And **the wizard has no
Slack phase**: `slack check` covers verification, and a `_verify_slack` with no phase behind it
would be dead code with a green checkmark.

### Local git, local filesystem, `holidays`

No credential and no network. `local_git_recent_commits` (standup only), `tools/codebase.py` under
`fs_policy` (planning and roadmap's PDF path), `detect_bank_holidays` (planning's sprint calendar).

## Recorded gaps

A deliberate absence, with the reason. Do not re-propose these.

| Provider → mode | Why not |
|---|---|
| Jira / AzDO Boards → roadmap | Roadmap intake reads a *document* — a tracker holds what was already decided, which is the output of the pipeline roadmap feeds, not an input to it. |
| Any provider → retro | Nothing is *read* into a retro: the board is sourced from what participants type in the session. `CAPABILITIES` records the matching TUI-only exemption. (Retro still *publishes* to Notion/Confluence — a destination, not reach.) |
| GitHub / AzDO Repos / Confluence / Notion → performance | Performance reads the tracker only (`performance/activity.py`). A review window is assessed on delivered, attributable work items; the code and doc signals reporting gathers are per *sprint*, not per person, so they have nowhere to sit in the per-member narrative. |
| Local git → anything but standup | It answers "what did this machine do today", which is a standup question and nobody else's. |
| Confluence / Notion → poker | Estimation reads tickets, not prose. |
| Slack → anything but standup | Slack is read back through an *anchor* — a row saying which run a delivered post was about — and only the standup emits a run id and a correctable artifact today (`ceremonies/catalog.py`'s `artifact_kind` / `emits_run_id`). A reporting post can still be paused or skipped from Slack, because that addresses the **ceremony**; nothing in it can be answered, because there is no stored artifact to answer against. This is a catalog row away, not a rewrite. |

## Open gaps

No recorded reason yet — each is a question for the owning workstream, not a decision taken here.

| Gap | Owner | Note |
|---|---|---|
| Ops / infra providers reach no mode | integrations | The whole bottom matrix row. Six modes have a question an ops provider would answer; see the charter's admission test. |
| No provider is verifiable after setup | tui-ux | Five `_verify_*` probes exist and the settings screen calls none of them. **This is the integration campaign's one named prerequisite** — angle 2 degrades to wizard-probe-only until it lands, and says so in its PR body. It is tui-ux's because every provider then reuses one surface; built inside a campaign, the second campaign would have nothing to do for that angle. |
| Nothing tells a user what a connected provider powers | integrations | This file is the maintainer's copy of an answer no user can reach. |
| Adding a provider costs ~13 edit sites | integrations | There is no fetcher protocol; `CoverageTracker` and the retry/classify closure inside `collector.collect_recent_activity`
between them already define most of one. |
