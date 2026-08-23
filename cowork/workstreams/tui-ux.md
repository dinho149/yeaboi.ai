# tui-ux

**Owns** — `src/yeaboi/ui/` **except `ui/session/`** (which is **planning**'s), `src/yeaboi/repl/`,
`src/yeaboi/ui/shared/_tips.py`, `src/yeaboi/usage_export.py`, `formatters.py` (the REPL-side
formatter layer), and the terminal-affordance helpers the TUI reaches out through — `clipboard.py`,
`voice.py`, `voice_install.py`, `music.py`, `os_open.py` — plus the matching render tests. Includes the two TUI utility
mode cards, **`usage` and `settings`**. Also `src/yeaboi/provider_verification.py` — the setup
wizard's live credential pings, promoted out of `ui/provider_select/` so the pre-mode LLM gate
(`ui/shared/_llm_gate.py`) can call it without a UI-layer dependency.

**Skills** — `.claude/skills/tui-standards/SKILL.md` (mandatory — read it before any edit)

**Cadence** — Wed 07:00 UTC, weekly

## Standing concerns

- **`ui/mode_select/__init__.py` is 14,265 lines** and the worst merge surface in the repo. Touch it
  in the smallest possible increment, and never in the same run as another `ui/` change. Proposals to
  split it are welcome; doing it unattended is not.
- **The mandatory Panel page structure.** Every screen is `build_page_panel` with the mode's
  background tint. An AST guard enforces it — a screen that bypasses it is a finding.
- **Shared primitives** live in `ui/shared/_components.py`. A screen re-implementing a button,
  scrollbar, or viewport calculation locally is duplication to file.
- **`no_wrap` needs Panel context** — a bare `console.print(text)` ignores `no_wrap`/`overflow`, so
  row-height tests must render through `Panel(Group(...))`. A test that does not is measuring nothing.
- **Every `_build_*_screen` needs a render test.** A screen without one is an auto-lane finding.
- **Feature tips** — every capability needs a `FeatureTip` in `_tips.py` keyed by capability name,
  with a `mode_key` when it owns a `_MODE_CARDS` card. `TestTips` enforces it both ways.
- **Never log per frame.** See `.claude/skills/logging/SKILL.md`.
- **`ui/` never imports `web/`.** Out of scope below says browser surfaces belong to **web-ux**;
  this is that line made mechanical, as the `tui-does-not-import-web` layering invariant your
  sweep runs as a lens (`cowork/hygiene-lenses.md`). An import across it is how a terminal
  screen starts depending on a committed bundle, which `pip install yeaboi` with no Node has
  to keep working without.

## The two utility pages

Both are mode cards, and both are deliberately engine-less, CLI-less and skill-less — those
exemptions are recorded in `CAPABILITIES`. Proposals to give them engines are already-answered
questions.

- **`usage`** reads the local `token_usage` table (schema v5, perf columns v12), written from
  `agent/llm.py:160`. The screen is `_build_usage_screen` in
  `ui/mode_select/screens/_screens_secondary.py:2345`, fed by `_collect_usage_data`; the copy/export
  path is `build_usage_text` in `usage_export.py`. Cost estimates are the thing to watch: a stale
  per-model price makes the whole page confidently wrong, and nobody cross-checks it.
  **`telemetry.py` is not this feature** — that is separate, opt-in, and not in `CAPABILITIES`.
- **`settings`** writes `~/.yeaboi/.env` through `config`. Every secret must be masked on render, and
  that masking must have a test — an unmasked key on a screen someone screenshots is the failure
  mode. Paths come from `paths.py`, never hardcoded.

## Auto lane, in practice

A missing render test, a dead screen, an `is_new=True` tip that should have been cleared a release
ago, doc drift. Layout, copy, colour, key bindings, and navigation always propose — they are what the
user sees.

## Out of scope

Browser surfaces of any kind (**web-ux**). `ui/session/` — the planning intake, review and editor
screens belong to **planning**. Every mode's engine logic belongs to that mode's own workstream.

**integrations** may append one wizard step and one `_verify_*` probe under `ui/provider_select/`,
and one Credentials section in `ui/mode_select/screens/_screens_secondary.py`, from a campaign run
(`house-rules.md`, **Extends**) — those two sites and that operation only. `ui/mode_select/__init__.py`
is deliberately outside the grant; nothing may append there.
