# planning sweep

**Trigger** — cron `0 7 * * 1` (Mon 07:00 UTC)
**Workstream** — [`workstreams/planning.md`](../../workstreams/planning.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = planning`.

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
