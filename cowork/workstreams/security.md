# security

**Owns** — `src/yeaboi/input_guardrails.py`, `output_guardrails.py`, `fs_policy.py`, `redaction.py`,
`claude_auth.py`, `auth_state.py`,
`web/security.py`, `sharing/access.py`, `sharing/gate.py`, `tests/unit/guardrails/`,
`tests/unit/test_{fs_policy,redaction,web_security,export_xss,consent,claude_auth,auth_state}.py`,
`.github/workflows/codeql.yml`, `.github/workflows/codeql-triage.yml`, `.github/codeql/`,
`SECURITY.md`

**Cadence** — Mon + Thu 06:00 UTC (the only workstream that runs twice a week)

## Standing concerns

- **The CSP invariant.** `ARTIFACT_CSP` is `connect-src 'none'`; `EDIT_CSP` is identical but for
  `connect-src 'self'`. A test diffs the two policies whole — if that test is ever loosened rather
  than updated, that is a finding, not a refactor.
- **`web/security.py` is the only place a served document's headers come from.** Any request handler
  writing its own headers is a finding.
- **Guardrail coverage vs. adversarial input** — `tests/unit/guardrails/test_guardrails_adversarial.py`
  and `nodes/test_parser_adversarial.py` should grow with every new input surface.
- **`make security`** — ruff SAST (flake8-bandit `S`) plus `pip-audit`. A new CVE is auto-lane, and
  is the one dependency bump that is — but check for an open Dependabot PR on that dependency first
  and drive that one instead of opening a second. See [house-rules.md](../house-rules.md).
- **Secret leakage** — `redaction.py` must cover every new log call and every new export field.
  gitleaks runs in CI; a redaction gap it cannot see is the one worth hunting. Its sibling
  `redaction.log_safe()` covers the other half — a tainted value must not be able to *end* a log
  line and forge the next one. Wrap the argument, never the format string.
- **CodeQL alerts are triaged by a workflow, not by this sweep.** `codeql-triage.yml` reads the open
  alerts weekly, batch-fixes the rules on `.github/codeql/triage-policy.yml`, and proposes the rest.
  A scout cannot do this: an alert is state in the GitHub API, not a fact about a file. Your job on
  Monday is one line — check the open triage PR is moving — and to keep the policy file honest, not
  to hunt alerts by hand.
- **Filesystem sandbox** — `fs_policy.py` + `YEABOI_ALLOWED_PATHS`. Any new file write must go
  through it.

## Auto lane, in practice

CVE bumps from `pip-audit`, a missing redaction pattern with a test, a guardrail gap where the fix is
one predicate. Anything that changes *what a user is allowed to do* proposes.

## Out of scope

The rest of `sharing/` (tunnels, live docs) belongs to **artifacts-sharing**. Web CSP *policy* is
yours; the markup and bundles it protects belong to **web-ux**.
