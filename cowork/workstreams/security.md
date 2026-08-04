# security

**Owns** — `src/yeaboi/input_guardrails.py`, `output_guardrails.py`, `fs_policy.py`, `redaction.py`,
`web/security.py`, `sharing/access.py`, `sharing/gate.py`, `tests/unit/guardrails/`,
`tests/unit/test_{fs_policy,redaction,web_security,export_xss,consent}.py`,
`.github/workflows/codeql.yml`, `SECURITY.md`

**Cadence** — Mon + Thu 06:00 UTC (the only workstream that runs twice a week)

## Standing concerns

- **The CSP invariant.** `ARTIFACT_CSP` is `connect-src 'none'`; `EDIT_CSP` is identical but for
  `connect-src 'self'`. A test diffs the two policies whole — if that test is ever loosened rather
  than updated, that is a finding, not a refactor.
- **`web/security.py` is the only place a served document's headers come from.** Any request handler
  writing its own headers is a finding.
- **Guardrail coverage vs. adversarial input** — `tests/unit/guardrails/test_guardrails_adversarial.py`
  and `nodes/test_parser_adversarial.py` should grow with every new input surface.
- **`make security`** — ruff SAST (flake8-bandit `S`) plus `pip-audit`. A new CVE is auto-lane.
- **Secret leakage** — `redaction.py` must cover every new log call and every new export field.
  gitleaks runs in CI; a redaction gap it cannot see is the one worth hunting.
- **Filesystem sandbox** — `fs_policy.py` + `YEABOI_ALLOWED_PATHS`. Any new file write must go
  through it.

## Auto lane, in practice

CVE bumps from `pip-audit`, a missing redaction pattern with a test, a guardrail gap where the fix is
one predicate. Anything that changes *what a user is allowed to do* proposes.

## Out of scope

The rest of `sharing/` (tunnels, live docs) belongs to **artifacts-sharing**. Web CSP *policy* is
yours; the markup and bundles it protects belong to **web-ux**.
