# artifacts-sharing sweep

**Trigger** — cron `30 7 11,25 * *` (11th and 25th, 07:30 UTC)
**Workstream** — [`workstreams/artifacts-sharing.md`](../../workstreams/artifacts-sharing.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = artifacts-sharing`.

## Focus

- **The CSP pair** — confirm the test that diffs `ARTIFACT_CSP` against `EDIT_CSP` still asserts the
  *whole* policy, and that they still differ only in `connect-src`. A third policy, or a diff test
  narrowed to "the interesting lines", is the finding.
- **Capability gating** — every control in the export bundle gated on `edit` or `correctable`. An
  ungated control renders a dead button in a written export, which the reader reads as a broken
  product and which leaves no log to diagnose.
- **Network surface** — `export/actions.ts` and `export/vote.ts` are the only network code in the
  export bundle. Grep for a third.
- **Attribution and versioning** — walk the edit-apply path and confirm no branch overwrites a
  version rather than appending, and none loses the who/when.
- **Anonymization irreversibility** — confirm no mapping reaches an export, a log, or a filename.

## Extra stop conditions

- `output-sharing` is `Exempt` on all five surfaces by design. Do not file proposals to make sharing
  headless, CLI-driven, or MCP-hosted — that question is already answered.
