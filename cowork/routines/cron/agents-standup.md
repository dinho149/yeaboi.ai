# agents standup

**Trigger** — cron `15 6 * * 1-5` (weekdays 06:15 UTC)
**Summary** — the daily agent digest: the agent-authored work that reached the trackers
**Workstream** — [`workstreams/agents.md`](../../workstreams/agents.md)

Not a sweep — this routine *runs the product* rather than surveying the code. It composes
nothing itself: the engine builds the digest, the routine posts it.

**This run is tracker-only, and that is a decision rather than a limitation.** Local session
logs are read from `~/.claude` on the machine the digest runs on. This routine runs in the
cloud, so the only sessions it can ever see are the ones in its own container — which on
2026-08-13 meant it reported "1 session · ~$0.10" about *itself*, while the same window on the
user's laptop held 74 sessions and ~$521. `--no-local-sessions` skips that half rather than
scanning and discarding it, because scanning is what produced the phantom. Local session cost
lives in `yeaboi agents cost` and the TUI, on the machine that has the history.

## Run

1. From the repo root, run:

   ```bash
   uv run python -m yeaboi.cli agents standup \
     --no-local-sessions --tracker-sources github --github-owners dinho149 --format json
   ```

   **`--github-owners` is passed explicitly and must stay that way.** The owners otherwise come
   from `TEAM_ANALYSIS_GITHUB_OWNERS`, falling back to the owner of `STANDUP_GITHUB_REPO` — both
   environment variables, and a cloud routine has no `.env` to read them from. Unset, the scan
   resolves no estate and returns nothing *every* day, writing the reason into `coverage_notes`
   while the digest itself reads like a quiet week. The token needs no such help: a routine
   session is handed `GITHUB_TOKEN`, and the REST reads this scan makes are on the egress
   proxy's allowlist (`tests/fixtures/cowork_github_access_live.json`).

   Trackers are this routine's whole input now, so a scan that reached nothing is not a degraded
   run — it is no run at all. Step 4 says what to post in that case, and it is never the quiet line.

   The JSON on stdout is an `AgentStandupDigest`: narrative, highlights, in-flight agent PRs,
   attention items, per-tracker evidence rows, coverage notes, warnings. `sessions_worked` is
   `0` by construction — never report it as a finding about how busy the agents were.

2. **Read `coverage_notes` and `warnings` before anything else.** They are the run's account of
   what it could not see, and they are what tells a thin digest from a broken one — a missing
   GitHub token and a genuinely quiet Tuesday both arrive as zero rows. Both arrays are on
   stdout with the rest of the JSON.

3. Post ONE message to `#yeaboi-claude`. Costs are estimates and the engine's own wording
   already says so — carry that through rather than restating a number as fact.

   **The block below is a SHAPE, not content. Every value in it is a placeholder.** Read each
   field from the JSON; if a field is empty, the section goes away. On 2026-08-13 the footer
   line of an earlier version of this example — which read "this run saw trackers only" — was
   copied verbatim into a real message that had just reported one session, so the post
   contradicted itself in two lines. Nothing here is text to reuse.

   ```slack
   🧭 **Agents** — <DATE> · <N> agent-authored item(s) across <M> repo(s)

   <digest.narrative, verbatim>

   ⭐ **Highlights** (<n>)

   1. [<title>](<url>) — <what it was>
   2. <highlight line>
   ───────────────────────────

   ⚠️ **Needs attention** (<n>)

   1. [<title>](<url>) — <why a human is wanted>
   ───────────────────────────

   _<one line per entry in digest.coverage_notes>_
   ```

   Rules the shape does not carry:

   - `⭐` and `⚠️` are this message's two section anchors, fixed. Cap each list at 5.
   - **The narrative is the engine's, not yours.** Post `digest.narrative` as written.
   - **The header counts tracker items, never sessions or models.** It has no session count and
     no spend figure to give: this run does not collect them, and a `$0.00` would read as "the
     agents were free today" rather than "nobody asked".
   - **The footer is rendered from `digest.coverage_notes`** — one line per entry, and **omitted
     entirely when the array is empty**. Never write a coverage line of your own, and never
     carry one over from a previous run or from the shape above.
   - **Every entry in `digest.warnings` is reported**, in the ⚠️ section if there is one, or as
     its own italic line beside the coverage notes. A digest that ran without an LLM, or without
     a GitHub token, must say so in the post — that failure is invisible otherwise, and it looks
     exactly like a quiet day.

4. If the digest is empty (no `repo_activity`), post the **degenerate form** — a title line and
   nothing else, as `cowork-scribe.md` allows — plus the coverage notes, which on an empty run
   are the only content there is:

   ```slack
   🧭 **Agents** — <DATE> · no agent-authored tracker activity in the window
   ```

   Absence of evidence is not idleness — never phrase it as "the agents did nothing", and never
   pad the line into a four-line message with empty sections to look like the normal shape. One
   quiet line is the honest report. If a coverage note or warning explains the emptiness (no
   token, no owners configured, a tracker unreachable), **that note is the message** and the
   quiet line is wrong: nothing was scanned, so nothing being found is not a fact about agents.

5. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- Do not run the engine twice; one `agents standup` invocation is the whole job.
- Do not file issues, create tickets, or touch the repo — this routine only posts to Slack.
- Do not re-add `--tracker-sources`-less or local-session runs here to "get more data". A digest
  that reports the routine's own container as the team's agent activity is worse than a short one.
- If the CLI exits non-zero, post the degenerate form with the error's first line and nothing else:
  `🧭 **Agents** — <DATE> · standup engine failed: <first line>`. No sections, no footer, no
  speculation about the cause. This routine is one of the three that may never be silent, and a
  failed run is the case where silence is most easily mistaken for a quiet day — an empty digest
  and no digest at all are different facts and must not produce the same message.
