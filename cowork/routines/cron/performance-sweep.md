# performance sweep

**Trigger** — cron `30 7 10,24 * *` (10th and 24th, 07:30 UTC)
**Summary** — the 1:1 and review pipeline, and whether it declines to conclude on thin data
**Workstream** — [`workstreams/performance.md`](../../workstreams/performance.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = performance`.

## Lenses

Run these before the scout and hand it the output — see
[hygiene-lenses.md](../../hygiene-lenses.md).

- `dead-code`
- `assertion-free-tests`
- `layering`

## Focus

- **Sample-size honesty** — enumerate every metric that feeds a 1:1 or a review, and confirm each has
  a path that declines to conclude on thin data. One engineer over one sprint is a handful of points;
  a confident sentence built on that is the worst bug this mode can have.
- **Beta labelling** — `src/yeaboi/beta.py` still import-free, `available: True` intact, and no beta
  caveat sitting in an engine warning where `--strict` would fail on it.
- **Note durability** — `perf_note_add` writes something a manager may quote a year later. Confirm
  the store's round-trip tests cover every field and that no migration can drop a note.
- **Redaction on delivery** — `performance/delivery.py` sends this content somewhere. Confirm no name
  and no note reaches a log.

## Extra stop conditions

- **Nothing in this mode's auto lane may change what it says about a person.** Tests, dead code, and
  docs only. Every inference change proposes, with the consequence stated in the issue.
