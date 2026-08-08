# roadmap sweep

**Trigger** — cron `30 7 12 * *` (12th of the month, 07:30 UTC)
**Summary** — roadmap intake and its four recorded surface-parity gaps
**Workstream** — [`workstreams/roadmap.md`](../../workstreams/roadmap.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = roadmap`.

## Focus

This is a monthly sweep over 1.2k LOC, so the code itself will rarely have something new. **The
standing work is the parity gaps**, and that is deliberate:

1. Read the `roadmap` row in `tests/unit/test_surface_parity.py` and re-read its four `Exempt`
   reasons. Three say "tracked follow-up gap"; one (no mode card) is correct by design.
2. For each of the three real gaps — MCP tool, CLI path, plugin skill — check whether an open
   proposal already exists. If not, file one **with a concrete design**, not a restatement of the
   exemption: the tool signature, the flag names, or the skill's steps.
3. If a gap has been proposed and closed unapproved twice, propose instead that the exemption be
   **rewritten to say the gap is permanent**. An exemption that says "tracked" for a year is
   misleading documentation, and that is itself a fixable finding.

Then, briefly: ingest fallbacks degrade to "I could not read this" rather than a confidently wrong
project list, and `intake_mode_for`'s ranking is unchanged.

## Extra stop conditions

- Do not propose promoting roadmap to a top-level mode card. It is a Planning intake card by design
  and the exemption says so.
- Finding nothing is the expected outcome most months once the three gaps are filed. Exit quietly.
