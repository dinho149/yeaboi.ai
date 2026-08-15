# go-migration

**Owns** — the Go rewrite program and its whole seam: `go/` (the `yeaboi-core` binary, the
future `cmd/yeaboi`, and every `internal/` twin of a Python engine), `contracts/` (the RPC
contract and the CLI golden contract), `src/yeaboi/gocore/`, `tests/parity/` (the byte-parity
gate between the twins), `tests/unit/test_migration_freeze.py`, `cowork/migration/` (the
program of record), `scripts/migration_progress.py` and `tests/unit/test_migration_progress.py`

**Skills** — `.claude/skills/project-map/SKILL.md`, `.claude/skills/ci-and-release/SKILL.md`

**Cadence** — weekdays 07:40 UTC ([`cron/go-migration-campaign.md`](../routines/cron/go-migration-campaign.md));
progress posted Tuesdays 08:30 and on every wave merge.

The seventeenth workstream, and the second one that builds rather than maintains. Its subject
is one program: rewrite the ~153K-line Python codebase in Go, thirteen wave-PRs, each gated by
byte parity — [`cowork/migration/program.md`](../migration/program.md) is the program of record
and the standing approval. The lane it runs in is
[`house-rules.md`](../house-rules.md), **The migration lane**.

**Extends** — the lockstep sites in platform's files. A wave PR must move the version lockstep
and the dual-maintenance record in the same commit that moves the code, and those files are
platform's. The grant is by site and by operation, exactly as the campaign lane's:
the version line in `packaging/yeaboi-core/pyproject.toml` — **platform**;
the `core = ["yeaboi-core>=X.Y,<X.Y+1"]` extra pin in `pyproject.toml` — **platform**;
appending one dual-maintenance bullet per wave (and flipping an area's reference-direction
wording per the program doc's §5) in `CLAUDE.md` — **platform**;
the `go-*`/`parity` targets in `Makefile` — **platform**;
the parity job's env vars and scope globs in `.github/workflows/ci.yml`, never its
required-context structure — **platform**;
and at W19 only, adding `.github/workflows/release-binaries.yml`, named in the program doc's
§5 — **platform**. Anything else in those files is platform's, and changing existing behaviour
at these sites is a proposal for platform.

## Standing concerns

- **The gate is the review.** A wave is reviewable because the byte-diff against Python is
  empty, not because someone read 8K lines of Go. A wave whose gate is skipped, weakened, or
  green-by-vacancy is the one finding that outranks all progress; `scripts/pr_feedback.py`
  holds `workstream:go-migration` PRs red until `Go core` and `Python ↔ Go parity` ran and
  passed on the head commit.
- **The program doc is the queue.** Progress is recomputed from its §3 checkbox table, merged
  PRs by label, and parity counts — never from memory between runs
  (`scripts/migration_progress.py` is the only renderer).
- **Freeze discipline.** Once the next wave lands, the previous area's Python files enter
  `FROZEN_SURFACES` in `tests/unit/test_migration_freeze.py`; a frozen file changing without a
  Go mirror is a finding, and the CLAUDE.md wording for that area flips to Go-as-reference.
- **No behaviour change observable from the Python product before W19**, except speed. A
  user-visible drift a parity corpus missed is a bug, not a deviation to document.

## Out of scope

Everything under `src/yeaboi/` except `src/yeaboi/gocore/` — the Python twins belong to their
mode's charter until their wave lands, and even then bugfixes land Go-first and mirror out.
`packaging/` and `.github/workflows/` (**platform**, except the `**Extends**` sites above).
`cowork/` outside `cowork/migration/` (**fleet**). The program doc's 13-wave table, decisions,
and conventions — status-checkbox flips and per-wave spec sections are this workstream's to
append; everything else in that file is a human's.
