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

logger = logging.getLogger(__name__)

# Shared base. `default-src 'none'` means every fetch type is denied unless
# named below, so anything added to a bundle that reaches the network fails
# closed. Inline style and script are unavoidable — the whole point of these
# documents is that they are one file with no external references — but that is
# the *only* concession: with no external origins there is nowhere to exfiltrate
# to, and with no 'unsafe-eval' a payload cannot be assembled from a string.
_CSP_BASE = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; font-src data:; "
    # base-uri: a <base> tag injected into the document would silently retarget
    # every relative URL on the page, including the join POST.
    "base-uri 'none'; frame-ancestors 'none'"
)

# The artifact is a finished snapshot. It has no reason to talk to anything.
ARTIFACT_CSP = f"{_CSP_BASE}; connect-src 'none'; form-action 'none'"

# The gate does exactly one thing the artifact does not: POST the code back to
# its own origin. 'self' is the narrowest policy that permits it.
#
# form-action stays 'none' — the <form> exists for Enter-key semantics and its
# submit handler always calls preventDefault, so a real form navigation only
# happens if the script is broken, in which case it would leak the typed code
# into a URL. Denying it turns that into a no-op instead.
GATE_CSP = f"{_CSP_BASE}; connect-src 'self'; form-action 'none'"

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
