# platform

**Owns** — `src/yeaboi/cli.py` (2.5k LOC), `config.py`, `paths.py`, `logging_setup.py`,
`telemetry.py`, `feedback.py`, `setup_wizard.py`, `update_check.py`, `changelog.py`, the MCP
**server** (`mcp/server.py`, `runtime.py`, `sampling.py`, `__init__.py`), the `claude-plugin/`
scaffold, `.github/workflows/`, `Makefile`, `pyproject.toml`, `packaging/`,
`scripts/pr_feedback.py`, `tests/unit/test_surface_parity.py`, and the Go sidecar seam —
`go/` (the `yeaboi-core` binary and its `internal/` twins of Python engines), `contracts/v1/`,
`src/yeaboi/gocore/`, `tests/parity/` (the byte-parity gate between the twins)

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
`cowork/` itself — propose changes to it like anything else; it is reviewed by a human by design.
