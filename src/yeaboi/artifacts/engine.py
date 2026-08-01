"""Headless access to a stored artifact's corrections.

Three public entry points, and only three — the surface-parity check treats
every public top-level function in an ``engine.py`` as a capability that must be
registered, which is the right pressure: a helper that leaks out here is a
surface nobody decided to ship.

No LLM anywhere in this file. Correcting a report is the one operation in the
product where a model has no business being involved: the whole point is that a
person knows something the model got wrong.

# See docs: "Architecture" — headless engines
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from yeaboi.artifacts.edits import EDIT_OPS, Edit, EditError, apply_edits, validate
from yeaboi.artifacts.registry import ARTIFACTS, spec_for
from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref

logger = logging.getLogger(__name__)


def _db(db_path: Path | None) -> Path:
    from yeaboi.paths import get_db_path

    return db_path or get_db_path()


def _ref(kind: str, session_id: str, run_id: int, engineer: str) -> str:
    return artifact_ref(kind, session_id=session_id, run_id=run_id, engineer=engineer)


def artifact_fields(kind: str = "") -> dict:
    """Describe what may be corrected, for one artifact kind or for all of them.

    The answer an agent needs before it tries: which paths exist, what each is
    called, how long a value may be, and which lists are addressed by which key.
    Returning it is also the honest way to expose the *absences* — a team profile
    reports no editable fields and says why.
    """
    kinds = [kind] if kind else sorted(ARTIFACTS)
    out: list[dict] = []
    for name in kinds:
        spec = spec_for(name)
        if spec is None:
            raise ValueError(f"{name!r} is not an editable artifact")
        out.append(
            {
                "kind": spec.kind,
                "label": spec.label,
                "annotatable": spec.annotatable,
                "note": spec.note,
                "list_keys": dict(spec.list_keys),
                "fields": [
                    {
                        "path": ".".join(field.chain),
                        "kind": field.kind,
                        "label": field.label,
                        "max_length": field.limit(),
                        "max_items": field.max_items,
                    }
                    for field in spec.fields
                ],
            }
        )
    return {"ops": list(EDIT_OPS), "artifacts": out}


def artifact_edit_history(
    kind: str,
    *,
    session_id: str = "",
    run_id: int = 0,
    engineer: str = "",
    limit: int = 50,
    db_path: Path | None = None,
) -> dict:
    """Return the corrections recorded against one artifact, oldest first.

    Names are **self-declared** — whoever held the share link typed them — so a
    caller reading this must not present it as an audit trail. The field is
    called ``author`` and not ``user`` for that reason.
    """
    if spec_for(kind) is None:
        raise ValueError(f"{kind!r} is not an editable artifact")
    ref = _ref(kind, session_id, run_id, engineer)
    with ArtifactEditStore(_db(db_path)) as store:
        edits = store.list_edits(kind, ref, limit=max(0, limit))
        editors = store.editors(kind, ref)
    return {
        "kind": kind,
        "ref": ref,
        "count": len(edits),
        "editors": list(editors),
        "attribution": "self-declared",
        "edits": [
            {
                "id": e.edit_id,
                "seq": e.seq,
                "op": e.op,
                "path": e.path,
                "value": e.value,
                "label": e.label,
                "target": e.target,
                "author": e.author,
                "at": e.at,
            }
            for e in edits
        ],
    }


def apply_artifact_edits(
    kind: str,
    edits: list[dict],
    *,
    session_id: str = "",
    run_id: int = 0,
    engineer: str = "",
    author: str = "",
    dry_run: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Apply corrections to a stored artifact and record them.

    Goes through the *same* :func:`~yeaboi.artifacts.edits.validate` and the same
    allowlist a browser does, which is the reason this exists as an engine rather
    than as a direct store write: an agent fixing a wrong name gets the same
    caps, the same refusals and the same injection sweep as the teammate who
    would otherwise have done it by hand.

    ``dry_run`` validates and materialises without writing anything — the way to
    ask "would this apply cleanly" before committing to it.
    """
    spec = spec_for(kind)
    if spec is None:
        raise ValueError(f"{kind!r} is not an editable artifact")

    from yeaboi.artifacts.session import EditableSession

    ref = _ref(kind, session_id, run_id, engineer)
    with ArtifactEditStore(_db(db_path)) as store:
        existing = store.list_edits(kind, ref)

    artifact = _load(kind, session_id=session_id, run_id=run_id, engineer=engineer, db_path=_db(db_path))
    if artifact is None:
        raise ValueError(f"no stored {kind} to correct")

    accepted: list[Edit] = []
    refused: list[dict] = []
    for index, raw in enumerate(edits):
        candidate = Edit(
            edit_id=str(raw.get("edit_id", "") or f"mcp-{ref}-{len(existing) + index + 1}"),
            op=str(raw.get("op", "")),
            path=str(raw.get("path", "")),
            value=str(raw.get("value", "")),
            base=str(raw.get("base", "")),
            label=str(raw.get("label", "")),
            target=str(raw.get("target", "")),
            author=author or str(raw.get("author", "")),
        )
        try:
            accepted.append(validate(candidate, spec))
        except (EditError, ValueError) as exc:
            refused.append({"index": index, "reason": str(exc)})

    corrected, results = apply_edits(artifact, (*existing, *accepted), spec)
    outcomes = {r.edit_id: r for r in results}
    stale = [
        {"id": e.edit_id, "reason": outcomes[e.edit_id].reason}
        for e in accepted
        if e.edit_id in outcomes and not outcomes[e.edit_id].applied
    ]
    applied = [e for e in accepted if e.edit_id in outcomes and outcomes[e.edit_id].applied]

    committed = 0
    if applied and not dry_run:
        session = EditableSession(
            artifact, kind=kind, db_path=_db(db_path), run_id=run_id, session_id=session_id, engineer=engineer
        )
        for edit in applied:
            session.share.document.apply(edit)
            session.persist(session.share, edit, "")
        committed = session.commit()

    logger.info(
        "artifact edits: kind=%s ref=%s applied=%d refused=%d stale=%d dry_run=%s",
        kind,
        ref,
        len(applied),
        len(refused),
        len(stale),
        dry_run,
    )
    return {
        "kind": kind,
        "ref": ref,
        "applied": len(applied),
        "refused": refused,
        "stale": stale,
        "committed_run_id": committed,
        "dry_run": dry_run,
    }


def _load(kind: str, *, session_id: str, run_id: int, engineer: str, db_path: Path) -> Any:
    """Read the stored artifact a correction targets, or None."""
    if kind == "standup":
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            return store.get_run_by_id(run_id) if run_id else store.get_latest_report(session_id)
    if kind == "reporting":
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as store:
            return store.get_run_by_id(run_id) if run_id else store.get_latest_report(session_id)
    if kind == "retro":
        from yeaboi.retro.store import RetroStore

        with RetroStore(db_path) as store:
            return store.get_run_by_id(run_id) if run_id else store.get_latest_report(session_id)
    raise ValueError(f"{kind!r} cannot be corrected headlessly yet — use the shared document")
