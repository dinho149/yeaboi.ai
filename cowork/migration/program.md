# Go Migration — Program of Record

**End state:** yeaboi is a single Go binary, downloadable without PyPI or uv, with the terminal
UI/UX unchanged. **Shape:** 13 waves, one PR per wave (house style: one PR, phase commits —
precedent PRs #102/#107), each gated by a parity/golden harness.

> **Who edits this file.** Status-checkbox flips in §3 and per-wave `## PR N — Wave X` spec
> sections (§5, *Wave specs*) are appended by the go-migration campaign
> (`cowork/house-rules.md`, **The migration lane**); every other edit to this file is a human's.
> A checkbox flip writes `✔` — exactly that glyph: `scripts/migration_progress.py` parses the
> box as `[☐✔xX]`, so a `☑`, `✅` or `[x]` makes the row vanish from the progress bar with
> nothing failing anywhere.

---

## 1. Context

The migration began as a sidecar pilot: `go/` mirrors the standup deterministic core, the
agentwatch family, and (waves 5–6) the analysis scoring, behind an ndjson JSON-RPC seam
(`contracts/v1/`, `src/yeaboi/gocore/`) with byte-level parity tests (`tests/parity/`) and an
always-complete Python fallback. That covers ~5% of the ~153K-line Python codebase.

A full rewrite cannot fit in 1–2 PRs (the TUI alone is 54.6K lines on Rich's renderer), so the
program runs as 13 wave-PRs. What makes big PRs reviewable here: almost every wave is
parity-gated — the review isn't "read 8K lines of Go," it's "the byte-diff against Python is
empty."

## 2. Decisions (confirmed)

- **Scope**: full program — all 13 waves through the Go TUI and cutover.
- **PR granularity**: one PR per wave; W18 may split into two if unreviewable.
- **Distribution**: curl `install.sh` (served from yeaboi.ai) + per-OS/arch binaries on GitHub
  Releases — shipped **only at W19 (cutover)** as the finished Go binary. No embedded-runtime
  interim bundle.
- **PyPI**: keeps publishing during the transition; sunset decided after cutover proves out.
  At W19 the finished Go binary **also ships on PyPI** — a full-product per-platform wheel built
  by the same custom hatch-hook pattern as `packaging/yeaboi-core` (binary inside, console-script
  shim), published through `publish.yml`'s existing flow, so `pip install yeaboi` keeps working
  after cutover.
- Terminal UI/UX must not change (frame-parity gates in W17/W18).
- **`yeaboi-core` publishes final wheels to PyPI per wave.** This is already live behavior:
  `publish-core.yml` is version-triggered (push to main + version-without-tag check) and
  `core-v0.3.0` published on 2026-08-12. A wave that bumps `binaryVersion` + the packaging
  version ships `core-vX.Y.0` on merge; a wave that adds no RPC methods ships nothing.

## 3. The 13 PRs

Waves 1–6 predate this program: the sidecar pilot (PRs #215, #217, #221) merged waves 1–5, and
Wave 6 (`analysis.score_docs`, PR #224) merged on 2026-08-17. PR 1 below begins from there.

**All thirteen land on one branch.** Every wave PR is based on `chore/go-migration` and merges
into it once its gate is green; wave N+1 branches off that, so nothing waits. `main` gains none
of this until a human opens and merges the single `chore/go-migration` → `main` PR after W19 —
which is the review this program is ultimately for: one branch, read whole, rather than thirteen
separate merges nobody saw together. The checkboxes below therefore live on the integration
branch for the whole program, which is why `scripts/migration_progress.py` reads them from there
and not from `main`.

| ✔ | PR | Wave | Contents (phase commits inside) | Size | Gate |
|---|---|---|---|---|---|
| ✔ | 1 | W7 | Retro/poker export builders | S | existing parity harness |
| ☐ | 2 | W8 | Foundations: config/paths, 85 env vars, CLI parser skeleton | M | golden-subprocess diff (`--help`, config/paths resolution) |
| ☐ | 3 | W9 | Persistence: sessions v1→v27 ladder + ~30 tables across per-mode stores | L | migrate fixture DBs both sides, diff full projections |
| ☐ | 4 | W10 | Mode engines headless (phase per mode: standup remainder, retro, poker, performance, reporting, roadmap, analysis, artifacts/sharing) | XL | parity per engine |
| ☐ | 5 | W11 | Web layer: 3 stdlib HTTP servers, SSE, tunnel, gate + the four prep refactors from the boards ruling | L | new HTTP replay harness |
| ☐ | 6 | W12 | LLM layer: 5 provider REST clients, streaming, usage tracking, guardrails | L | recorded-cassette contract tests |
| ☐ | 7 | W13 | Agent graph + nodes.py (8.9K) + prompts/ + 37 tool schemas | XL | golden evaluators + prompt-byte diffs |
| ☐ | 8 | W14 | Tracker integrations (phase per tracker: GitHub, AzDO, Jira, Confluence, Notion, team_learning) | XL | recorded-response contract tests |
| ☐ | 9 | W15 | MCP server (mcp-go) + plugin rewiring | M | MCP tool-schema diff |
| ☐ | 10 | W16 | Exporters + `yeaboi-extras` split | L | export byte-diffs |
| ☐ | 11 | W17 | TUI platform: Rich-compatible renderer, termios input, themes, frame-parity harness | XL | Go frames diffed vs committed frame goldens |
| ☐ | 12 | W18 | TUI screens: 72 `_build_*_screen` fns, mode_select hubs (14.9K), chat driver, provider setup, splash/mascot/mayhem/music | XXL | frame parity per screen |
| ☐ | 13 | W19 | Cutover: single `yeaboi` Go binary, install.sh + GitHub Releases, PyPI transition | M | install smoke on 5 platforms |

**Ordering**: W8/W9 unblock everything; W17 → W18; W19 last.
**Parallelism** (Go-package deps): after W8 merges, **W9, W12, W14, W17** can run in four parallel
worktrees (`make wt-headless NAME=wave-N-<area>`); after W9 → W10; after W10 → W11, W16; W13
needs W12+W14; W15 needs W13; W18 needs W17+W10. PRs merge in wave order even when developed in
parallel. Conflict surface ~nil (disjoint `go/internal/` trees; CLAUDE.md list + Makefile
trivially rebased). Rebase on `origin/chore/go-migration` before opening a wave PR — **not**
`/sync-main`, which rebases on `origin/main` and would drag the wave off its base.

## 4. What stays in Python — `yeaboi-extras` (W16)

Exactly the already-optional, lazy-imported leaf features:

1. **Voice/dictation** (`voice.py`, `_voice_input.py` — PortAudio needs cgo, which would end the
   `CGO_ENABLED=0` static cross-compile; the whisper ecosystem is Python-native).
2. **.pptx/.docx export** (`reporting/pptx_export.py` — no credible Go OOXML writer).
3. **PDF ingest** (roadmap intake, pymupdf — Go extraction is markedly worse).
4. **matplotlib charts** (`charts.py`, 127 lines — pixel parity impossible; candidate to re-derive
   in Go later with accepted drift).

Shipped as one optional Python package the Go binary discovers and shells out to (today's sidecar
pattern inverted). Absent ⇒ features degrade exactly as missing extras do today. The dev/test/eval
harness also stays Python (never ships).

## 5. Program conventions (bind all 13 PRs)

### Contract evolution & RPC sunset
- Contract stays **v1 forever**; method addition is already additive-without-bump.
- An RPC method is added **only when Python will dispatch it in production before W19**.
  Future-binary waves (W8, W9, W11, W17, W18) are exercised by their gates, never RPC.
- Expected RPC growth: W7 (export builders), possibly W10 cores; W12/W14 gate-only (a local RPC
  hop gains nothing for network I/O). **The RPC surface freezes no later than W14**; from W13 on,
  new Go code imports earlier waves' packages directly. W19 deletes `src/yeaboi/gocore/`,
  `contracts/v1/`, `cmd/yeaboi-core`; RPC parity suites retire, behavior gates survive as Go tests.

### Wave specs
Each wave's first campaign run appends a `## PR N — Wave X` section to this file — following the
§6/§7 template: verified ground truth, scope in/out, the gate, phase commits, lockstep bumps,
risks — **before any code is written**, committed as phase 0 on the wave branch. Drafting the
spec is unattended (it is planning inside the approved program); changing the §3 table, the §2
decisions, or these conventions is not.

### Gate strategy per wave class
- **Pure cores** (W7, W9 ladder, W10, W13 routing/schemas): byte parity, existing
  `tests/parity/` + `_diff.py`, fixture corpora per wave.
- **W8**: golden-subprocess harness (see §7).
- **W9**: fixture DBs at each of v1→v27; both sides migrate; dump every table to canonical JSON
  and diff; crash-mid-migration fixtures. Schema-ceiling lockstep pattern already exists
  (`test_gocore_packaging.py::TestSchemaGuardLockstep`).
- **W11**: HTTP replay harness (`tests/parity/http_replay/`) — scripted request sequences against
  both servers on ephemeral ports; diff status/body bytes + an allowlisted header subset; SSE as
  ordered event-frame lists.
- **W12/W14**: recorded cassettes (`tests/parity/cassettes/`) — a stub server asserts the request
  bytes each client emits (method, path, ordered params, body); both clients parse identical
  canned responses to identical artifacts; no live network in CI.
- **W17/W18**: frame parity. Note: syrupy today is only `test_formatters.py` (45 asserts, .ambr);
  the screen tests are assertion-based. W17 phase 1 builds a Python frame-capture harness: render
  each `_build_*_screen` via Rich `Console(width, height).export_text()` under fixed fake state
  and commit **raw `.txt` goldens** under `tests/parity/goldens/frames/` (Go must read them too);
  the Go renderer reproduces them byte-for-byte.

### Dual maintenance & the freeze mechanism
- CLAUDE.md's "REQUIRED: Go sidecar dual maintenance" gains **one table row per wave** (family
  ↔ Python files ↔ Go package). Go files keep naming their Python twin in headers.
- **Reference flip + freeze**: Python stays the reference for an area until the *next* wave merges
  cleanly on top. Then the area's files enter `FROZEN_SURFACES` in a new
  `tests/unit/test_migration_freeze.py` (`{path: sha256}`); the test fails when a frozen file
  changes — editing requires updating the hash **and** mirroring to Go first. CLAUDE.md wording
  flips per area: "Go is the reference; the frozen Python twin changes only for user-facing
  bugfixes, mirrored from Go." W19 deletes frozen files; the table empties.

### Versioning & release
- `binaryVersion` bumps minor once per wave PR that adds RPC methods; others leave it alone.
- `cmd/yeaboi` reports the **product** version (pyproject via ldflags), not `binaryVersion`.
- A wave that bumps `binaryVersion` + the `packaging/yeaboi-core` version **publishes nothing on
  its own merge**: waves merge into `chore/go-migration`, and `publish-core.yml` is triggered by
  a version change reaching `main`. The bumps accumulate on the integration branch and the core
  wheel publishes **once**, when the human merges the final PR — so the version a wave writes is
  a promise about the final wheel, not a release. The lockstep tests still force each wave's own
  consistency. W19 adds `release-binaries.yml` (matrix `go build` or goreleaser) attaching per-platform
  `yeaboi` binaries + `install.sh` to GitHub Releases (reuse the version-has-no-tag → publish
  pattern and `softprops/action-gh-release`); the PyPI flow (`publish.yml`) continues unchanged.
- `auto-version.yml` untouched; wave PRs expect **minor** bumps; no major before W19.

### Definition of done per wave PR
All ten items of `cowork/definition-of-done.md` (Linear ticket, tests, lint, security,
observability, Notion, Slack, pr-feedback gate), plus:
1. Wave gate green **unskipped** in CI (parity job env vars extended as needed).
2. CLAUDE.md dual-maintenance table row added; freeze-table entry added for the *previous* wave's area.
3. `contracts/` updated iff the RPC surface changed; `binaryVersion` bumped iff methods added;
   schema-ceiling lockstep honored (W9+).
4. `make go-test`, `make go-lint`, `make parity` in the verification list.
5. Surface-parity/web-bundle DoD items recorded as explicit `Exempt(...)` — silence is not an
   exemption.
6. No behavior change observable from the Python product before W19 (except speed).

### Open items to settle during execution (defaults chosen, not blocking)
- `fs_policy.py` wave assignment → W9/W10 (first Go user-path access).
- `.env` writer parity in W8 → **in** (small, derisks W17/W19 Settings).
- `redaction.py` port sizing → verify during W8; split to its own phase if large.
- Frame golden format → raw `.txt`.
- Freeze mechanism → hash-table test (works locally, no git machinery).
- Windows parity → W19-only (CI is linux, dev is darwin; Python is best-effort there today).

---

## 6. PR 1 — Wave 7: retro/poker export builders

**Base**: fresh worktree off `origin/chore/go-migration`, the program's integration branch, which
every wave from here is based on and merges into — PR #224 (Wave 6) merged on 2026-08-17 and its
work is on `main`, which the integration branch carries forward. `binaryVersion` 0.4.0 → this PR
makes **0.5.0**; the lockstep tests force consistency.

### The pure seam (~775 lines Python → ~700–900 Go)
- `src/yeaboi/retro/export.py` (229 L): `_title`, `_stem`, `_reactions_str`,
  `build_retro_markdown`, `_card_payload`, `retro_export_args`. Impure stays Python: `_slug`,
  the `export_page` call, `export_retro` (fs writes, paths, logging).
- `src/yeaboi/poker/export.py` (216 L): `_title`, `_stem`, `_pts`, `_votes_str`,
  `build_poker_markdown`, `_ticket_payload`, the args/nav/facts/trend assembly inside
  `build_poker_html`.
- `src/yeaboi/artifacts/render.py` (119 L, all pure): `annotations_payload`,
  `annotations_markdown`, `with_annotations`, `edit_map`, `row_anchor`.
- `src/yeaboi/html_theme.py`: `safe_url` (Go side logs nothing on drop — documented deviation),
  `history_series`, `trend`.
- `markdown_convert.md_table_cell`; `artifacts/paths.escape_value` (urllib quote + `.`→`%2E`);
  the stores' `_dict_to_retro_report` / `_dict_to_poker_report` normalisation (promote to public
  `report_from_dict`, keep the old name as alias); `retro/board.py` grid/status constants;
  `RetroReport.by_grid()`.
- **Boundary**: inputs = (report-as-asdict dict, history rows, editable flag, two wall-clock
  strings — the only nondeterminism, hoisted into params); outputs = (markdown string,
  export-args dict). Python keeps rendering the HTML shell around the returned args.
- Callers all route through the two export modules (cli.py:1804,1868; 8 `mode_select` sites;
  `sharing/documents.py:111–114` + `:184–186` editable rebuild; `mcp/tools_retro.py`,
  `tools_poker.py`) — public signatures unchanged, no caller moves.

### Contract (additive, v1)
Two methods: `retro.build_export` (params: report, history, editable, generated_ts,
generated_date) and `poker.build_export` (no editable — poker has no editable share). Results
`{contract_version, markdown, args}`; **key order contractual** (args is json.dumps-ed into the
page boot payload). Schema notes: retro `columns` always exactly `len(RETRO_GRIDS)` = 4 in board
order; unknown-grid cards dropped from columns but counted in CARDS/trend; carried key order with
in-place editable text merge; `trend` null (not omitted) under 2 points; poker `final` forced null
when not estimated; nav gains `ai`/`duels` only when present. `rpc.md`: hello line + two method
sections + **rule 13** — splitlines universal-terminator set, no-arg `split()` unicode whitespace,
float widening vs json.Number echo, urllib-quote exactness, privacy (package imports no `log`;
`safe_url`'s Python-side warning = accepted Python-only deviation).

### Go layout
New `go/internal/exports/`: `retro.go`, `poker.go`, `theme.go`, `render.go`, `run.go`
(`RunRetroBuildExport`/`RunPokerBuildExport` over `*pysem.Obj`), four test files. pysem additions:
`Splitlines` (Python universal terminators), `SplitWS` (no-arg `str.split` — check
`go/internal/standup` for a promotable equivalent first), `QuoteAll` (urllib percent-encoder).
No high-risk regexes this wave (safe_url's two are ASCII-safe under RE2).

### Python dispatch (clone of the `go_score_docs` idiom)
`build_*_export_inputs` (single `datetime.now()` capture, json round-trip freeze) →
`go_build_*_export` (CoreError → warn → None; success logs one line) or `build_*_export`
(reference impl via the store-deserializer round-trip). **Validation**: markdown non-empty str;
`args.report.kind` correct; structural count checks (retro: columns == len(RETRO_GRIDS) and
carried == input carried count; poker: tickets count) — any failure ⇒ malformed ⇒ Python. Public
API rewired through one `_export_doc()`; `export_retro` calls it once for both artifacts. Poker
refactor: split the inline assembly into `poker_export_args` (matches sibling naming).

### Parity — `tests/parity/test_exports_parity.py`
`test_analysis_parity` skeleton; corpus self-guards **unskipped**. Retro corpus: NBSP/U+2028 in
card text, `|` + emphasis passthrough (unnormalised), Turkish İ + emoji authors, AI-origin card,
`grid=""` card, empty grid, zero-count reaction filtered, multi-codepoint emoji, every
CARRIED_STATUSES value + an unknown status, annotation variants, empty participants, empty
sprint_name, editable anchors needing `escape_value`; history >14 rows, duplicate dates, cutoff,
null card_count, 1-row (trend null). Poker corpus: 0.5/2.5/13.0/int-float/None points, skipped
ticket with stale final_points, URL attack set (`javascript:`, `JAVA\tSCRIPT:`, `//host`,
relative, `mailto:`, `\x00`, empty), filtered empty vote, ai_note ±, duel transcript with
`\r\n`/`\x85`/U+2028, empty-tickets report, `|` in summary. Assertions: `approx_equal` +
`key_orders` + **exact markdown string compare**; recommended: exact json.dumps byte-compare of
args. Phase-A unit-test hardening in `test_retro_export.py` / `test_poker_export.py` (pinned
timestamps enable full-document goldens for the first time).

### Lockstep bumps
`binaryVersion` 0.5.0 + methods list + dispatch arms; `packaging/yeaboi-core` 0.5.0; root
`core = ["yeaboi-core>=0.5.0,<0.6"]`; add `TestExportsPrivacyInvariant` (no-log-import) to
`test_gocore_packaging.py` (the method-set lockstep updates itself from main.go/rpc.md/schemas);
CLAUDE.md dual-maintenance gains a fourth table row — load-bearing, because `html_theme.py` and
`render.py` are shared with five other exporters and changes there now need the Go twin.

### Phase commits
1. `move the retro and poker export builders behind the build_export seam` — Python only, fully
   green with no sidecar change (dispatch hits -32601/no-binary and falls back).
   `make test && make lint`.
2. `serve retro.build_export and poker.build_export from the go sidecar` — Go package + pysem +
   main.go + contracts + rpc.md + parity + bumps.
   `make go-test → make parity → make test → make lint` (+ `make security`).
3. (reserved) review followups after the independent review.

E2E before shipping: binary on `YEABOI_CORE_BIN`, run TUI/CLI retro + poker exports, confirm both
"served by the sidecar" log lines, diff the written .md/.html vs a `YEABOI_GO=0` run on frozen
inputs. Ship via `/ship`; `/pr-feedback` when green.

### Risks
Astral-plane JSON escaping (emoji reactions pin surrogate-pair behavior); json.Number echo vs
float widening (`3` vs `3.0` drift — hence the exact-bytes compares); editable-share per-edit
sidecar round-trip (milliseconds; cheap retreat = keep `editable=True` Python-only); the sidecar
serving user-visible string bytes is new — flag in the PR body. Open: fold the
standup/reporting/performance/roadmap export builders into the same package later (layout chosen
to make that additive); history rows sent verbatim (confirmed).

---

## 7. PR 2 — Wave 8: foundations

**Verified ground truth**: `cli.py` 2,597 L — 28 top-level flags, 135 `add_argument` calls,
8 top-level subcommands (`report standup standup-review perf retro poker analyze agents`) +
nested `perf`/`agents` = **16 parsers**; **exactly 85 env vars**; `config.py` 1,090 L (~70
getters/setters); `paths.py` 565 L; `logging_setup.py` 169 L. `__version__` from package
metadata; changelog bundled as `changelog_data.json`; `.env` via python-dotenv (`ENV_FILE` pinned
to `~/.yeaboi/.env` even under `YEABOI_HOME` — bootstrap circularity, paths.py:140); startup
latency contract in `tests/unit/test_cli_startup.py`; argparse prefix-abbreviation collision
documented at cli.py:766–768.

### Scope IN
1. `go/internal/home` — paths.py port: `YEABOI_HOME` resolution, all dir constants, `_safe_key`,
   `ENV_FILE` pinning, `get_*_dir/path` helpers (mkdir + perms), `migrate_root_dir` + the
   file-move parts.
2. `go/internal/dotenv` — hand-written python-dotenv-compatible **reader and writer** (`set_key`
   semantics, comment preservation, quoting). Never a third-party dotenv lib.
3. `go/internal/config` — all 85 getters, ported individually (config.py deliberately uses TWO
   truthy conventions — no shared parse-bool), clamps only after successful int parse, CSV dedup,
   fallback chains (Confluence→Jira, AzDO team default, org-URL scheme normalisation), setters via
   `internal/dotenv` + 0600/0700 hardening.
4. `go/cmd/yeaboi` — the future binary, **hidden and unshipped**: full 16-parser tree → typed
   Args, argparse-compatible validation (mutual exclusion, `nargs="?"` consts —
   `--resume`→`"__pick__"` — choices, prefix abbreviation), **hand-rendered help byte-matching
   argparse** via new `internal/argview` (no cobra/flag — nothing reproduces argparse bytes),
   `--version`, hidden `__dump-foundations`/`__dump-args` JSON commands for the gate. Every real
   command exits 1 with "not yet implemented in yeaboi (Go) — use the Python yeaboi".
5. `go/internal/logfile` — logging_setup semantics (2MB×3 rotation, 0600/0700, LOG_LEVEL fallback
   WARNING, handler registry `tui|<mode>|session`) + port `redaction.py` alongside.
6. Version via `-ldflags -X main.version=` from pyproject; changelog loader via
   `go:embed changelog_data.json`.

**Scope OUT**: command implementations/dispatch (W10/W17/18); `fs_policy.py` (W9/W10); the sqlite
merge inside `get_db_path` (W9 — Go returns path + perms only, `// W9:` marker); setup wizard /
splash / update_check / voice behavior (their env getters are in; the features are not).

### The gate — golden-subprocess parity (`tests/parity/foundations/`)
- `matrix.py`: ~25 env fixtures (YEABOI_HOME variants, override precedence, nasty `.env` corpus,
  CSV dupes, out-of-range ints per clamped getter, both truthy conventions, fallback chains,
  invalid LOG_LEVEL, `set_key` scenarios).
- `dump.py`: Python dumper — one subprocess per fixture (config/paths resolve at import time),
  fake `$HOME`, prints one canonical JSON of every getter + path helper.
- `test_foundations_parity.py`: runs the dumper vs `yeaboi __dump-foundations` under identical
  env; diffs with `approx_equal`; skips when `YEABOI_CLI_BIN` absent (existing pattern).
- Help goldens `tests/parity/goldens/cli/*.txt`: ~17 help screens + `--version`, captured with
  `COLUMNS=80`, `LANG=C.UTF-8`, Python 3.11. A Python-side test pins `build_parser()` to the
  goldens (the drift detector when someone edits cli.py); a Go-side test pins the binary to them.
  Plus ~30 argv behavioral vectors (abbreviation, mutual exclusion, bad choices) via `__dump-args`.
- Escape hatch, decided up front: if argparse usage-line wrapping is pathological for one screen,
  normalise **only that screen's `usage:` block** and record it as a documented deviation
  (rpc.md precedent).
- Makefile `go-build-cli` target; fold into `parity:` via `YEABOI_CLI_BIN`; ci.yml `go` job builds
  and uploads both binaries; CI assertion that the core wheel does **not** contain the `yeaboi`
  CLI binary. No RPC methods added; `binaryVersion` untouched; no go.mod changes (stdlib only).

### Traps to encode in fixtures
argparse help width/version pinning; prefix abbreviation incl. the `--export` collision;
`nargs` consts; python-dotenv ≠ godotenv; the two truthy conventions; NOT XDG (`~/.yeaboi` +
`expanduser` only, no `$VAR` expansion); ENV_FILE bootstrap pinning + import-time resolution (Go
resolves once per process); Windows chmod best-effort (Windows parity deferred to W19); Python
`int("  5 ")` whitespace tolerance, rejects `"5.0"`, clamp only after parse.

### Phase commits
1. `internal/home` + gate scaffolding (fixture matrix, dumper, paths goldens).
2. `internal/dotenv` + `internal/config`; extend the dumper to all 85 vars.
3. `cmd/yeaboi` parse tree + typed Args + validation + dump commands; argv behavioral goldens.
4. `internal/argview` hand-rendered help + `--version`/ldflags; the 17 help goldens + the
   Python-side freeze test.
5. `internal/logfile` + redaction port; `go:embed` changelog loader.
6. Wiring: Makefile, ci.yml, CLAUDE.md bullet, `contracts/cli/README.md` describing the golden
   gate (it is a contract, just not an RPC one).

Verification per phase: `make go-test && make go-lint && make parity && make test && make lint`;
final: fresh-clone `make parity` proving the goldens are hermetic (no dependence on the dev
machine's `$HOME`/terminal).

---

## 8. Reference — current state (as surveyed 2026-08-11; publish facts corrected 2026-08-15)

- Merge line: #215 pilot (YEA-73) → #217 follow-ups (YEA-74) → #221 W5 analysis (YEA-76);
  **#224 W6 (`analysis.score_docs`, YEA-78) open/in review**. Linear YEA-9 = the TUI rewrite
  backlog ticket. Issue #216 open: per-method RPC timeouts (fold into an early wave).
- `go/`: go 1.22, single dep `modernc.org/sqlite` (CGO-free). Packages: `contract`, `rpc`,
  `pysem` (CPython semantics — truthiness, str/int/strip/lower incl. U+0130, banker's round,
  json.dumps defaults, repr, ordered JSON, `\b`/`\w` RE2 boundary), `agentwatch` (~3.2K),
  `standup` (~5.3K), `analysis` (3.9K, main only).
- Methods (main, 0.3.0): `core.hello`, `agentwatch.{refresh,usage,standup,security}`,
  `standup.aggregate`, `analysis.{classify_markers,score_code}`; W6 adds `analysis.score_docs`.
- Dispatch: `YEABOI_GO` on/off/auto; discovery `YEABOI_CORE_BIN` → wheel → PATH (cwd rejected);
  `get_client()` latches per process; hello handshake 10s; every call site: warn → None →
  Python; result hydration validated; success logs one line. Schema guard: DB newer than Go's
  `currentSchemaVersion` → error 1001 → Python, never a write.
- Parity: 31 tests on main (36 with W6); corpus self-guards run unskipped; conftest autouse
  fixture sets `YEABOI_GO=0` for unit/integration tests.
- Lockstep guards (`tests/unit/test_gocore_packaging.py`): binaryVersion ↔ packaging version;
  method set ↔ rpc.md hello ↔ `contracts/v1/*.json`; schema ceiling; wheel target matrix.
- Prior rulings (Wave-6 plan): boards ruled out (I/O-owned state, 60 Hz TUI reads, impure
  handlers, no HTTP/threading vocabulary in the parity harness; four prep refactors named —
  now folded into W11); export builders = natural W7; TUI last.
- Python surface: `src/yeaboi/` 301 files / 153.6K lines — `ui/` 54.6K (72 `_build_*_screen`,
  19 Themes, termios input, `_mayhem.py` sprite compositor), `tools/` 15.8K (37 @tool fns;
  Jira/Confluence/Notion/AzDO/GitHub via official Python SDKs), `standup/` 13.8K, `agent/` 13.1K
  (LangGraph shallow: 9 nodes, one real loop). SQLite: `sessions.py` v27 single ladder +
  per-mode store ladders (~30 tables). Web: stdlib `http.server` + committed TS bundles
  (language-agnostic); `scripts/gen_web_types.py` needs a Go twin eventually. MCP: 2.6K lines
  over engines only; the Claude plugin invokes `uvx --from yeaboi[mcp]` (rewire at W15/W19).
- Packaging: `yeaboi` wheel via hatchling, `publish.yml` PyPI OIDC (filename load-bearing);
  `yeaboi-core` wheel via `packaging/yeaboi-core` custom hatch hook — cross-compiles 5 targets
  (linux/amd64+arm64, darwin/amd64+arm64, windows/amd64) from one runner; `publish-core.yml`
  **fired for the first time 2026-08-12**: tag `core-v0.3.0`, GitHub Release cut, wheels
  published to PyPI. Install docs advertise `uv tool install yeaboi` (README ~33–88,
  `docs/index.html` hero/#start); Homebrew deliberately removed; `tests/unit/test_site_seo.py`
  pins site metadata; `docs/` is the GH Pages site (CNAME yeaboi.ai) — serves `install.sh` at W19.
