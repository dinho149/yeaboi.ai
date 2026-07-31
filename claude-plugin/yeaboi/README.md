# yeaboi — AI Scrum Master plugin for Claude Code

Sprint planning, daily standups, stakeholder delivery reports, and engineer
1:1 prep — without leaving your coding agent.

## What you get

- **MCP server** (started automatically via `uvx`): 25 tools — `plan_generate`,
  `plan_get`/`plan_export`/`plan_publish`/`plan_sync`, `intake_questions`,
  `sessions_list`/`session_get`/`session_delete`, `usage_get`,
  `standup_run`/`standup_history`/`standup_members`/`standup_repositories`/`standup_config_get`/`standup_config_set`,
  `report_delivery`, `perf_roster`,
  `perf_one_on_one_prep`/`perf_one_on_one_complete`/`perf_six_month_review`/
  `perf_note_add`, `retro_history`/`retro_export`, `team_analyze`,
  `team_profile_get`, `team_compare_plan_to_actuals`.
- **Skills**: `/yeaboi:plan-sprint` (guided conversational intake → full sprint
  plan), `/yeaboi:standup`, `/yeaboi:delivery-report`, `/yeaboi:performance`,
  `/yeaboi:team-analysis`.

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
claude plugin marketplace add omardin14/yeaboi.ai
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
