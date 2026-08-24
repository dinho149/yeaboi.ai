"""Native routes for the in-app feedback form.

``feedback.py`` is not an engine and has no MCP tool, deliberately: filing an
issue on a public repository under the user's own GitHub token is not something
an arbitrary tool client should be able to do on their behalf. It is offered
where a person is looking at the form they wrote.

Two calls, matching the two buttons. Polish is one LLM call that rewrites the
draft and returns it for review — it never submits. Submit is the one that
writes, and it never raises: a token path failure comes back as a browser URL
the person can finish by hand.

Screenshot attachments are TUI-only for now (``Ctrl+V`` chips over a terminal
image protocol); the desktop form sends text, and the paths field stays in the
wire shape so a later drag-and-drop needs no new route.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)

_MAX_TITLE = 250
_MAX_DESCRIPTION = 20_000


def options(app, request: Request) -> Response:
    """``GET /api/feedback/options`` — the type and area vocabularies."""
    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_REPO, FEEDBACK_TYPES

    return json_response({"types": list(FEEDBACK_TYPES), "areas": list(FEEDBACK_AREAS), "repo": FEEDBACK_REPO})


def submit(app, request: Request) -> Response:
    """``POST /api/feedback`` — file the issue (API token) or hand back a URL."""
    from yeaboi.feedback import submit_feedback
    from yeaboi.mcp.runtime import to_jsonable

    kind, area, title, description = _draft(request)
    result = submit_feedback(kind, area, title, description)
    logger.info("feedback submitted: type=%s area=%s ok=%s via=%s", kind, area, result.ok, result.via)
    return json_response(to_jsonable(result))


def polish(app, request: Request) -> Response:
    """``POST /api/feedback/polish`` — one LLM rewrite of the draft, for review.

    ``polished`` is null when no LLM is configured or the call failed; ``status``
    says why, and the draft the person wrote is what stands. Never an error
    status: falling back to the original is the designed outcome, not a failure.
    """
    from yeaboi.feedback import polish_feedback

    kind, area, title, description = _draft(request)
    polished, status = polish_feedback(kind, area, title, description)
    return json_response(
        {
            "polished": {"title": polished[0], "description": polished[1]} if polished else None,
            "status": status,
        }
    )


def _draft(request: Request) -> tuple[str, str, str, str]:
    """The four fields both calls take, validated against their vocabularies."""
    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES

    payload = request.json()
    kind = str(payload.get("kind", "")).strip()
    area = str(payload.get("area", "")).strip()
    title = str(payload.get("title", "")).strip()
    description = str(payload.get("description", "")).strip()
    if kind not in FEEDBACK_TYPES:
        raise HTTPError(400, f"unknown feedback type {kind!r} — one of {', '.join(FEEDBACK_TYPES)}")
    if area not in FEEDBACK_AREAS:
        raise HTTPError(400, f"unknown feedback area {area!r} — one of {', '.join(FEEDBACK_AREAS)}")
    if not title:
        raise HTTPError(400, "a feedback report needs a title")
    if not description:
        raise HTTPError(400, "a feedback report needs a description")
    return kind, area, title[:_MAX_TITLE], description[:_MAX_DESCRIPTION]
