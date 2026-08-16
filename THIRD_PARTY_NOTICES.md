# Third-party notices

This project vendors adapted source from the following projects. Each vendored
file names its origin and the changes made in its module docstring.

## Headroom

- Project: https://github.com/headroomlabs-ai/headroom
- License: Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0)
- Copyright: Headroom contributors

Vendored (adapted) files:

- `src/yeaboi/agentwatch/waste_audit.py` — from `headroom/audit/reads.py`
  (v0.35.0): the Read-waste audit over Claude Code session transcripts.
- `src/yeaboi/agentwatch/cache_signals.py` — from the detector half of
  `headroom/transforms/cache_aligner.py` (v0.35.0): structural volatile-content
  classification (UUID / ISO 8601 / JWT-shape / hex-hash).

Both files were modified for yeaboi; see each module docstring for the list of
changes. The remainder of this repository is not derived from Headroom.
