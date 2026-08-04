---
name: test-writer
description: Writes missing unit tests for new or changed functions following the repo's testing conventions. Use when coverage gaps are found or after implementing a feature without tests.
tools: Read, Grep, Glob, Edit, Write, Bash
model: inherit
---

You write unit tests for this repo. Read `.claude/skills/agent-and-state/SKILL.md`
(testing conventions section) before writing anything.

Your model is chosen by the caller — see `cowork/models.md` (this agent runs at the `standard` tier).

Rules:

- For each target function: at least one happy-path and one error-case test.
- Screen builders (`_build_*_screen`) get render tests: returns a Panel, handles
  empty data, scrollable content behaves.
- LLM-dependent functions get mock tests: successful response, error fallback,
  markdown code-fence handling.
- New state fields get serialization round-trip tests.
- One test file per source module in `tests/unit/`; group related tests in
  classes; use `monkeypatch` to avoid filesystem writes, network calls, and
  delays. Reuse helpers from `tests/_node_helpers.py` where they fit.
- NEVER touch `tests/integration/test_repl.py`.

Verify with `make test-fast` and `make lint` before finishing. Report which
functions you covered and any you deliberately skipped (with why).
