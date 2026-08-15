# tui-ux sweep

**Trigger** — cron `0 7 * * 3` (Wed 07:00 UTC)
**Summary** — render-test coverage and shared-primitive reuse across the TUI screens
**Workstream** — [`workstreams/tui-ux.md`](../../workstreams/tui-ux.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = tui-ux`.

Read `.claude/skills/tui-standards/SKILL.md` first — it is mandatory for this workstream.

## Lenses

Run these before the scout and hand it the output — see
[hygiene-lenses.md](../../hygiene-lenses.md).

- `dead-code` — a screen helper nothing calls. `ui/` grows by accretion and a `_build_*` left
  behind by a redesign looks exactly like one still wired up.
- `assertion-free-tests` — a render test that renders and asserts nothing is the failure this
  charter's "every `_build_*_screen` needs a render test" rule was meant to prevent, wearing the
  rule's own uniform.
- `layering` — `tui-does-not-import-web`, plus every invariant that applies everywhere. The
  `Path.home() / ".yeaboi"` in `ui/mode_select/__init__.py` is one of these, and it is a one-line
  import swap rather than a structural change, so it is the shape the lane is written for.
- `stale-flags` — every `is_new=True` in `_tips.py` that has shipped in two or more releases. This
  charter has said "more than two releases ago" in prose since it was written; the lens is that
  sentence with a command behind it. All seven are stale today, so the first run's `held` count is
  the file telling you nobody has ever cleared one.
- `crash-fuzz` — seeded keystrokes against the live TUI. The screens are yours, so most of what it
  finds is too; a crash whose deepest frame is somebody else's file reports as `outside-owns` and
  stays theirs.

**A crash lands in the auto lane on its seed and nothing else** — the key sequence is the regression
test. A **hang** proposes: there is no mechanical reproduction of "it stopped repainting". A hang
carries no traceback, so the fuzzer aborts the wedged process under `PYTHONFAULTHANDLER` to get one;
the frames you are handed come from that dump.

**There is one open already.** `--seed 2 --steps 150` wedges the TUI after 28 keystrokes inside
`_sweep_menu_in`, screen still repainting, stdin no longer read. Reproducible, yours, root cause
not established. Confirm it before proposing anything, and do not file a second copy of it.

**`duplication` is deliberately not on this list.** It finds a great deal here and almost all of it
is `ui/mode_select/__init__.py` duplicating itself, which is the 14k-LOC problem this charter
already has a standing proposal for. Re-filing it every week under a different name would spend both
proposal slots on an answered question. Run it by hand — `make cowork-lens LENS=duplication WS=tui-ux`
— when the split is being argued; do not file from it.

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
