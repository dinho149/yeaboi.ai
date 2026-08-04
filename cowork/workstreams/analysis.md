# analysis

**Owns** — `src/yeaboi/analysis/`, `src/yeaboi/team_profile.py`, `team_profile_exporter.py`
(3.2k LOC), `src/yeaboi/tools/team_learning.py` (7.1k LOC), `mcp/tools_team.py`,
`claude-plugin/yeaboi/skills/team-analysis/`, `tests/unit/test_analysis_*.py`,
`test_doc_quality.py`, `test_team_profile*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — Thu 06:30 UTC, weekly

`team-learning` carries a recorded `Exempt`: *"no plugin skill yet — tracked gap"*. Like roadmap's
gaps, that is standing work — propose it with a concrete design rather than letting "tracked" mean
"never".

## Standing concerns

- **Small-sample honesty.** Metrics computed over a handful of PRs must say so rather than render a
  confident percentage. Any new metric needs its own honesty path.
- **AI-usage markers must stay precise.** The detectors (Codex/agent/branch markers) are the feature's
  credibility. A marker that over-matches turns the whole table into noise.
- **Activity-targeted scanning** — repo selection follows where the team actually worked (AzDO PR +
  commit activity), never alphabetical. A regression to alphabetical is a finding.
- **`Cell.tone` is the one documented presentation field in a payload**, because its thresholds are
  per-column *and* directional (80% completion is good, 80% spillover is not). It is a word, gated
  against `TONES` in `Profile.tsx`. Any second presentation field in a payload is a finding.
- **Dual-source runs** (Jira + AzDO in one pass) must keep the two sources visibly separated in the
  output — merging them silently misattributes work.
- **Cold-run caps** exist so a first run terminates. Removing one to "get better numbers" is a
  proposal with a cost estimate, not an auto fix.

## Auto lane, in practice

Broken tests, dead scan paths, doc drift. Metric definitions, thresholds, and marker sets propose —
they change numbers people have already seen.

## Out of scope

The team-profile export's React components and Block rendering (**web-ux**). Tracker API mechanics
(**integrations**).
