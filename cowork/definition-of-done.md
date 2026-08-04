# Definition of Done

The single contract. Every cowork routine and every `/ship` run must satisfy all nine items.
Nothing is "done" because the code works — it is done when the loop is closed.

| # | Item | How it is checked |
|---|---|---|
| 1 | **Linear ticket** exists on team `Yeaboi`, labelled `workstream:<name>`, with the PR attached | scribe: `save_issue` + `create_attachment` |
| 2 | **Tests** — a unit test per new function (happy + error), render tests for every `_build_*_screen`, mock tests for LLM-dependent code (success / error fallback / code fences), round-trip tests for new state fields | `make test` |
| 3 | **Lint** | `make lint` |
| 4 | **Security** — ruff SAST + `pip-audit` clean, CodeQL not regressed | `make security` |
| 5 | **Surface parity** — new capability registered in `CAPABILITIES` (+ `PARAM_PAIRS`) in `tests/unit/test_surface_parity.py`, plus a `FeatureTip` in `src/yeaboi/ui/shared/_tips.py`; or a recorded `Exempt("reason")` | `make test` fails without it |
| 6 | **Observability** — the three pillars from `CLAUDE.md`: `logger.info()` on every user action, log paths from `paths.py`, tests | review |
| 7 | **Web bundles** — anything under `frontend/` ⇒ `make web` and the rebuilt `src/yeaboi/web/static/` committed in the *same* commit | CI `web` job |
| 8 | **Notion** — page created or updated under 🤙 yeaboi for any user-facing change | scribe |
| 9 | **Slack** — one `#yeaboi-claude` post: what shipped, PR link, Linear link | scribe |

## Rules

- **Items 1, 8 and 9 are always done by the `cowork-scribe` agent**, never inline. See [crew.md](crew.md).
- **Item 1 happens first**, before any code is written. The ticket is how work is discoverable while
  it is in flight, not a receipt afterwards.
- **Items 8 and 9 happen on merge**, not on PR open — driven by the `pr-merged-close-loop` routine.
- Items 2–7 are the *gate*: they block the PR. A PR that cannot pass them is not opened; the finding
  is filed as a proposal instead.
- **Exemptions are recorded, not assumed.** If an item genuinely does not apply (e.g. item 7 on a
  Python-only change), say so in the PR body in one line. Silence is not an exemption.

## Targets

| System | Target | ID |
|---|---|---|
| Linear | team **Yeaboi** | `a324293a-0fd3-41d3-8730-58192a1babeb` |
| Slack | **#yeaboi-claude** | `C0BMADQQN1Z` |
| Notion | page **🤙 yeaboi** | `3b01bf92-1b06-8163-af24-ea0a77641e17` |
