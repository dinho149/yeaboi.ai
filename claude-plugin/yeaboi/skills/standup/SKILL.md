---
name: standup
description: "Run a daily scrum standup with yeaboi: collect ticketing, code, and documentation activity, score sprint confidence, and summarize per member. Also reviews standup meeting transcripts to find what the report missed and why. Use when the user asks for a standup, daily scrum, 'what did the team do', a sprint progress check, or wants to check a standup report against a recording/transcript of the meeting."
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

4. **Check a standup against its meeting.** When the user has a transcript of
   the standup itself — or says the report missed something someone mentioned —
   call `standup_review`. It reads `.txt/.md/.vtt/.srt/.json` transcripts from
   `~/.yeaboi/transcripts` (or specific files via `transcript_paths`), checks
   what each person said they did against the evidence the report actually had,
   and diagnoses each gap. Present the two halves separately, because they need
   different actions:

   If the user pastes the transcript into the conversation, or you already have
   the text from a meeting-notes document, pass it as `transcript_text` — it is
   saved into `~/.yeaboi/transcripts` and reviewed like any other file. **Do not
   ask them to save it somewhere first.** Pass `standup_date` alongside it when
   the meeting was not today; for pasted text that date wins outright.
   - `gaps` are faults in yeaboi itself (a missing integration, a capability the
     collectors lack, a summary that dropped collected evidence). These are
     drafted as GitHub issues.
   - `config_suggestions` are the user's to fix and carry an exact `remedy`.
     They are never filed.

   **`file_issues` writes real, public GitHub issues** on the yeaboi repository.
   Never set it without asking the user first, and show them the gap titles
   before you do. The default drafts everything locally so it can be reviewed.
   Use `standup_gaps` to read back past reviews and see which gaps are already
   filed, which recurred, and their issue numbers.

5. **Configuration.** To view or change the standup setup (time, weekdays,
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
   `transcript_dir` adds an external folder to the transcript sweep (a Zoom or
   Google Meet recordings folder), and `transcript_review_enabled` turns off the
   automatic review that runs before each standup. Both are also settable in the
   TUI now, under Standup › Review › "Change my transcript folders…", which
   offers the detected recording folders by name — suggest that when the user
   would rather point-and-pick than find the path themselves.
   Installing the OS schedule that fires it daily is done from the yeaboi TUI.

If there are no sessions yet, suggest planning first (`/yeaboi:plan-sprint`) —
the standup needs a session for sprint dates and team context.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint` (usually credentials — `yeaboi --setup`); don't
retry blindly. `llm_mode: "fallback"` means no LLM was reachable and the summary
is a deterministic skeleton — suggest `yeaboi --setup`.
