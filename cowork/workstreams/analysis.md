# analysis

**Owns** — `src/yeaboi/analysis/`, `src/yeaboi/team_profile.py`, `team_profile_exporter.py`
(3.2k LOC), `team_roster.py`, `src/yeaboi/tools/team_learning.py` (7.1k LOC), `mcp/tools_team.py`,
`claude-plugin/yeaboi/skills/team-analysis/`, `tests/unit/test_analysis_*.py`,
`test_doc_quality.py`, `test_team_profile*.py`

**Reads** — `tests/unit/test_surface_parity.py`, for the `team-learning` row only. The registry is **platform**'s
(`workstreams/platform.md`): you may look, no builder of yours may edit, and anything you find there
is `lane: propose` with `owner: platform`, filed against platform's slots
([`house-rules.md`](../house-rules.md), *Stay in your paths*). What is yours is knowing whether a
recorded reason is still **true** — platform owns the file and cannot own that judgement for
seventeen modes. Declaring it here is what turns the routing from an inference into a fact.

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — Thu 06:30 UTC, weekly

`team-learning` carries a recorded `Exempt`: *"no plugin skill yet — tracked gap"*. Like roadmap's
gaps, that is standing work — propose it with a concrete design rather than letting "tracked" mean
"never". It is a `**Reads**` find, so it files under `workstream:platform` and takes one of
platform's slots, never one of yours.

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

**Correcting a detector is not redefining one.** A marker that matches something it was never meant
to match — a missing word boundary, an unanchored pattern, a case fold that swallows a surname — is
a bug in an existing rule, and it rides the auto lane on the same admission ticket as any other: a
regression test that fails before the fix and passes after, naming the input that was misclassified.
What still proposes is the *rule* — adding a marker, removing one, re-weighting one, or moving a
threshold — because that changes what the table is counting rather than whether it counted
correctly. The test is mechanical, and it is the one to apply: **if you can name an input the
current code gets provably wrong, it is a correction; if you are arguing about what the right answer
should be, it is a definition, and it proposes.**

This is the same line `house-rules.md` draws between behaviour and copy, on the axis this charter
happens to sit on: behaviour has a right answer the tests can hold, and a false positive is a wrong
answer rather than a different one.

## Out of scope

The team-profile export's React components and Block rendering (**web-ux**). Tracker API mechanics
(**integrations**).

**integrations** may append a provider to `analysis/engine.py`'s `_COMPONENTS` and its
`_available_*_sources` probes from a campaign run (`house-rules.md`, **Extends**) — registration
only. Every threshold, marker and metric definition in that file stays yours.
