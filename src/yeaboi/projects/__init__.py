"""First-class projects — the identity that links sessions across modes.

A project row lives in the shared sessions.db and sessions link to it through
``sessions_meta.project_id``. Scoped context gathering resolves through
``yeaboi.projects.scope``; the store below owns the rows themselves.

Naming hazard: the legacy planning-TUI layer (``yeaboi.persistence``) also
speaks of a "project_id" — a bare uuid4 in ``~/.yeaboi/data/projects.json``,
a render cache in a disjoint id space. These ``proj-<8hex>`` ids never mix
with it, and nothing here reads or writes that file.
"""

from yeaboi.projects.store import ProjectStore, new_project_id

__all__ = ["ProjectStore", "new_project_id"]
