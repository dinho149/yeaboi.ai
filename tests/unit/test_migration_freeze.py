"""The Go-migration freeze table — frozen Python surfaces may not drift silently.

Once the *next* wave of the Go migration lands, the previous wave's area flips
reference: Go becomes the implementation of record and the Python twin is
frozen (`cowork/migration/program.md`, §5 *Dual maintenance & the freeze
mechanism*). A frozen file changing without a Go mirror is exactly the drift
`make parity` cannot always see — the parity corpus pins behaviour, not source
bytes, and a Python-only "fix" to a frozen surface silently forks the twins.

So the freeze is enforced at the byte level: every frozen path is pinned to a
sha256 here, and this test fails the moment the file changes. Editing a frozen
file legitimately means updating the hash **and** mirroring the change to Go
first (for the pieces the sidecar serves — the unmirrored plumbing around them
needs only the hash bump). W19 deletes the frozen files and this table empties.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# {repo-relative path: sha256 of the frozen bytes}. One entry per wave, added
# by the wave PR that lands on top of it (Definition of Done per wave PR,
# item 2). Wave 6 (`analysis.score_docs`, PR #224) froze when Wave 7 landed.
FROZEN_SURFACES: dict[str, str] = {
    # Wave 6 — doc-quality scoring. The mirrored pieces are the ones CLAUDE.md's
    # dual-maintenance table names for `doc_quality.py`; the read/cache plumbing
    # in the same file is not served by the sidecar but freezes with the file.
    # `tools/team_learning.py` (the same wave's `_insight_item` pair) is
    # deliberately NOT here: a whole-file freeze would block the unrelated tool
    # work that file mostly is, and behavioural drift in the mirrored pieces is
    # still caught by `make parity` — the freeze only adds byte-level cover.
    "src/yeaboi/analysis/doc_quality.py": "57df80a38a94c8a41fdff7f8112600c76016bb19a9871fe9bc31b6e67bdcb28d",
}


class TestFrozenSurfaces:
    def test_frozen_files_exist(self) -> None:
        missing = [path for path in FROZEN_SURFACES if not (ROOT / path).is_file()]
        assert not missing, (
            f"frozen surfaces missing from the tree: {missing} — a frozen file is deleted "
            "only at W19, when this table empties (cowork/migration/program.md §5)"
        )

    def test_frozen_files_unchanged(self) -> None:
        drifted = {}
        for path, expected in FROZEN_SURFACES.items():
            actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            if actual != expected:
                drifted[path] = actual
        assert not drifted, (
            f"frozen surfaces changed: {drifted} — Go is the reference for these areas now. "
            "Mirror the change into the Go twin first (make parity must stay green), then "
            "update the pinned sha256 here in the same commit (cowork/migration/program.md §5)"
        )
