"""The self-contained browser poker page served to teammates.

``build_poker_html()`` returns ONE HTML string — the React bundle from
``frontend/src/poker`` inlined by :func:`yeaboi.web.assets.render_page`, with no
external requests of any kind. The tunnel CSP forbids every external origin and
a teammate on a phone has no second round-trip available, so everything the
board needs arrives in one document.

The page is **token-free**: ``GET /`` is unauthenticated, so baking the access
token into the HTML would hand it to anyone who reaches the board — which, over a
public tunnel, is anyone with the link. The client reads the token from its own
URL (``?token=``) or by typing the short **join code** into the gate
(``POST /api/join``).

Vote secrecy is server-enforced (``PokerBoard.state_snapshot``): while the round
is open the payload carries no vote values at all, so there is nothing here for
a client bug to leak early.

What Python owns is the shell and the boot island in :func:`board_config`.
Everything the board does lives in TypeScript and is tested there; the contract
tests for this file cover the seam.

# See docs: "Guardrails" — output validation / escaping
"""

from __future__ import annotations

import logging

from yeaboi.music import CHANNELS
from yeaboi.names import ADJECTIVES, NOUNS
from yeaboi.web.brand import build_chrome

logger = logging.getLogger(__name__)


def board_config(project_name: str = "", scope_label: str = "") -> dict[str, object]:
    """Return the boot payload the React board reads from its JSON island.

    Deliberately small, for the same reason retro's is: the deck, the avatars
    and the theme names are server-validated tuples that
    ``scripts/gen_web_types.py`` already emits into ``frontend/src/types/enums.ts``
    with a ``--check`` in CI. Shipping them here as well would give one tuple two
    sources of truth, and the island wins at runtime — so a stale bundle would
    offer a card value the board is going to refuse.

    **The facts are static ones only** — see the note in ``retro/page.py``. The
    ticket position, the vote count and the timer all move during a session and
    arrive over ``/api/state``; this document is built once at server start.

    Never put a secret in here. ``GET /`` is unauthenticated, so anyone who
    reaches the board can read every byte of it, token or not.
    """
    return {
        "chrome": build_chrome(
            mode="poker",
            title="Planning Poker",
            wordmark="poker",
            subtitle=project_name,
            facts=[("PROJECT", project_name), ("SCOPE", scope_label)],
        ),
        "scope": scope_label,
        "adjectives": list(ADJECTIVES),
        "nouns": list(NOUNS),
        # The same internet-radio library the TUI plays (yeaboi.music.CHANNELS).
        "musicChannels": [{"name": c["name"], "url": c["url"]} for c in CHANNELS],
    }


def _document_title(project_name: str, scope_label: str) -> str:
    """Tab title naming the session, not just the mode.

    A host with a board per team had every tab reading "Planning Poker".
    Falls back to the bare mode name when the board has neither name.
    """
    named = " · ".join(part for part in (project_name, scope_label) if part)
    return f"Planning Poker — {named}" if named else "Planning Poker"


# Shown before the bundle mounts, and to anyone with JavaScript disabled. A
# planning-poker session is a live surface with no static rendering, so this
# points somewhere real rather than just apologising.
_NOSCRIPT = (
    "<noscript>This planning-poker board needs JavaScript. Ask the host to "
    "export the session instead — the exported summary is a plain document.</noscript>"
)


def build_poker_html(project_name: str = "", scope_label: str = "") -> str:
    """Return the poker board: one self-contained, token-free document."""
    from yeaboi.web.assets import render_page  # noqa: PLC0415 - avoids an import cycle via html_theme

    html = render_page(
        bundle="poker",
        title=_document_title(project_name, scope_label),
        data=board_config(project_name, scope_label),
        # Layers poker's gold accent over whichever palette the visitor chose.
        html_attrs='data-mode="poker"',
        body=_NOSCRIPT,
    )
    logger.debug("poker: page built (%d bytes)", len(html.encode("utf-8")))
    return html
