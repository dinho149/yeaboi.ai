---
name: agents-standup
description: "(beta) Run the daily AI-agent standup with yeaboi: what the user's coding agents did — local sessions worked (with estimated cost) plus agent-authored commits and PRs found in GitHub/Azure DevOps. Use when the user asks what their agents did, wants an agent standup/digest, or a daily agent activity summary."
---

# Agent standup workflows with yeaboi

> **Beta.** The Agents modes are in beta — detection is a lower bound: agents
> that leave no marker are invisible. Never present absence of evidence as
> idleness, and present costs as estimates.

1. **Run the digest** with `agents_standup_run`. Defaults cover everything
   since the previous working day (a Monday run reaches back to Friday) and
   scan both trackers best-effort; narrow with:
   - `days` — explicit look-back window (0 = previous-working-day default).
   - `tracker_sources` — `["github"]`, `["azdo"]`, or `[]` for local-only.
   - `github_owners` / `azdo_projects` — scope the tracker scan.
   - `deliver` — posts to the configured Slack webhook. Leave it false and
     present the digest yourself; only set true if the user explicitly asks.

2. **Present it like a standup**: lead with the `narrative`, then `highlights`
   (shipped work), `in_flight` (open agent PRs), and `attention_items` (what
   needs a human). The `session_summaries` and `repo_activity` rows are the
   evidence behind every line.

3. **Surface `coverage_notes` and `warnings`** — a tracker that couldn't be
   scanned (missing token) is a visible gap, not an empty day.

4. **Compare days** with `agents_standup_history` (newest first) instead of
   recomputing.

5. **Privacy.** Only aggregates and tracker metadata are read; session
   transcripts are never copied or uploaded. Exports auto-save under
   `~/.yeaboi/exports/agentwatch/standup/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `llm_mode: "fallback"`
means no LLM was reachable — the evidence rows are still real, only the
narrative prose fell back to deterministic lines.
