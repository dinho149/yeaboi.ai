"""Reader-authored corrections to a generated artifact.

A shared report is produced by one LLM call over whatever the trackers, repos and
doc spaces had to say. When it gets something wrong, the person who knows it is
wrong is usually not the person at the terminal — they are the teammate reading
the link. This package is what lets them fix it: an append-only log of attributed
edits, applied to a frozen artifact to produce a corrected one.

The pieces:

* :mod:`~yeaboi.artifacts.paths` — the grammar for naming one editable place
* :mod:`~yeaboi.artifacts.registry` — which places those are, per artifact kind
* :mod:`~yeaboi.artifacts.edits` — validating one correction, and replaying a log

Nothing here touches HTTP, the database, or the browser. Serving lives in
:mod:`yeaboi.sharing`, and it is a deliberate split: an edit that cannot be
applied is a validation question, not a request question, and it should be
answerable in a unit test with no server in it.
"""

from yeaboi.artifacts.edits import EDIT_OPS, Edit, EditError, EditResult, apply_edits, validate
from yeaboi.artifacts.paths import PathError, Segment, parse_path, render_path, resolve
from yeaboi.artifacts.registry import ARTIFACTS, ArtifactSpec, FieldSpec, editable_field, spec_for, spec_for_artifact

__all__ = [
    "ARTIFACTS",
    "EDIT_OPS",
    "ArtifactSpec",
    "Edit",
    "EditError",
    "EditResult",
    "FieldSpec",
    "PathError",
    "Segment",
    "apply_edits",
    "editable_field",
    "parse_path",
    "render_path",
    "resolve",
    "spec_for",
    "spec_for_artifact",
    "validate",
]
