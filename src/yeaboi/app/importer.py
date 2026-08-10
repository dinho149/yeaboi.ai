"""Importing a project out of the TUI and into the app.

The two stores stay separate — ``persistence.py`` keeps the TUI's whole-file
JSON, ``app/store.py`` keeps the app's SQLite — and this is the one place they
meet. It **copies**; it does not share, alias, or migrate.

That is a deliberate limit rather than a missing feature. Making the TUI read
through a server would mean ``yeaboi`` stops working on a plane, and the local
tool working offline is most of why it exists. So an import is a snapshot: the
plan as it stood, filed against a project the app owns. Re-importing makes a
second artifact rather than mutating the first, because two people looking at
"the plan" and seeing different documents is worse than two dated ones.
"""

from __future__ import annotations

import logging

from yeaboi.app.store import AppStore, Project
from yeaboi.html_exporter import plan_export_args
from yeaboi.persistence import load_graph_state

logger = logging.getLogger(__name__)


def import_plan(
    store: AppStore,
    user_id: str,
    tui_project_id: str,
    *,
    into_project_id: str = "",
) -> tuple[Project, str] | None:
    """Copy a TUI project's plan into the app as an artifact.

    Returns ``(project, artifact_id)``, or ``None`` when the TUI project cannot
    be read or the user may not write to the target.

    ``into_project_id`` puts the plan in an existing app project; without it a
    new one is created named after the plan. The second is the common case —
    an import is usually the first thing that happens to a project.
    """
    graph_state = load_graph_state(tui_project_id)
    if not graph_state:
        logger.info("no TUI project %r to import", tui_project_id)
        return None

    args = plan_export_args(graph_state)
    payload = args["report"]
    if not isinstance(payload, dict) or "kind" not in payload:
        # An exporter that stops emitting a kind would file an artifact the
        # bundle cannot draw, and a blank report is discovered by whoever opens
        # it rather than by anything here.
        logger.warning("plan payload for %r carries no kind; refusing to import", tui_project_id)
        return None

    title = str(args.get("title") or "Plan")

    if into_project_id:
        project = store.project(into_project_id, user_id)
        if project is None:
            return None
    else:
        project = store.create_project(title, user_id)

    artifact = store.create_artifact(project.id, user_id, str(payload["kind"]), title, payload)
    if artifact is None:
        return None
    return project, artifact.id
