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
from uuid import uuid4

from yeaboi.artifacts.edits import EDIT_OPS, Edit, EditError
from yeaboi.artifacts.registry import ARTIFACTS, spec_for
from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref

logger = logging.getLogger(__name__)

HEADLESS_KINDS = ("standup", "reporting", "retro")
"""Artifacts a correction can reach without a browser.

The three whose stores can take a corrected row (``session._COMMITTERS``). The
rest are correctable on the shared document, where a person is present to decide
what to keep — there is no headless equivalent yet because there is nowhere to
put the result.
"""

SHARED_KINDS = ("standup", "reporting", "retro")
"""Artifacts the TUI currently opens as a *correctable* share.

Deliberately its own tuple even though it equals :data:`HEADLESS_KINDS` today,
because the two answer different questions and will diverge: this one is "does
Share Online let anyone edit this", and it is gated on the same three
committers — a share whose corrections have nowhere to be written back to would
collect them and drop them when the tunnel closed.

Roadmap, the three performance artifacts and the team profile still publish
**read-only** shares. That is a real gap against "every shared document", and it
is reported here rather than left for a caller to discover: an agent that is
told a roadmap is correctable and finds no way to correct it has been misled by
this function, which is the failure this whole module is written to avoid.
"""


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

    Each row carries two reachability flags, because "correctable" is not one
    question. ``headless`` is whether :func:`apply_artifact_edits` can reach the
    artifact from here; ``shared`` is whether the TUI opens it as a correctable
    document rather than a read-only one. Advertising eight kinds while five of
    them raise is worse than advertising three, so both are data rather than
    something a caller discovers by being refused — and today a row with neither
    flag set is a kind whose fields are described here and are not editable
    anywhere yet.
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
                "headless": spec.kind in HEADLESS_KINDS,
                "shared": spec.kind in SHARED_KINDS,
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
    if kind not in HEADLESS_KINDS:
        raise ValueError(
            f"{kind!r} can only be corrected on the shared document — "
            f"headless correction covers {', '.join(HEADLESS_KINDS)}"
        )

    from yeaboi.artifacts.session import EditableSession
    from yeaboi.sharing.editable import ConflictError

    loaded = _load(kind, session_id=session_id, run_id=run_id, engineer=engineer, db_path=_db(db_path))
    if loaded is None:
        raise ValueError(f"no stored {kind} to correct")
    base_id, artifact = loaded

    # One session, which replays everything already on record onto the *base*.
    # Applying through it rather than calling apply_edits separately is what
    # keeps this call's corrections on top of earlier ones rather than instead
    # of them — and anchoring to `base_id` is what stops them being applied
    # twice, since the row this commits becomes the newest one.
    session = EditableSession(
        artifact, kind=kind, db_path=_db(db_path), run_id=base_id, session_id=session_id, engineer=engineer
    )
    ref = session.ref

    applied: list[Edit] = []
    refused: list[dict] = []
    stale: list[dict] = []
    for index, raw in enumerate(edits):
        candidate = Edit(
            # A real random id, not a counter. `revision` advances only for
            # *accepted* edits while `index` advances for every one, so a single
            # refused edit desynchronised the two and a later call re-minted an
            # id already in the replayed log. `apply` then took it for a retry,
            # returned the earlier edit, and this counted it as applied — the
            # caller was told the correction landed and it had been discarded.
            edit_id=str(raw.get("edit_id", "") or f"mcp-{uuid4()}"),
            op=str(raw.get("op", "")),
            path=str(raw.get("path", "")),
            value=str(raw.get("value", "")),
            base=str(raw.get("base", "")),
            label=str(raw.get("label", "")),
            target=str(raw.get("target", "")),
            author=author or str(raw.get("author", "")),
        )
        try:
            stored = session.share.document.apply(candidate)
        except ConflictError as exc:
            # Retryable and reported as such: the artifact moved under this
            # correction, which is a different thing from it never having been
            # acceptable.
            stale.append({"index": index, "id": candidate.edit_id, "reason": "conflict", "detail": str(exc)})
            continue
        except (EditError, ValueError) as exc:
            refused.append({"index": index, "reason": str(exc)})
            continue
        applied.append(stored)

    committed = 0
    if applied and not dry_run:
        for edit in applied:
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


def _load(kind: str, *, session_id: str, run_id: int, engineer: str, db_path: Path) -> tuple[int, Any] | None:
    """Read the *base* artifact a correction log is anchored to, with its row id.

    Deliberately not the latest row. `get_latest_report` returns the corrected
    artifact — that is the whole "edits become the artifact" property, and every
    reader should get it. But a log is recorded against the original and
    replayed onto the original, so building a session on the corrected row
    replays every earlier correction a second time. `set` survives on its
    compare-and-swap; `append`, `note` and `field` have none, and duplicated
    once per call.

    Returning the id matters as much as the artifact: it anchors the log's ref
    to the generated run rather than to whichever corrected row happened to be
    newest, so the ref stops moving as corrections accumulate and headless
    agrees with the TUI, which has always passed the run it is sharing.
    """
    if kind == "standup":
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            return store.get_base_run(session_id=session_id, run_id=run_id)
    if kind == "reporting":
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as store:
            return store.get_base_run(session_id=session_id, run_id=run_id)
    from yeaboi.retro.store import RetroStore

    with RetroStore(db_path) as store:  # kind == "retro"; the caller already gated on HEADLESS_KINDS
        return store.get_base_run(session_id=session_id, run_id=run_id)
