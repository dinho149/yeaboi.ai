---
name: standup
description: "Run a daily scrum standup with yeaboi: collect ticketing, code, and documentation activity, score sprint confidence, and summarize per member. Use when the user asks for a standup, daily scrum, 'what did the team do', or sprint progress check."
---

# Daily Standup with yeaboi

1. **Run it.** Call `standup_run` (blank `session_id` targets the most recent
   planning session). Leave `deliver` false — you present the report; only set
   `deliver: true` if the user explicitly asks to send it to their configured
   channels (Slack/email/desktop).

2. **Present the report.** From `data`: lead with sprint day and the confidence
   score + rationale, then the team summary, then per-member updates (yesterday
   / today / blockers style), including each person's General Overview,
   `ticketing_summary`, `code_summary`, and `documentation_summary` with their
   category-specific links. Surface any `warnings` (e.g. a tracker returned
   401) — they explain missing sections.

3. **History.** For trends or "how have standups been going", call
   `standup_history` and summarize confidence over time.

4. **Configuration.** To view or change the standup setup (time, weekdays,
   delivery channels, member aliases, user name, tracker sources, and selected
   team), use `standup_config_get` / `standup_config_set`. Call
   `standup_members` first to preview candidates from Jira, Azure DevOps, or
   both, and `standup_repositories` to discover GitHub repository/Azure project choices.
   Save an explicit `code_sources`, `github_repositories`, and
   `azdo_projects` scope; GitHub identifiers are `owner/repo`, while each Azure
   project dynamically covers all accessible repositories. Save `documentation_sources` as a subset
   of `confluence`/`notion`; documentation files in selected repositories are
   included automatically. The selected roster is authoritative:
   unselected authors are excluded from member updates and team totals.
   Activity providers and repository/project scans run concurrently with
   bounded provider limits; a single final synthesis keeps the four summary
   sections consistent.
   Installing the OS schedule that fires it daily is done from the yeaboi TUI.

If there are no sessions yet, suggest planning first (`/yeaboi:plan-sprint`) —
the standup needs a session for sprint dates and team context.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint` (usually credentials — `yeaboi --setup`); don't
retry blindly. `llm_mode: "fallback"` means no LLM was reachable and the summary
is a deterministic skeleton — suggest `yeaboi --setup`.
