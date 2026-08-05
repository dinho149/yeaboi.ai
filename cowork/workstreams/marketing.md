# marketing

**Owns** — nothing in the repo. Reads everything, writes only to Notion under 🤙 yeaboi
(`3b01bf92-1b06-8163-af24-ea0a77641e17`).

**Cadence** — Sat 08:00 UTC, subject advancing one row per run

## Subject rotation

Seven subjects, one per run, so each comes round every seven weeks. It used to be one per weekday,
which re-drafted every subject weekly against a codebase that does not change weekly — the drafts
had nothing new in them by design, not by accident.

| # | Subject | Read |
|---|---|---|
| 1 | Daily Standup | `src/yeaboi/standup/`, `docs/docs/modes/standup.html` |
| 2 | Retro + Planning Poker | `retro/`, `poker/`, `docs/docs/modes/{retro,poker}.html` |
| 3 | Stakeholder Reporting | `reporting/`, `docs/docs/modes/reporting.html` |
| 4 | Team Analysis | `analysis/`, `team_profile.py`, `docs/docs/modes/team-analysis.html` |
| 5 | Planning + Roadmap Intake | `agent/`, `roadmap/`, `docs/docs/modes/planning.html` |
| 6 | Integrations | `tools/`, `docs/docs/integrations-exports.html` |
| 7 | Performance (1:1s + reviews) | `performance/`, `docs/docs/modes/performance.html` |

The position is not stored anywhere — GitHub issues are the only shared state in this system, and a
Notion page title is enough. The newest `Draft — <subject> — <date>` page names the last subject
drafted; take the next row, wrapping at 7.

## What a good draft is

- **Grounded in the code you read this run**, not in the marketing page. If the docs and the code
  disagree, that disagreement is the more interesting article — and it is also a proposal issue for
  the owning workstream.
- **One concrete problem, one concrete mechanism.** "How yeaboi tells a real blocker from a quiet
  day" beats "AI-powered scrum".
- **Honest about limits.** Beta features are labelled beta. Fallback mode exists and is worth
  explaining.
- **No invented numbers, no invented customers, no invented quotes.** Ever.
- 600–1,000 words. A draft nobody reads to the end is not a draft.

## Run

Read this week's subject, draft the page, and have `cowork-scribe` create it as a sub-page under
🤙 yeaboi titled `Draft — <subject> — <ISO date>`. File a `cowork:proposal` issue only if the read
turned up a genuine docs/code contradiction. The daily digest carries the draft link; do not post to
Slack yourself.

## Out of scope

Every file in the repo. Marketing never edits `docs/index.html`, `docs/docs/`, or `src/` — a change
it wants there is a proposal issue for **web-ux**.
