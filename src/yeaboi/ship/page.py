"""The self-contained browser page for the ship board.

``build_board_html()`` returns ONE HTML string — the ``ship`` React bundle
inlined by :func:`yeaboi.web.assets.render_page`, no external requests. Same
constraints as every board page: the tunnel CSP forbids external origins and a
teammate on a phone has no second round-trip.

**Token-free.** ``GET /`` is unauthenticated, so nothing secret goes in the boot
island — not the token, not the join code, and here especially **not the diff**.
The status, the phases, the patch and the verdict all arrive over the
authenticated ``/api/state`` poll and are scrubbed on the way out
(``ship/board.py``); the boot island carries only the static chrome.

# See docs: "Guardrails" — output validation / escaping
"""

from __future__ import annotations

import logging

from yeaboi.web.brand import build_chrome

logger = logging.getLogger(__name__)


def board_config(story_title: str = "", project_name: str = "") -> dict[str, object]:
    """The boot payload the ship board reads from its JSON island.

    Deliberately tiny and static. Everything that changes during a run — status,
    the phase checklist, the agent's activity, the diff, the validation verdict,
    the cost — arrives over ``/api/state``; the page HTML is built once at server
    start, so a live value baked in here would freeze at go-time.

    Never put a secret here: ``GET /`` is unauthenticated. The story title and
    project name are the only identifying strings, and they are the same
    deliberate widening the retro board makes — a teammate arriving at a live
    board the host is actively inviting them into needs to know which story they
    are about to help ship. What is withheld is everything the diff would
    disclose, which is not here at all.
    """
    return {
        "chrome": build_chrome(
            mode="ship",
            title="Ship",
            wordmark="ship",
            subtitle=story_title,
            facts=[("PROJECT", project_name), ("STORY", story_title)],
        ),
        "story": story_title,
        "project": project_name,
    }


def _document_title(story_title: str, project_name: str) -> str:
    """Tab title naming the run, falling back to the bare mode name."""
    named = " · ".join(part for part in (project_name, story_title) if part)
    return f"Ship — {named}" if named else "Ship"


_NOSCRIPT = (
    "<noscript>This ship board needs JavaScript. Watch the run from the terminal "
    "instead — the approval gate is fully usable there.</noscript>"
)


def build_board_html(story_title: str = "", project_name: str = "") -> str:
    """Return the ship board: one self-contained, token-free document."""
    from yeaboi.web.assets import render_page  # noqa: PLC0415 — avoids an import cycle via html_theme

    html = render_page(
        bundle="ship",
        title=_document_title(story_title, project_name),
        data=board_config(story_title, project_name),
        html_attrs='data-mode="ship"',
        body=_NOSCRIPT,
    )
    logger.debug("ship: board page built (%d bytes)", len(html.encode("utf-8")))
    return html
