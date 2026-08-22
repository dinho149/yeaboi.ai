"""MCP tools: the two-way Slack lane — read what a team asked for, and what happened.

**Read-only, and for a sharper reason than the ceremonies tools'.** Those are
read-only because declaring a ceremony installs a job on the user's machine.
These are read-only because of what the lane *is*: every inbound act is
authorised by an allowlist checked against a member id **Slack's own servers
attributed**, and the engine inherits that authorisation rather than re-checking
it. An "apply" tool here would let any MCP client fabricate an event and drive a
verdict, a pause or a correction with no Slack in the loop at all — a door where
identity is asserted by the caller, in a lane whose whole premise is that
identity is looked up and never parsed.

``link_slack_member`` is absent for a second reason, and it is not the same one:
it is safe, but it decides whose name goes on somebody else's report. That is
the one binding in the lane Slack did not attest, so it stays a human's, typed
at a terminal (`yeaboi slack link`).
"""

from __future__ import annotations

import logging

from yeaboi.mcp.runtime import run_readonly

logger = logging.getLogger(__name__)


def _history(limit: int, pending: bool):
    from yeaboi.slack.engine import inbound_history

    return inbound_history(limit=limit, pending=pending)


def _identities(session_id: str):
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.slack import identity

    resolved = resolve_session_id(session_id)
    return {"session_id": resolved, "identities": identity.listing(resolved)}


def register(app) -> None:
    """Attach the Slack tools to the FastMCP app."""

    @app.tool()
    async def slack_inbound_history(limit: int = 20, pending: bool = False) -> dict:
        """What the team asked for from Slack — reactions and thread replies on what
        yeaboi posted — and what became of each one. Every considered event is here,
        including the refusals, with the reason: ``unauthorized`` (not on the
        allowlist), ``ignored`` (not part of the grammar), ``refused`` (the write said
        no), ``deferred`` (someone had the report open) and ``stale`` (the post is too
        old to answer). ``recent_polls`` says whether the reader is even running.

        Set ``pending`` to see only events claimed but never settled — what a crash
        mid-apply leaves behind. Those are reported and deliberately never retried.

        Use to answer "did my thumbs-down register", "why did nothing happen when I
        reacted", or "is the Slack poll actually alive"."""
        return await run_readonly(_history, limit, pending)

    @app.tool()
    async def slack_identities_list(session_id: str = "") -> dict:
        """Which Slack users are bound to which team members in this session. The
        binding is used for exactly one thing: choosing between a roster name and a
        raw ``@U…`` as the author of a correction typed in a Slack thread. It never
        decides what anyone is allowed to do — that is the allowlist, against an id
        Slack's servers attributed.

        An empty list is a working configuration, not a broken one: everything in the
        lane works unbound, and corrections are simply attributed to the raw id.
        Bind one at the terminal with `yeaboi slack link`."""
        return await run_readonly(_identities, session_id)
