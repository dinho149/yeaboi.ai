"""Who may act from Slack — and the rule that everyone else is ignored quietly.

A Slack member id on a reaction is attributed **by Slack's servers**. That is
the first genuinely attested identity anything in yeaboi has ever had: the edit
log's author is self-declared, and the boards' only identifiers are a
browser-minted presence id and a salted IP hash. It is also only as strong as
the workspace it comes from, which is why this is a small hand-curated list and
not "anyone who can see the channel".

Machine-wide in ``~/.yeaboi/.env`` beside the token and the channel, rather than
per session. A per-session allowlist would just give an attacker a session to
pick, and would make "who may approve this" a question with N different answers.
And not a repo-versioned table either, which is how the cowork fleet's relay
does it: that works because a reviewer already reads that repo, and a
``pip install yeaboi`` user has no such repo to review.

Three rules, all of them failing closed:

1. **Empty means nobody**, and the poll then does not call Slack at all — there
   is no signal it could act on, so the request is waste.
2. **One malformed entry voids the whole list.** A half-filled allowlist is the
   more dangerous of the two states because it *looks* configured; a typo must
   not silently reduce it to the ids that happened to parse.
3. **The bot's own id is never authorised.** Without that, a token with
   ``reactions:write`` plus an acknowledgement reaction is a loop that
   authorises itself.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: ``U…`` on an ordinary workspace, ``W…`` on Enterprise Grid.
_MEMBER_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")

#: Values that parse but were plainly never filled in. Checked case-insensitively
#: against the whole id, so a real id containing an X is unaffected.
_PLACEHOLDERS = frozenset(
    {
        "UXXXXXXXX",
        "UXXXXXXXXX",
        "U000000000",
        "U0000000",
        "UABCDEFGH",
        "U12345678",
        "U123456789",
    }
)


class AllowlistError(ValueError):
    """The allowlist is configured but unusable, so nobody is authorised."""


def is_placeholder(member: str) -> bool:
    """True for an id that parses but is obviously an unedited example."""
    return member.strip().upper() in _PLACEHOLDERS


def is_member_id(value: str) -> bool:
    """True for a well-formed, non-placeholder Slack member id.

    Public because the id shape is one fact with two readers — who may act
    (here) and who somebody *is* (``identity.link``) — and a second copy of this
    regex is a second answer to the same question.
    """
    candidate = value.strip().lstrip("@").upper()
    return bool(_MEMBER_RE.match(candidate)) and not is_placeholder(candidate)


def parse(raw: str) -> tuple[str, ...]:
    """Parse a comma/space-separated member list. Raises on anything malformed.

    Raising rather than filtering is the point: silently dropping the entry
    somebody typed wrong leaves them believing they are on a list they are not.
    """
    entries = [part.strip() for part in re.split(r"[,\s]+", raw or "") if part.strip()]
    # Through ``is_member_id`` rather than the regex directly, so the two readers
    # of "the id shape" cannot drift: a pasted ``@U0123456789`` — which is how
    # Slack's own UI offers it — used to void the entire list here while being
    # perfectly acceptable to ``identity.link``.
    bad = [e for e in entries if not is_member_id(e)]
    if bad:
        raise AllowlistError(
            f"SLACK_ALLOWED_MEMBER_IDS has {', '.join(map(repr, bad))} in it — "
            "every entry must be a Slack member id like U0123456789 "
            "(find them with `yeaboi slack members`). Until this is fixed nobody is authorised."
        )
    # Dedupe, keep the order somebody wrote them in.
    seen: dict[str, None] = {}
    for entry in entries:
        seen.setdefault(entry.lstrip("@").upper(), None)
    return tuple(seen)


def load() -> tuple[str, ...]:
    """The configured allowlist, or () when unset or unusable.

    Never raises: the caller is an unattended poll, and "nobody is authorised"
    is the safe reading of every failure here.
    """
    from yeaboi import config

    raw = config.get_slack_allowed_member_ids()
    if not raw.strip():
        return ()
    try:
        return parse(raw)
    except AllowlistError as e:
        logger.error("slack allowlist unusable: %s", e)
        return ()


def authorised(actor: str, allowed: tuple[str, ...], *, bot_user_id: str = "") -> bool:
    """May ``actor`` drive an action? Fails closed on every unclear case."""
    if not actor or not allowed:
        return False
    if bot_user_id and actor.upper() == bot_user_id.upper():
        # Otherwise our own acknowledgement reaction authorises the next round.
        logger.debug("slack: ignoring the bot's own reaction")
        return False
    return actor.upper() in allowed


def describe() -> str:
    """One line for `slack check` and the settings row."""
    from yeaboi import config

    raw = config.get_slack_allowed_member_ids()
    if not raw.strip():
        return "not set — nobody can act from Slack, and the poll will not run"
    try:
        members = parse(raw)
    except AllowlistError as e:
        return str(e)
    return f"{len(members)} member(s): {', '.join(members)}"
