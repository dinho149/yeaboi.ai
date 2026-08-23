"""Which person a Slack id is — the one mapping in this package a human curates.

Two things about it are worth stating up front, because the obvious design gets
both backwards.

**It never gates an act.** The tempting shape is "resolve the actor, then decide
what they may do", and it is wrong here twice over. Authorisation is already
answered — by the allowlist, against an id Slack's own servers attributed — and
a ceremony belongs to a *session*, not to a person. And a practice verdict's
``member`` is the **subject of the signal, read off the anchor**, never the
voter: a signal is a claim about a change, and anyone the allowlist trusts can
say the detector got it wrong. That is the entire point of the feedback loop.
So exactly one act consults this table, and only to choose between a roster name
and ``@U…`` as an author string. Leg 2 works with this table empty; without that
property the whole lane would do nothing until somebody ran a setup command.

**It is validated on write, not on read.** ``member`` must be a name in that
session's roster at the moment it is bound — the ``edits.validate`` gesture,
applied once, where the person who typed it is standing. A read on the poll path
is then a single indexed SELECT with no roster load and no heavy import, which
matters because that path runs ~144 times a day.

Resolution *suggestions* (below) reuse ``transcript_review.resolve_speakers``'
rules — exact alias match, then a first-token match accepted only when it is
unique — and ambiguity yields nothing. A mis-bound id would put the wrong
person's name on somebody else's correction, which is worse than ``@U…``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from yeaboi.slack.store import SlackStore

logger = logging.getLogger(__name__)


class IdentityError(ValueError):
    """A link that must not be written — a bad id, or a name not in the roster."""


def roster(session_id: str, *, db_path: Path | None = None) -> list[str]:
    """The team names a link may be bound to, for one session.

    Honours ``db_path`` like every other read here. Ignoring it would have
    ``link`` validate against the production roster while writing the binding
    somewhere else — the two halves of one decision reading two databases.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    with StandupStore(db_path or get_db_path()) as store:
        config = store.load_config(session_id) or {}
    return [str(name) for name in config.get("team_members", []) if str(name).strip()]


def resolve(session_id: str, slack_user: str, *, db_path: Path | None = None) -> str:
    """The roster name bound to a Slack id, or '' — never raises.

    The poll path's read. Everything failing to '' is deliberate: an unresolved
    id becomes ``@U…`` on the note, which is the weaker true statement rather
    than a guess wearing somebody's name.
    """
    if not session_id or not slack_user:
        return ""
    try:
        with SlackStore(db_path) as store:
            return store.identity(session_id, slack_user)
    except Exception:  # noqa: BLE001 — an unreadable mapping is an unmapped one
        logger.warning("slack: could not resolve %s in session %s", slack_user, session_id, exc_info=True)
        return ""


def suggest(display_name: str, members: list[str]) -> str:
    """Which roster name a Slack display name probably is, or '' if unclear.

    A convenience for `yeaboi slack link` and nothing else — the *binding* is
    always a human's, because this is the string that goes on their teammate's
    report.
    """
    from yeaboi.standup.engine import _normalize_author
    from yeaboi.standup.transcript_review import resolve_speakers

    if not display_name.strip() or not members:
        return ""
    alias_map = {m: _normalize_author(m) for m in members}
    return resolve_speakers((display_name,), alias_map).get(display_name, "")


def link(session_id: str, slack_user: str, member: str, *, db_path: Path | None = None) -> str:
    """Bind a Slack id to a roster name. Raises ``IdentityError`` on anything unsafe.

    Raises rather than returning a flag, for the allowlist's reason: a link that
    silently did not happen leaves somebody believing their corrections are
    attributed when they are not.
    """
    from yeaboi.slack.allowlist import is_member_id

    if not session_id:
        raise IdentityError("no session to link in — plan a project or run a standup first.")
    actor = slack_user.strip().lstrip("@").upper()
    if not is_member_id(actor):
        raise IdentityError(
            f"{slack_user!r} is not a Slack member id — they look like U0123456789 "
            "(list them with `yeaboi slack members`)."
        )
    name = member.strip()
    known = roster(session_id, db_path=db_path)
    if not known:
        raise IdentityError(
            "this session has no team roster to link against — configure the standup's team first. "
            "Until then a correction from Slack is attributed to the raw id, which still works."
        )
    if name not in known:
        raise IdentityError(f"{name!r} is not on this session's roster ({', '.join(known)}).")
    with SlackStore(db_path) as store:
        store.link_identity(session_id, actor, name)
    return f"{actor} → {name}"


def unlink(session_id: str, slack_user: str, *, db_path: Path | None = None) -> bool:
    """Drop a binding. False when there was nothing bound."""
    with SlackStore(db_path) as store:
        return store.unlink_identity(session_id, slack_user.strip().lstrip("@"))


def listing(session_id: str, *, db_path: Path | None = None) -> list[dict]:
    """Every binding in one session — what `slack link --list` and MCP read."""
    if not session_id:
        return []
    with SlackStore(db_path) as store:
        return store.identities(session_id)
