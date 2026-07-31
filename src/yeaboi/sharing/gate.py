"""The access-code gate document and the two Content-Security-Policies.

The gate is what an unauthenticated visitor gets at ``GET /`` on a tunnel URL.
It is rendered from the committed ``gate`` bundle (``frontend/src/gate``) rather
than a Python string, so the retro and poker boards can reuse the same React
component when they migrate.

# See docs: "Guardrails" — access control and untrusted browser input
"""

from __future__ import annotations

import logging
from functools import lru_cache

from yeaboi.web.assets import render_page

# The two policies this module's surfaces run under live in ``web.security``
# alongside the board's, so that all three are written and reviewed together.
# Re-exported here because the gate is where callers expect to find them.
from yeaboi.web.security import ARTIFACT_CSP, GATE_CSP

logger = logging.getLogger(__name__)

__all__ = ["ARTIFACT_CSP", "GATE_CSP", "render_gate_page"]

# Shown only when scripting is off. It is also the entire server-rendered body:
# an unauthenticated visitor must learn nothing about what is behind the gate,
# so no title, no mode, no host name — the same silence the old inline gate kept.
_NOSCRIPT = (
    "<noscript><main>"
    "<h1>Someone shared an output with you</h1>"
    "<p>Enter the access code shown by the host to view it. "
    "This page needs JavaScript to check the code, and it disappears when the "
    "host stops sharing.</p>"
    "</main></noscript>"
)


@lru_cache(maxsize=1)
def render_gate_page() -> str:
    """Return the join-gate document.

    Constant — it carries no per-share data, deliberately (see ``_NOSCRIPT``),
    so it is built once per process. The token and join code never appear in
    it; the code is checked server-side in ``sharing.server``.
    """
    html = render_page(
        bundle="gate",
        title="Shared with yeaboi",
        body=_NOSCRIPT,
        head='<meta name="robots" content="noindex, nofollow">',
    )
    logger.debug("gate page rendered (%d bytes)", len(html))
    return html
