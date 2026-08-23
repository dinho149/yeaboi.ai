"""Native routes for the shell's own furniture: the duck, the music, the gate.

None of this is a capability — there is no ambience engine and no ambience MCP
tool, because none of it is work anyone would ask an agent to do. What it is, is
state the shell needs before it can draw itself: which stations exist, whether
the duck is muted, whether the pet is on, and whether this beta mode has already
been explained once.

Music is served as a catalogue and nothing else. The terminal plays a station by
handing the URL to ``ffplay``; the desktop hands the same URL to an ``<audio>``
element and needs no binary at all, so playback state lives in the renderer and
only the *preference* comes back here.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)


def ambience(app, request: Request) -> Response:
    """``GET /api/ambience`` — duck, music, saver and pet preferences in one read."""
    from yeaboi import ambience as ambience_state

    return json_response(ambience_state.state())


def set_ambience(app, request: Request) -> Response:
    """``POST /api/ambience`` — persist any subset of the preferences."""
    from yeaboi import ambience as ambience_state

    changes = request.json()
    return json_response(ambience_state.apply(changes))


def beta(app, request: Request) -> Response:
    """``GET /api/beta`` — the one-time entry gates and which are already spent.

    ``seen`` is the whole point: a gate the person has acknowledged must not
    reappear, and the acknowledgement is shared with the terminal.
    """
    from yeaboi.beta import BETA_GATE_COPY, BETA_GATE_FOOTER, BETA_GATE_SUBTITLE, BETA_LABEL
    from yeaboi.config import is_beta_notice_seen

    return json_response(
        {
            "label": BETA_LABEL,
            "subtitle": BETA_GATE_SUBTITLE,
            "footer": BETA_GATE_FOOTER,
            "gates": {
                key: {"headline": copy["headline"], "body": list(copy["body"]), "seen": is_beta_notice_seen(key)}
                for key, copy in BETA_GATE_COPY.items()
            },
        }
    )


def ack_beta(app, request: Request) -> Response:
    """``POST /api/beta/{mode_key}/ack`` — record that the gate was accepted.

    Only Continue writes this. Backing out leaves the notice pending, so
    someone who bailed still gets told next time — the same rule as the TUI.
    """
    from yeaboi.beta import BETA_GATE_COPY
    from yeaboi.config import mark_beta_notice_seen

    mode_key = request.params.get("mode_key", "")
    if mode_key not in BETA_GATE_COPY:
        raise HTTPError(404, f"no beta gate for {mode_key!r} — one of {', '.join(sorted(BETA_GATE_COPY))}")
    mark_beta_notice_seen(mode_key)
    logger.info("beta gate acknowledged: %s", mode_key)
    return json_response({"mode_key": mode_key, "seen": True})
