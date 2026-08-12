# tui-ux sweep

**Trigger** — cron `0 7 * * 3` (Wed 07:00 UTC)
**Summary** — render-test coverage and shared-primitive reuse across the TUI screens
**Workstream** — [`workstreams/tui-ux.md`](../../workstreams/tui-ux.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = tui-ux`.

Read `.claude/skills/tui-standards/SKILL.md` first — it is mandatory for this workstream.

## Focus

- **Render-test coverage** — enumerate every `_build_*_screen` and every render test. A screen
  without one is an auto-lane gap; hand it to `test-writer`.
- **Primitive reuse** — grep `ui/` for locally re-implemented buttons, scrollbars, and viewport
  maths that `ui/shared/_components.py` already provides. Each is a proposal (they are refactors,
  and refactors in `ui/` touch a lot of lines).
- **Tip hygiene** — every capability has a `FeatureTip`; every `is_new=True` flag set more than two
  releases ago should be cleared.
- **Panel structure** — confirm the AST guard still covers every page and that no screen paints
  outside `build_page_panel`.

## Extra stop conditions

- **`ui/mode_select/__init__.py` (14k LOC): at most one change per run, and never alongside another
  `ui/` edit.** Anything structural there proposes.
