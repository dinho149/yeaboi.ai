# platform

**Owns** — `src/yeaboi/cli.py` (2.5k LOC) and `__main__.py` (`python -m yeaboi`, the
entry the desktop's bundled interpreter starts the backend through),
`config.py`, `paths.py`, `logging_setup.py`,
`_compat.py` and `timeparse.py` (the two shims the supported Python range rests on —
both are deleted, not edited, when the floor rises to 3.11),
`telemetry.py`, `feedback.py`, `setup_wizard.py`, `update_check.py`, `changelog.py`,
`provenance/` (the cross-mode tamper-evident decision chain),
`ceremonies/` (the clock any mode can run on — it owns the OS-job installer and the
delivery channels the standup was promoted out of, so `standup/scheduler.py` and
`standup/delivery.py` are shims over it and yours too),
`slack/` (the inbound half of that clock — what a team said back to a delivered post;
`tools/slack.py` is the Web API client and is **integrations'**), the MCP
**server** (`mcp/server.py`, `runtime.py`, `sampling.py`, `__init__.py`), the `claude-plugin/`
scaffold, `.github/workflows/`, `Makefile`, `pyproject.toml`, `packaging/`,
`scripts/pr_feedback.py` and `tests/unit/test_surface_parity.py`

**Skills** — `.claude/skills/project-map/SKILL.md`, `.claude/skills/ci-and-release/SKILL.md`

**Cadence** — Fri 07:00 UTC, weekly

Each `mcp/tools_*.py` and each `claude-plugin/yeaboi/skills/*/` belongs to the mode it serves, not to
you. You own the server they plug into and the registry that proves they exist.

## Standing concerns

- **Surface parity is the charter.** yeaboi ships on five surfaces — TUI, CLI, engines, MCP
  server, plugin skills. A capability on fewer than all five needs a recorded `Exempt("reason")`,
  not silence. You are the workstream that notices when another workstream
  shipped TUI-only.
- **Param parity** — engine signatures vs. MCP tool schemas. A new engine param must reach the tool
  or land in `HIDDEN_PARAMS` with a reason. `db_path`/`today`/`on_progress`/`dry_run` are injection
  seams and always hidden.
- **All paths come from `paths.py`.** A hardcoded `Path.home() / ".yeaboi"` anywhere is a finding.
  This is the `paths-through-paths-py` layering invariant — your sweep runs it as a lens
  (`cowork/hygiene-lenses.md`), so it is a mechanical check rather than something to grep for by
  hand. One deliberate crossing exists, in `config.py`, waived on the line with its reason;
  a second one anywhere reports. **You declare it and everyone runs it.** The invariant is
  `applies_to: "*"`, so every sweep scans it over that charter's own files — which means a
  hardcoded home directory in `ui/` is **tui-ux's** find and never yours. Your own run going
  quiet while another charter files one is the invariant working. **`paths.py` itself is excluded from `dead-code`** — an export
  there with no caller is a mode that has not adopted the convention, and the fix is at the
  caller, never a deletion here.
- **The `pr-feedback` gate is load-bearing and quiet when broken.** `scripts/pr_feedback.py` plus
  `.github/workflows/pr-feedback.yml` are what stop a PR merging past unanswered review findings —
  DoD item 10. Two failures there are invisible: the status context dropping out of the `main-branch`
  ruleset (the check still runs, still goes red, and blocks nothing), and a producer changing its
  comment format so the marker stops parsing (which reads as "the review never ran" — loud — but a
  *reversed* mistake, a marker that always parses as zero, would be silent). Check the ruleset
  actually requires the context.
- **CI health** — this repo has 12 workflows (`backlog-groomer.yml` and `security-scan.yml` were
  retired into cowork: the digest ranks and ages out the queue, and `security-sweep` runs
  `make security` twice weekly at `deep`). Watch for: workflows that silently stopped firing
  (`auto-version.yml` needs `AUTO_VERSION_PAT` or Claude Review stops receiving `workflow_run`
  events), disabled jobs that were meant to be temporary (`smoke.yml` is `if: false`), and actions
  pinned to versions that have moved.
- **Packaging** — the wheel packages only `src/yeaboi`; hatchling loads `.gitignore` as build
  excludes, so an unanchored glob can silently drop files from the wheel. `make build` and inspect.
- **Telemetry stays opt-in** and its transparency page stays accurate.

## Auto lane, in practice

A stale action version, a broken CI job, dead CLI flags, doc drift in `--help`, packaging fixes.
New flags, new MCP tools, workflow *behaviour* changes, and anything touching release always propose.

## Out of scope

Individual `mcp/tools_*.py` files and individual plugin skills — each belongs to its mode.
`cowork/` itself — that belongs to **fleet** now, and only in one direction: it may *record* what already
went wrong (a `calibration.md` row, a lens exclusion) unattended, and everything that changes judgement or
increases output still proposes to a human. The constitution — `house-rules.md`,
`definition-of-done.md`, `sweep-procedure.md`, `models.md`, `crew.md` and the crew agents — is outside
*every* charter, including that one. Propose changes to `cowork/` like anything else.

**integrations** may append a provider's credential getters to `config.py` from a campaign run
(`house-rules.md`, **Extends**) — that site and that operation only; everything else in the file is
yours.

The Go seam — `go/`, `contracts/`, `src/yeaboi/gocore/`, `tests/parity/`,
`tests/unit/test_migration_freeze.py` — belongs to **go-migration** now. **go-migration** may
edit the lockstep sites named in its charter's `**Extends**` paragraph — the version line in
`packaging/yeaboi-core/pyproject.toml`, the root `core` extra pin in `pyproject.toml`, appending
dual-maintenance table rows in `CLAUDE.md`, the `go-*`/`parity` `Makefile` targets, ci.yml's parity
scope, and (at W19 only) adding `release-binaries.yml` — that operation at those sites only;
everything else in those files is yours.
