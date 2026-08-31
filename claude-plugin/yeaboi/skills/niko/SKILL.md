---
name: niko
description: "Ask Niko, yeaboi's global assistant, a question that spans modes — what to look at next, what the AI coding agents cost, where a feature lives, is anything waiting for approval. Read-only: Niko looks things up across planning, standups, retros, poker, performance, reporting, ship, the agentwatch family, ceremonies, provenance and LLM usage, and names the screen that does a thing. Use when the question spans several modes or you don't know which one holds the answer; use the specific tool when you already do."
---

# Asking Niko

Niko is the one tool that reads *across* yeaboi. Every other tool answers about
one mode; Niko reads several and writes the sentence. It is read-only by
construction — there is no write tool in its surface, so it cannot start a run,
change a setting, schedule anything, or delete anything.

## When Niko is the right tool

Reach for `niko_ask` when:

- the question spans modes — "what should I look at today?", "what did my agents
  cost and is anything waiting on me?"
- you do not know which mode holds the answer — "where do I see estimation
  patterns?"
- the user is asking about yeaboi itself — what it does, what a mode is for,
  where a feature lives.

Reach for the **specific tool** when you already know the answer's home:
`agents_usage` for a fresh cost report, `standup_run` to actually run a standup,
`provenance_trace` for one decision's trail. Niko reading history is cheaper and
faster than a mode's full pipeline, but it reads *stored* runs — it never
produces a new report.

## Calling it

`niko_ask(question, conversation_id="", route="", max_rounds=4)`

- **`question`** — plain language. Niko answers in prose, 1-3 sentences for a
  simple ask.
- **`conversation_id`** — omit to start a thread; pass the id from a previous
  result to continue one. The thread is persisted locally, so it survives
  restarts and is the same thread the desktop panel and `yeaboi ask` see.
- **`route`** — where the user is, as a desktop route (`/agents/usage`,
  `/team/retro`). It colours the answer toward that screen. Omit it rather
  than guessing; Niko says it doesn't know which screen rather than inventing
  one.
- **`max_rounds`** — tool rounds before Niko must answer. The default of 4 is
  ample; lower it to 1 for a pure "what is X?" question with no lookup.

## Reading the result

`data` carries:

- **`text`** — the answer. Relay it; do not paraphrase the numbers.
- **`tool_calls`** — what Niko read, each with `ok` and either `result` or
  `error`. This is the answer's evidence. A call with `ok: false` usually means
  "the user hasn't run that yet", not a failure worth reporting as one.
- **`route`** — Niko's navigation suggestion, or `""`. In the desktop it moves
  the window; here it is the screen to *name* to the user.
- **`conversation_id`** — pass it back to continue.
- **`warnings`** — relay these. `tool-round limit` means the answer may be
  partial; an `AI answers unavailable` warning means Niko fell back to a
  signpost built from local registries and answered nothing from the user's data.

## What Niko will refuse

Asked to run, change, schedule or delete something, Niko says it can't and names
the screen instead. That is correct behaviour, not a failure — if the user
actually wants the thing done, call the mode's own tool (`standup_run`,
`agents_usage`, `plan_generate`, …) rather than asking Niko again.

Niko is also told never to state a number it did not just read. If it says it
doesn't know, take that at face value rather than re-asking with a nudge.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. An `llm_mode` of
`fallback` means no model was available and the answer is the local signpost —
say so rather than presenting it as an answer from the user's data.
