"""The access-code gate document and the two Content-Security-Policies.

The gate is what an unauthenticated visitor gets at ``GET /`` on a tunnel URL.
It is rendered from the committed ``gate`` bundle (``frontend/src/gate``) rather
than a Python string, so the retro and poker boards reuse the same React
component.

**What this page may say.** It is reachable by anyone holding the tunnel URL, so
whatever it says is said to a stranger. It names the *mode* — one word from the
fixed vocabulary in ``web/brand.py`` — and wears that mode's accent, because a
teammate who followed a link deserves to know what they are being asked to join,
and "a retro" tells a stranger nothing the host's message did not.

Everything that would actually be a disclosure is still withheld:

* the artifact's title (``ShareDocument.title`` is "1:1 Prep — Ada", "Retro —
  Sprint 42"; the tab title here comes from ``MODE_LABELS`` and never from it),
* the host's name and machine, the sprint, period or engineer it is about,
* its contents, the access token, the join code, and how many people are inside.

``performance`` is excluded from even the mode name — see ``GATE_BRANDED_MODES``.

# See docs: "Guardrails" — access control and untrusted browser input
"""

from __future__ import annotations

import logging
from functools import lru_cache
from html import escape

from yeaboi.web.assets import render_page
from yeaboi.web.brand import (
    DEFAULT_FOOTER,
    GATE_BRANDED_MODES,
    MODE_LABELS,
    MODE_SHARE_PHRASES,
    MODE_WORDMARKS,
    accent_mode,
    frame_title,
)

# The two policies this module's surfaces run under live in ``web.security``
# alongside the board's, so that all three are written and reviewed together.
# Re-exported here because the gate is where callers expect to find them.
from yeaboi.web.security import ARTIFACT_CSP, GATE_CSP

logger = logging.getLogger(__name__)

__all__ = ["ARTIFACT_CSP", "GATE_CSP", "render_gate_page"]

# What an unbranded gate says — the neutral fallback for `performance` and for
# any mode the vocabulary does not know.
_NEUTRAL_TITLE = "Shared with yeaboi"
_NEUTRAL_HEADING = "Someone shared this with you"


def _noscript(heading: str) -> str:
    """The entire server-rendered body — shown only when scripting is off.

    Everything on it is a constant of the mode, never of the share.
    """
    # Escaped even though every value is a module constant from `web.brand`'s
    # fixed vocabulary. This is the one f-string-into-markup left in the file
    # whose entire job is what an untrusted visitor sees, and the cost of the
    # call is nothing next to the cost of someone later making `heading` carry
    # something from the share.
    return (
        "<noscript><main>"
        f"<h1>{escape(heading)}</h1>"
        "<p>Enter the access code shown by the host to view it. "
        "This page needs JavaScript to check the code, and it disappears when the "
        "host stops sharing.</p>"
        "</main></noscript>"
    )


def gate_boot(mode: str = "") -> dict[str, str]:
    """Return the gate's boot island for ``mode``.

    Split out of :func:`render_gate_page` so the payload can be asserted on its
    own — ``test_web_wire_shapes`` snapshots it and ``frontend/.../wire.ts``
    checks the snapshot against ``GateBoot``. Without that, a field renamed on
    the TypeScript side typechecks and ships the neutral gate to every share,
    because ``gate/main.tsx`` treats every prop as optional by design.

    Flat, and drawn only from ``web.brand``'s fixed vocabulary. Nothing about
    the *share* reaches it — see this module's docstring for what that excludes
    and why.
    """
    branded = mode in GATE_BRANDED_MODES
    wordmark = MODE_WORDMARKS.get(mode, "yeaboi") if branded else "yeaboi"
    accent = accent_mode(mode) if branded else ""
    phrase = MODE_SHARE_PHRASES.get(mode, "") if branded else ""

    return {
        "mode": accent,
        "wordmark": wordmark,
        "frameTitle": frame_title(accent),
        "heading": f"Someone shared {phrase} with you" if phrase else _NEUTRAL_HEADING,
        "eyebrow": "Shared from a terminal",
        "cta": "Open",
        # The gate is the last surface whose byline lived in the TSX. Every
        # other one reads it off the island, so a change to the credit had to be
        # made in two languages to take effect everywhere.
        "footer": DEFAULT_FOOTER,
    }


@lru_cache(maxsize=16)
def render_gate_page(mode: str = "") -> str:
    """Return the join-gate document for ``mode``.

    Still a per-process constant, just one per mode rather than one overall:
    nothing in it varies with the share, so ``render_gate_page("retro") is
    render_gate_page("retro")`` holds. Sixteen slots covers the eight modes, the
    empty default, and room to grow.

    The token and join code never appear in it; the code is checked server-side
    in ``sharing.server``.
    """
    branded = mode in GATE_BRANDED_MODES
    label = MODE_LABELS.get(mode, "") if branded else ""
    title = f"{label} — shared with yeaboi" if label else _NEUTRAL_TITLE

    boot = gate_boot(mode)
    heading = boot["heading"]
    accent = boot["mode"]

    html = render_page(
        bundle="gate",
        title=title,
        data=boot,
        body=_noscript(heading),
        head='<meta name="robots" content="noindex, nofollow">',
        # `accent` comes from accent_mode()'s allowlist, so it is safe to
        # interpolate — an unknown mode yields "" and no attribute at all.
        html_attrs=f'data-mode="{accent}"' if accent else "",
    )
    logger.debug("gate page rendered for mode=%r (%d bytes)", mode, len(html))
    return html
