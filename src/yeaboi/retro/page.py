"""The self-contained browser board page served to teammates.

``build_board_html()`` returns ONE HTML string — the React bundle from
``frontend/src/retro`` inlined by :func:`yeaboi.web.assets.render_page`, with no
external requests of any kind. That is not a style preference: the tunnel CSP
forbids every external origin, and a teammate on a phone over Cloudflare has no
second round-trip available. Everything the board needs arrives in one document.

The page is **token-free**: ``GET /`` is unauthenticated, so baking the access
token into the HTML would leak it to any LAN peer. The client reads the token
from its own URL (``?token=``) or obtains it by typing the short **join code**
into the gate (``POST /api/join``).

What Python is responsible for is only the *seam* — the shell, and the boot
island in :func:`board_config`. Everything the board does lives in TypeScript
and is tested there; the contract tests for this file assert the properties the
TS suite structurally cannot see (self-containment, no leaked secrets, a
script-safe island).

# See docs: "Guardrails" — output validation / escaping
"""

from __future__ import annotations

import logging

from yeaboi.music import CHANNELS
from yeaboi.names import ADJECTIVES, NOUNS

logger = logging.getLogger(__name__)


def board_config(sprint_name: str = "") -> dict[str, object]:
    """Return the boot payload the board reads from its JSON island.

    Deliberately small. The grids, carried statuses, reaction emojis, avatars
    and theme names are **not** here even though the plan's sketch had them:
    every one is a server-validated tuple, and ``scripts/gen_web_types.py``
    already emits them into ``frontend/src/types/enums.ts`` from these same
    constants, with a ``--check`` in CI. Shipping them twice would give one
    tuple two sources of truth that can disagree silently — the island naming a
    column the bundle has no heading for.

    So the island carries only what a codegen cannot pin: the free-form word
    lists, the stream library, and this session's titles.

    Never put a secret in here. ``GET /`` is unauthenticated, so everything in
    this dict is readable by any LAN peer without a token.
    """
    return {
        "title": "Sprint Retro",
        "sprint": sprint_name,
        "adjectives": list(ADJECTIVES),
        "nouns": list(NOUNS),
        # The same internet-radio library the TUI plays (yeaboi.music.CHANNELS).
        "musicChannels": [{"name": c["name"], "url": c["url"]} for c in CHANNELS],
    }


# Shown before the bundle mounts, and to anyone with JavaScript disabled. The
# board is a live collaborative surface — there is no static rendering of it to
# fall back to — so this says so rather than leaving a blank page.
_NOSCRIPT = (
    "<noscript>This retro board needs JavaScript. Ask the host to export the "
    "retro instead — the exported summary is a plain document.</noscript>"
)


def build_board_html(sprint_name: str = "") -> str:
    """Return the retro board: one self-contained, token-free document."""
    from yeaboi.web.assets import render_page  # noqa: PLC0415 - avoids an import cycle via html_theme

    html = render_page(
        bundle="retro",
        title="Sprint Retro",
        data=board_config(sprint_name),
        # Layers the retro accent over whichever palette the visitor chose.
        html_attrs='data-mode="retro"',
        body=_NOSCRIPT,
    )
    logger.debug("retro: board page built (%d bytes)", len(html.encode("utf-8")))
    return html
