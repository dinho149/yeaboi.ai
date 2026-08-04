# security sweep

**Trigger** — cron `0 6 * * 1,4` (Mon + Thu 06:00 UTC)
**Workstream** — [`workstreams/security.md`](../../workstreams/security.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = security`.

## Focus

Alternate between the two weekly runs:

- **Monday — dependency and SAST.** Run `make security`. Every `pip-audit` CVE and every new ruff
  `S`-rule hit is a candidate; CVE bumps are auto lane.
- **Thursday — surface review.** Pick one input or output surface added or changed in the last two
  weeks (`git log --since='2 weeks' --name-only -- src/yeaboi/`) and check it against the guardrail
  layers: input validation, redaction on the way to logs, CSP on the way to a browser, `fs_policy`
  on the way to disk.

Both runs: verify the `ARTIFACT_CSP` / `EDIT_CSP` diff test still asserts the *whole* policy.

## Extra stop conditions

- A finding that requires disclosure (an exploitable path in a shipped release) is **not** filed as a
  public GitHub issue. File the Linear ticket, post to `#yeaboi-claude`, and stop.
