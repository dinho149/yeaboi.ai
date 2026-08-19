# Third-party notices

This project vendors adapted source from the following projects. Each vendored
file names its origin and the changes made in its module docstring.

## Headroom

- Project: https://github.com/headroomlabs-ai/headroom
- License: Apache License, Version 2.0 — full text ships in the package at
  `src/yeaboi/licenses/headroom-APACHE-2.0.txt`
- Upstream NOTICE attribution: `src/yeaboi/licenses/headroom-NOTICE.txt`
  ("Headroom — Copyright 2025 Headroom Contributors"; the remainder of the
  upstream NOTICE lists libraries Headroom itself uses, none of which are
  vendored here)

Vendored (adapted) files:

- `src/yeaboi/agentwatch/waste_audit.py` — from `headroom/audit/reads.py`
  (v0.35.0): the Read-waste audit over Claude Code session transcripts.
- `src/yeaboi/agentwatch/cache_signals.py` — from the detector half of
  `headroom/transforms/cache_aligner.py` (v0.35.0): structural volatile-content
  classification (UUID / ISO 8601 / JWT-shape / hex-hash).

Both files were modified for yeaboi; see each module docstring for the list of
changes. The remainder of this repository is not derived from Headroom.

## Semantica

- Project: https://github.com/semantica-agi/semantica
- License: MIT ("Copyright (c) 2026 Hawksight AI") — full text ships in the
  package at `src/yeaboi/licenses/semantica-MIT.txt`

Vendored (adapted) files:

- `src/yeaboi/provenance/records.py`, `src/yeaboi/provenance/integrity.py`,
  and `src/yeaboi/provenance/store.py` — from `semantica/provenance/`
  (`schemas.py`, `integrity.py`, `storage.py`, `manager.py` at commit
  `15171fd3`): the PROV-O-shaped decision record, its SHA-256 checksum, and
  the tamper-evident hash-chained SQLite log with `verify_chain`.

All three files were substantially rewritten for yeaboi (append-only rows
instead of upsert-plus-relabel, every field inside the hash, separator-joined
canonical form); see each module docstring for the list of changes. The
conflicts vocabulary in `src/yeaboi/provenance/conflicts.py` is a fresh
implementation that imitates the shape of `semantica/conflicts/` and copies no
code. The remainder of this repository is not derived from Semantica.
