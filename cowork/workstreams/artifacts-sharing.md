# artifacts-sharing

Cross-cutting post-processing on **every** mode's output — the three capabilities that have no mode
card because they are actions on a result screen, not destinations.

**Owns** — `src/yeaboi/artifacts/` (8 files, 2.0k LOC: store, registry, render, edits),
`src/yeaboi/anonymize/` (541 LOC), `src/yeaboi/sharing/` **except `access.py` and `gate.py`**,
`mcp/tools_artifacts.py`, `mcp/tools_anonymize.py`, `tests/unit/test_{artifacts,sharing}_*.py`

**Cadence** — 11th and 25th of the month, 07:30 UTC

## Standing concerns

- **Exports are inert unless a server is behind them.** `ARTIFACT_CSP` sets `connect-src 'none'`, so
  a written file physically cannot make a request. `EDIT_CSP` is **identical but for
  `connect-src 'self'`**, and a test diffs the two policies whole. One policy for both talkers
  (editable artifacts and correctable standups) because they differ in what they *send*, not in what
  they may *reach*. A third policy, or a loosened diff test, is a finding.
- **Every control must be gated on the payload's capability flag** — `edit`, `correctable`. A control
  shipped ungated renders a button that does nothing in a written export, which looks like a bug to
  the reader and cannot be diagnosed after the fact.
- **`export/actions.ts` and `export/vote.ts` are the only network code in the export bundle.** A
  third one is a finding. Post via `mutate('/api/…', {…})` with a literal path and body, and read via
  `payload.get("…")`, so `test_web_request_keys.py` keeps seeing the route.
- **Edits are versioned and attributed.** Every change to a shared artifact carries who and when. An
  edit path that loses attribution, or that lets a version be overwritten rather than appended, is
  the highest-consequence bug in this charter.
- **The one-way-exporter constraint and the `asdict`/tuple trap** — exporters build payloads of text
  and numbers; a tuple that survives `asdict` into a payload will not round-trip.
- **Anonymization must be irreversible in the artifact it produces.** A mapping that leaks into the
  export, a log, or a filename defeats the entire feature.
- **`output-sharing` is `Exempt` on all five surfaces by design** — local process and access-code
  ownership belongs to the human host in the TUI. Proposals to make sharing headless are
  already-answered questions.

## Auto lane, in practice

Broken tests, dead render paths, doc drift. CSP policy, capability flags, attribution, and
anonymization behaviour always propose.

## Out of scope

`sharing/access.py` and `gate.py` — access control is **security**'s. The `export` and `gate` React
bundles (**web-ux**).
