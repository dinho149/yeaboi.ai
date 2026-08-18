# yeaboi CLI golden contract (W8 foundations)

The second contract between the Python product and the Go rewrite. Unlike
`contracts/v1/` it carries no RPC: nothing in production dispatches to
`go/cmd/yeaboi` before W19. The contract is a corpus of committed goldens
that both implementations must reproduce byte-for-byte, and it is enforced
exactly the way the RPC parity suites are — in CI's `Python ↔ Go parity`
job, unskipped.

## What is pinned, and where

| Surface | Golden | Python side | Go side |
|---|---|---|---|
| `paths.py` + `config.py` + `logging_setup.py`/`redaction.py` resolution | `tests/parity/goldens/foundations/*.json` — one canonical JSON dump per env fixture | `tests/parity/foundations/dump.py`, run in a subprocess per fixture (config/paths resolve at import time) under a fake `$HOME` | `go/internal/foundations`' golden tests replay the same files; the binary serves `yeaboi __dump-foundations` |
| `cli.py build_parser()` argv behaviour (abbreviation, mutual exclusion, `nargs` consts, bad choices) | `tests/parity/goldens/cli/args.json` | `tests/parity/foundations/argdump.py` over `argvectors.VECTORS` | `go/cmd/yeaboi/args_golden_test.go`; the binary serves `yeaboi __dump-args` |
| Every `--help` screen + `--version` | `tests/parity/goldens/cli/help/*.txt`, captured at `COLUMNS=80` (`version.txt` is a `{version}` template) | `tests/parity/foundations/helpdump.py` | `go/internal/argview` hand-renders argparse's bytes; golden test in `go/cmd/yeaboi` |
| Bundled changelog parsing | `tests/parity/goldens/changelog/` | `tests/parity/foundations/changelogdump.py` | `go/internal/changelog` (`go:embed changelog_data.json`) |

The fixture matrix lives in `tests/parity/foundations/matrix.py`; the traps it
must keep exercising (tilde expansion, unicode whitespace, both truthy
conventions, clamp-after-parse, python-dotenv quoting, …) are asserted by
corpus self-guards in `tests/parity/foundations/test_foundations_parity.py`,
which run in the ordinary pytest suite with or without any binary.

## How it runs

- **`make parity`** builds both binaries (`go-build`, `go-build-cli`) and runs
  `tests/parity` with `YEABOI_CORE_BIN` and `YEABOI_CLI_BIN` exported. Without
  `YEABOI_CLI_BIN` the binary-replay arms skip — `make test` stays pytest-only.
- **CI** (`ci.yml`): the `Go core` job builds and uploads both binaries; the
  `Python ↔ Go parity` job downloads them and runs the suite unskipped. The
  `go` job also asserts the `yeaboi-core` wheel ships **no** `yeaboi` CLI
  binary (`scripts/check_core_wheel.py`) — `cmd/yeaboi` is hidden and
  unshipped until W19.
- **Python-side freeze**: every fixture's live dump must equal its committed
  golden, so editing `paths.py`/`config.py`/`cli.py` regenerates goldens
  deliberately (`uv run python -m tests.parity.foundations.regen`) — after
  mirroring the change into the Go twin, or `make go-test` fails on the same
  files.

## Documented deviations

Same rule as `contracts/v1/rpc.md`: a deviation is recorded where it lives,
and is legitimate only if it is invisible under the pinned goldens.

- `go/internal/argview`: terminal width comes from `COLUMNS` with an 80
  fallback; Python additionally queries the terminal via ioctl when `COLUMNS`
  is unset. Invisible because the goldens are captured at `COLUMNS=80`.
- `go/internal/logfile/redact.go`: Python's single alternation regex cannot be
  expressed in RE2, so Go scans by hand, reproducing `re.sub`'s
  leftmost-scan, first-alternative-wins semantics; redaction.py's
  env-snapshot cache has no twin.
- Changelog: a non-iterable `highlights` value (an int, say) crashes Python's
  `load_changelog` (the comprehension sits outside its try block) where
  `go/internal/changelog.Parse` yields zero highlights. Freezing a crash into
  a golden pins nothing useful, so the corpus sticks to the iterable malformed
  shapes both sides survive (`tests/parity/foundations/changelogdump.py`).
- Every real command in `go/cmd/yeaboi` exits 1 with a "not yet implemented"
  message — dispatch arrives with W10/W17/W18; only the parse tree, help, and
  the hidden `__dump-*` commands are contractual today.
