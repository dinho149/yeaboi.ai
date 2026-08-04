# marketing

**Owns** — nothing in the repo. Reads everything, writes only to Notion under 🤙 yeaboi
(`3b01bf92-1b06-8163-af24-ea0a77641e17`).

**Cadence** — daily 08:00 UTC, subject rotating by weekday

## Subject rotation

| Day | Subject | Read |
|---|---|---|
| Mon | Daily Standup | `src/yeaboi/standup/`, `docs/docs/modes/standup.html` |
| Tue | Retro + Planning Poker | `retro/`, `poker/`, `docs/docs/modes/{retro,poker}.html` |
| Wed | Stakeholder Reporting | `reporting/`, `docs/docs/modes/reporting.html` |
| Thu | Team Analysis | `analysis/`, `team_profile.py`, `docs/docs/modes/team-analysis.html` |
| Fri | Planning + Roadmap Intake | `agent/`, `roadmap/`, `docs/docs/modes/planning.html` |
| Sat | Integrations | `tools/`, `docs/docs/integrations-exports.html` |
| Sun | Performance (1:1s + reviews) | `performance/`, `docs/docs/modes/performance.html` |

## What a good draft is

- **Grounded in the code you read today**, not in the marketing page. If the docs and the code
  disagree, that disagreement is the more interesting article — and it is also a proposal issue for
  the owning workstream.
- **One concrete problem, one concrete mechanism.** "How yeaboi tells a real blocker from a quiet
  day" beats "AI-powered scrum".
- **Honest about limits.** Beta features are labelled beta. Fallback mode exists and is worth
  explaining.
- **No invented numbers, no invented customers, no invented quotes.** Ever.
- 600–1,000 words. A draft nobody reads to the end is not a draft.

## Run

Read today's subject, draft the page, and have `cowork-scribe` create it as a sub-page under
🤙 yeaboi titled `Draft — <subject> — <ISO date>`. File a `cowork:proposal` issue only if the read
turned up a genuine docs/code contradiction. The daily digest carries the draft link; do not post to
Slack yourself.

## Out of scope

Every file in the repo. Marketing never edits `docs/index.html`, `docs/docs/`, or `src/` — a change
it wants there is a proposal issue for **web-ux**.
