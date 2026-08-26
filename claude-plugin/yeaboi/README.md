# yeaboi — best friend to engineers and agents, as a Claude Code plugin

Sprint planning, daily standups, stakeholder delivery reports and engineer 1:1
prep for your team — plus cost, daily digests and security posture for the
coding agents working alongside it. Without leaving your coding agent.

## What you get

- **MCP server** (started automatically via `uvx`): 57 tools over the engines —
  planning (`plan_generate`, `intake_questions`, `plan_get`/`plan_export`/
  `plan_publish`/`plan_sync`, `plan_prior_art`), sessions, standups (including
  `standup_review` and `standup_practice_feedback`), delivery reports, retros,
  poker, performance, team analysis, roadmap intake, anonymisation, provenance,
  artifact edits, `ship`, ceremonies, the **Agents family** (`agents_usage`,
  `agents_advisor_run`, `agents_standup_run`, `agents_security_scan` and their
  histories), and the two-way **Slack** lane (`slack_inbound_history`,
  `slack_identities_list`). Run `/mcp` in Claude Code for the live list.
- **Skills**: `/yeaboi:plan-sprint` (guided conversational intake → full sprint
  plan), `/yeaboi:standup`, `/yeaboi:delivery-report`, `/yeaboi:performance`,
  `/yeaboi:team-analysis`, `/yeaboi:ceremonies`, `/yeaboi:provenance`,
  `/yeaboi:ship`, `/yeaboi:slack-inbound`, and the Agents family
  (`/yeaboi:agents-usage`, `/yeaboi:agents-advisor`, `/yeaboi:agents-standup`,
  `/yeaboi:agents-security`).

## Requirements

- `uv` installed (the server runs via `uvx --from 'yeaboi[mcp]' yeaboi-mcp`).
- **No API key needed in Claude Code** — generation runs through MCP sampling,
  i.e. the model you're already talking to. In clients without sampling
  support, yeaboi falls back to its own configured provider
  (`~/.yeaboi/.env`, set up with `yeaboi --setup`).
- Optional: Jira / Azure DevOps / GitHub credentials in `~/.yeaboi/.env` power
  the standup, delivery-report, and performance tools.

## Install

```bash
claude plugin marketplace add yeaboi-ai/yeaboi.ai
/plugin install yeaboi@yeaboi
```

### Testing a local checkout (development)

`claude --plugin-dir /path/to/repo/claude-plugin/yeaboi` loads the **skills** from
your checkout — but `.mcp.json` launches the server via `uvx --from 'yeaboi[mcp]'`,
which resolves yeaboi from **PyPI** (the last published release, not your branch).
To test unreleased server changes, register a dev server pointing at the checkout:

```bash
claude mcp add yeaboi-dev -- uv run --project /path/to/repo --extra mcp yeaboi-mcp
```

## Notes

- Every tool returns `{ok, llm_mode, warnings, data}`. `llm_mode: "fallback"`
  means no LLM was reachable and `data` is a deterministic skeleton.
- Plans generated here are saved as yeaboi sessions — resumable in the yeaboi
  TUI (`uvx yeaboi`) and shared with the standup/reporting/performance tools.
- Past retrospectives are readable via `retro_history`; the live retro board
  itself stays in the yeaboi TUI (it's a real-time browser page).
- Server logs: `~/.yeaboi/logs/mcp/mcp.log`.
