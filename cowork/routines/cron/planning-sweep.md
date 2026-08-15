# planning sweep

**Trigger** — cron `0 7 * * 1` (Mon 07:00 UTC)
**Summary** — ScrumState fields, prompt token budgets, and eval coverage
**Workstream** — [`workstreams/planning.md`](../../workstreams/planning.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = planning`.

## Lenses

Run these before the scout and hand it the output — see
[hygiene-lenses.md](../../hygiene-lenses.md).

- `dead-code` — the prompt factories and node helpers a chat refactor left behind. `prompts/` is
  additive by habit and nothing here is imported by name from a screen.
- `assertion-free-tests` — `tests/unit/nodes/` and `tests/golden/` are this charter's, and a golden
  test that renders without comparing is the shape this lens exists for.
- `layering` — every invariant that applies everywhere; `agent/` is where a hardcoded path is most
  likely to be written by hand.
- `crash-fuzz` — `ui/session/` is this charter's, and the planning composer is where the paste and
  control-key handling lives. A find outside `Owns` belongs to whoever owns the file it names; report
  it and move on.

**A crash lands in the auto lane on its seed and nothing else.** A **hang** proposes — there is no
mechanical regression test for "it stopped repainting", so somebody has to read it.

## Focus

- **State audit** — every field on `ScrumState` must have a frozen-dataclass default and a
  serialization round-trip test. Enumerate the fields, enumerate the tests, report the difference.
- **Prompt budget** — run `make budget-report` and compare against the numbers in the most recent
  budget-report issue. Growth over ~20% in one prompt is a finding.
- **Eval coverage** — `git log --since='4 weeks' -- src/yeaboi/prompts/`. Any prompt changed
  materially with no matching change under `tests/golden/` is a finding.
- **Fallback coverage** — for each node in `agent/nodes.py`, confirm a test exercises the parse
  failure path. A node whose only test is the happy path is an auto-lane test gap.
- **Intake paths** — rotate one of Small / Large / Roadmap / Offline per sweep and walk it end to
  end. Offline round-trips through a markdown questionnaire and breaks quietly.
- **Provider parity** — grep for response-shape assumptions that only hold for Anthropic.

## Extra stop conditions

- **Never modify `tests/integration/test_repl.py`.** If a change would require it, propose instead.
