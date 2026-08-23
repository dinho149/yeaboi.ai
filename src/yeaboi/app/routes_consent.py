"""Native routes for the filesystem-sandbox consent modal.

The asking half is not a route at all — a denial arrives on the ambient event
feed as ``consent_request`` (see ``app/consent.py``), because nothing the shell
did is waiting on it. These two routes are the answering half, plus a read for
the window that reloaded and missed the event.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.fs_policy import CONSENT_CHOICES

logger = logging.getLogger(__name__)


def pending(app, request: Request) -> Response:
    """``GET /api/consent`` — requests still waiting on an answer.

    A window that reloaded between the event and the click would otherwise
    leave a denied path unresolved with nothing on screen to say so.
    """
    return json_response({"requests": app.consent.open_requests(), "choices": list(CONSENT_CHOICES)})


def resolve(app, request: Request) -> Response:
    """``POST /api/consent/{req_id}`` — allow once, allow always, or deny.

    ``granted`` is what the sandbox now believes, not what the person clicked:
    the two differ only for ``deny``, and a caller that assumed otherwise would
    retry an access that is still refused.
    """
    req_id = request.params.get("req_id", "")
    choice = str(request.json().get("choice", "")).strip()
    if choice not in CONSENT_CHOICES:
        raise HTTPError(400, f"unknown consent choice {choice!r} — one of {', '.join(CONSENT_CHOICES)}")
    try:
        granted = app.consent.resolve(req_id, choice)
    except KeyError:
        raise HTTPError(404, f"no open consent request {req_id!r} — it was answered or expired") from None
    logger.info("consent resolved: %s -> %s (granted=%s)", req_id, choice, granted)
    return json_response({"req_id": req_id, "choice": choice, "granted": granted})
