---
name: provenance
description: "Audit the tamper-evident decision chain behind yeaboi's signals: every practice nudge, blocker flag, confidence adjustment, conflict card, and performance prep/review is recorded with its evidence in a hash-chained local log. Use when the user asks why yeaboi said something, whether a signal's history has been tampered with, for an audit/compliance trail of automated decisions, or to trace the evidence behind a specific decision."
---

# Provenance audit workflows with yeaboi

Every deterministic signal yeaboi surfaces is recorded as a hash-chained
decision with the evidence it rested on. The chain is append-only: retracting a
decision appends a tombstone record, and deleting or editing a stored row is
detectable, never hidable. Everything is deterministic and local — no LLM is
involved anywhere in this pipeline.

1. **Verify and summarise** with `provenance_audit`. The default window is 30
   days (`window_days`, 1–365). Lead with `chain_valid`: `true` means every
   record's checksum, link, and sequence number verified across the WHOLE
   chain, not just the window. If it is `false`, `breaks` lists each failure —
   `checksum_mismatch` means a record was edited in place, `chain_break` means
   one was deleted or renumbered, and `truncated_tail` means the newest
   records were removed (the walk fell short of the head anchor every append
   updates in the same transaction). Say so plainly; that is the finding. One
   honest caveat if asked: the chain is a local file with no external anchor,
   so an adversary who rewrites the rows *and* the anchor together is beyond
   what it can prove — it makes tampering visible, not impossible.

2. **Read what was decided**: `recent` lists the window's decisions newest
   first (capped at 50 — a warning names the overflow), and `records_by_type`
   counts the whole chain by kind (`practice-signal`, `blocker-signal`,
   `confidence`, `adjudication-drop`, `conflict`, `one-on-one-prep`,
   `one-on-one`, `six-month-review`).

3. **Trace a "why"** with `provenance_trace` and an `entity_id` from the audit
   (e.g. `standup:2026-08-16:practice:wip-sprawl:alice`). The trail includes
   the entity's own records — retractions included — plus the latest record
   behind each piece of evidence it used, up to `depth` hops (default 2).

4. **An empty chain is normal** on a fresh install: decisions start recording
   when a standup or performance workflow runs. Relay the warning rather than
   treating it as an error.

5. **Privacy.** Records carry decision ids, rule/model names, evidence keys
   and counts — never transcript content, ticket bodies, or 1:1 notes. If the
   user asks what is IN the chain about a person, the honest answer is:
   which rules fired, when, and on what evidence keys.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `provenance_trace` on an
unknown entity returns `found: false` with a hint to list ids via
`provenance_audit` — offer that instead of guessing ids.
