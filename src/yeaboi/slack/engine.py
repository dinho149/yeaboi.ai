"""The headless surface of the two-way Slack lane.

Three entry points, and the shape of the set is the design: **one that acts, two
that read, and nothing that configures.** The CLI, the MCP tools and the
Ceremonies page are thin adapters over these — the house rule every other mode
follows, applied to a lane whose "TUI" is a Slack thread.

What is deliberately *not* here is as load-bearing as what is. There is no
entry point that installs the recurring poll, sets a token, or edits the
allowlist: those write to the operating system and to ``~/.yeaboi/.env``, and
they are decisions made at the terminal that will run the job. ``slack watch``
stays a CLI verb over ``ceremonies.scheduler`` for the same reason
``ceremonies add`` does.

And ``apply_inbound_events`` is **never an MCP tool**, which is the sharpest
line in the file. The allowlist that authorises an event lives in the poller;
this function inherits that authorisation rather than re-checking it. An MCP
client calling it would be Slack-in-the-loop only by convention — and a lane
whose whole premise is "identity is looked up, never parsed" cannot have a door
where identity is asserted by the caller.

Imports stay inside the functions: the recurring poll starts a fresh process
every few minutes, so nothing here may drag LangChain onto the start-up path.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def apply_inbound_events(*, db_path: Path | None = None) -> dict:
    """Read the Slack window once and apply everything new in it.

    There is no ``scheduled`` flag here, and its absence is the point.
    ``run_ceremony`` needs one because an unattended fire raises questions a
    human at a terminal has already answered — is this too late to still be
    useful, is the month's budget gone. A poll raises none of them: it reads a
    fixed 48-hour window, every act it applies is free and idempotent, and the
    overlap lock and gap notice are therefore **unconditional**. A poll is the
    same poll however it was started. (The installed job's argv still carries
    ``--scheduled``; the CLI accepts it and says so.)

    A poll that declines — no token, no channel, an empty allowlist, another
    poll already running — is **not** a failure. It returns its reason and the
    caller exits 0, so a cron job that could not act does not page anybody.
    """
    from dataclasses import asdict

    from yeaboi.slack.poller import run_poll

    result = run_poll(db_path=db_path)
    logger.info("slack poll: %s (%d applied of %d seen)", result.outcome, result.events_applied, result.events_seen)
    return {**asdict(result), "declined": result.declined}


def inbound_history(*, limit: int = 20, pending: bool = False, db_path: Path | None = None) -> dict:
    """What Slack asked for, and what happened to it — newest first.

    Every event the lane considered is in here, including the refused ones,
    because "you are not on the list", "I could not tell what you meant", "the
    write said no" and "somebody else has this document open" are four different
    problems and only some of them are anyone's to fix. ``pending`` narrows to
    the events claimed but never settled, which is what a crash mid-apply
    leaves — reported, deliberately never retried.
    """
    from yeaboi.slack.store import SlackStore

    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200.")
    with SlackStore(db_path) as store:
        events = store.unsettled(limit=limit) if pending else store.history(limit=limit)
        polls = store.polls(limit=5)
    return {"events": events, "pending_only": pending, "recent_polls": polls}


def link_slack_member(
    session_id: str,
    slack_user: str = "",
    member: str = "",
    *,
    unlink: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Bind a Slack id to a roster name, drop a binding, or list them.

    With no ``slack_user`` this lists. It is off the MCP surface on purpose:
    this decides whose name goes on somebody else's report, and the identity it
    asserts is the one thing in the lane Slack's servers did not attest.
    """
    from yeaboi.slack import identity

    if not slack_user:
        return {"session_id": session_id, "identities": identity.listing(session_id, db_path=db_path)}
    if unlink:
        dropped = identity.unlink(session_id, slack_user, db_path=db_path)
        return {"session_id": session_id, "unlinked": dropped, "slack_user": slack_user}
    detail = identity.link(session_id, slack_user, member, db_path=db_path)
    return {"session_id": session_id, "linked": detail}
