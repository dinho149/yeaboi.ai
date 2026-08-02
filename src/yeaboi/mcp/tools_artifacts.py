"""Reading and applying corrections to a stored artifact.

What stays in the TUI: opening a shared, correctable document and watching
teammates fix it in a browser. That needs a tunnel, a join code and a person.

What an agent can do here: see what a report allows to be corrected, read who
changed what, and apply a correction directly — "the standup has Ada's name
wrong, fix it before it goes out" is one call, and it goes through exactly the
same validation, caps and allowlist a browser does.

**Attribution is self-declared.** Every ``author`` in this history was typed by
whoever held the share link. Do not present it as an audit trail.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)


def _artifact_fields(kind: str) -> dict:
    from yeaboi.artifacts.engine import artifact_fields

    return artifact_fields(kind)


def _artifact_edit_history(kind: str, session_id: str, run_id: int, engineer: str, limit: int) -> dict:
    from yeaboi.artifacts.engine import artifact_edit_history
    from yeaboi.mcp.tools_sessions import resolve_session_id

    return artifact_edit_history(
        kind,
        session_id=resolve_session_id(session_id) if not run_id else session_id,
        run_id=run_id,
        engineer=engineer,
        limit=limit,
    )


def _artifact_edit_apply(
    kind: str, edits: list[dict], session_id: str, run_id: int, engineer: str, author: str, dry_run: bool
) -> dict:
    from yeaboi.artifacts.engine import apply_artifact_edits
    from yeaboi.mcp.tools_sessions import resolve_session_id

    return apply_artifact_edits(
        kind,
        edits,
        session_id=resolve_session_id(session_id) if not run_id else session_id,
        run_id=run_id,
        engineer=engineer,
        author=author,
        dry_run=dry_run,
    )


def register(app) -> None:
    """Register the artifact-correction tools on the MCP app."""

    @app.tool()
    async def artifact_fields(ctx: Context, kind: str = "") -> dict:
        """List what may be corrected on an artifact, and what may not.

        Call this before artifact_edit_apply: it returns the editable paths, the
        length caps, the op vocabulary, and the natural key each list is
        addressed by (a member is `member_updates[name=Ada]`, never an index).

        Deliberate absences are reported too, with the reason — nothing carrying
        a URL is editable, and a team profile takes notes but no field edits
        because its numbers are computed.

        Each row's `headless` flag says whether artifact_edit_apply can reach
        that artifact, or whether it is correctable only on the shared document.

        kind: one of standup, reporting, retro, roadmap, analysis,
            performance_prep, performance_completion, performance_review.
            Blank returns every artifact.
        """
        return await run_readonly(_artifact_fields, kind)

    @app.tool()
    async def artifact_edit_history(
        ctx: Context,
        kind: str = "standup",
        session_id: str = "",
        run_id: int = 0,
        engineer: str = "",
        limit: int = 50,
    ) -> dict:
        """Read the corrections recorded against one stored artifact, oldest first.

        Names are self-declared: whoever opened the share link typed them. Report
        them as "recorded by", never as a verified identity.

        kind: the artifact kind (see artifact_fields).
        session_id: blank uses the most recent session.
        run_id: a specific history row; overrides session_id.
        engineer: for the performance artifacts, whose review this is.
        limit: newest cap on rows returned.
        """
        return await run_readonly(_artifact_edit_history, kind, session_id, run_id, engineer, limit)

    @app.tool()
    async def artifact_edit_apply(
        ctx: Context,
        kind: str = "standup",
        edits: list[dict] | None = None,
        session_id: str = "",
        run_id: int = 0,
        engineer: str = "",
        author: str = "",
        dry_run: bool = False,
    ) -> dict:
        """Correct a stored artifact, appending a corrected run rather than overwriting.

        The generated original is kept, and every trend and latest-report read
        picks the correction up on its own. Corrections stack: a second call adds
        to what earlier ones changed rather than replacing them.

        Covers **standup, reporting and retro** — the artifacts whose stores can
        take a corrected row. Call artifact_fields first and check `headless`;
        anything else is correctable only on the shared browser document.

        edits: a list of {op, path, value, base, label, target}. `op` is one of
            set / append / remove / note / field / revert. `path` addresses the
            artifact — get it from artifact_fields. `base` is the value you
            expect to replace; supplying it turns a silent overwrite of prose
            somebody else changed into a reported conflict.
        author: the name to record. Say who asked for the change, not "assistant".
        dry_run: validate and materialise without writing anything.
        """
        return await run_engine(
            ctx,
            _artifact_edit_apply,
            kind,
            edits or [],
            session_id,
            run_id,
            engineer,
            author,
            dry_run,
            needs_llm=False,
        )
