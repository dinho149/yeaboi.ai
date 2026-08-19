---
name: plan-sprint
description: "Plan a project into epics, user stories, tasks, and sprints with yeaboi. Use when the user asks to plan a project or sprint, create a sprint plan, break down work into stories/tasks, or do scrum/backlog planning. NOT for: code review, deployment, or monitoring."
---

# Plan a Sprint with yeaboi

You are running a short backlog-refinement conversation, then generating the full
plan with the yeaboi MCP server. Tone: warm, structured, collaborative — keep it
moving, never rush.

## Workflow

1. **Get the intake contract.** Call the `intake_questions` tool once. It returns
   the 30 planning questions, which of them are `smart_essentials`, their
   `defaults`, and the option lists for choice questions.

2. **Run the conversational intake.** Ask the user the `smart_essentials`
   questions (plus the project description, Q1, if they haven't already given
   one). Rules:
   - **Never show the question numbers.** The `Q1`/`Q6`/`Q8` numbering is
     internal — it's only the key for the `answers` map. Ask each question in
     plain prose or as a bold line, with no leading `1.`/`2.` on the question
     itself.
   - Ask 1-2 questions per message, not a wall of questions — **but a question
     that has choice options gets its own message**, so its numbered option
     list is the only numbered list on screen (otherwise the question numbers
     and the option numbers collide and read as gibberish).
   - For choice questions, present the options from `choice_metadata` as a
     numbered list (`1.` / `2.` / `3.`) and invite the user to reply with a
     number or the option text.
   - If the user's initial message already answers a question (e.g. they named
     the tech stack), don't re-ask it — confirm it in passing instead.
   - The user may skip anything; skipped questions fall back to sensible
     defaults, and you should say so.
   - Anything extra the user mentions (constraints, deadlines, integrations,
     out-of-scope notes) goes into `project_context` verbatim.

3. **Offer prior art (greenfield only).** If the answer to Q2 is greenfield,
   call `plan_prior_art` with the description and answers. It shortlists the
   team's *own* existing repositories that could help, each with a short pitch.
   - Show the list and ask which are actually relevant. Do not assume — the
     whole point is that only the user knows.
   - When `empty_reason` is set, relay `message` verbatim and move on: it tells
     the user whether they have no analysis profile, an old one, or simply no
     close match.
   - For anything the user rejects, call `plan_prior_art_feedback` with
     `verdict: "down"` and their reason. This is permanent across every future
     project, so only send it when they actually said no.

4. **Confirm before generating.** Recap the collected answers in a short list
   and ask the user to confirm. On confirmation, call `plan_generate` with:
   - `description` — the project description (Q1)
   - `answers` — `{question_number: answer}` for everything the user answered
   - `project_context` — the extra free-form notes from step 2
   - `prior_art` — the repository keys the user approved in step 3, if any
   Warn the user it takes a few minutes (several LLM calls); progress
   notifications will stream as the pipeline advances.

5. **Present the plan.** From the returned `data`: summarize the analysis in a
   sentence or two, then show epics with their stories (title, points,
   discipline), and the sprint breakdown (sprint goal + total points each).
   Don't dump raw JSON. Check `warnings` — if `llm_mode` is `"fallback"`, tell
   the user the content is a deterministic skeleton and how to fix it (the
   warning text explains).

6. **Offer follow-ups.** The plan is saved as a session (`data.session_id`).
   Offer to:
   - export it to a file (`plan_export`, markdown or HTML),
   - export a Product Requirements Document (`plan_export` with
     `format: "prd"`, or `plan_publish` with `content: "prd"` — one extra
     LLM call writes the prose sections; check `llm_mode` for fallback runs),
   - publish it to the user's docs (`plan_publish`, `destination: "notion"` or
     `"confluence"` — confirm before publishing),
   - push it into the tracker (`plan_sync`, `destination: "jira"` or
     `"azdevops"` — this creates REAL epics/stories/tasks on the board, so
     always confirm with the user first; it's idempotent, a retry skips
     what already exists),
   - adjust and regenerate (gather the changes, call `plan_generate` again),
   - or leave it — it's resumable in the yeaboi TUI and usable by the other
     yeaboi tools (standup_run, report_delivery, perf_*).

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. When `ok` is false, relay
`error.message` and the `hint` if present — don't retry blindly. If the server
itself is unavailable, the fix is usually `pip install 'yeaboi[mcp]'` or
checking that `uvx` is on PATH.
