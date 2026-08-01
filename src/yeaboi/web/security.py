"""The headers and Content-Security-Policies every yeaboi-served document carries.

Three servers hand HTML to a browser — ``sharing/server.py`` (the code gate and
the finished artifact), ``retro/server.py`` and ``poker/server.py`` (the live
boards). Each grew its own ``_send``, and they diverged in the way duplicated
code always does: the share server accumulated a full set of protective headers
and a policy per surface, while the two boards — the *publicly tunnelled,
interactive* ones — sent a ``Cache-Control`` and nothing else.

That asymmetry was backwards. A finished artifact is inert; a board takes input
from anyone holding the link. So the headers live here now, in one leaf module
that imports nothing from ``yeaboi``, and each handler's ``_send`` delegates.

# See docs: "Guardrails" — access control and untrusted browser input
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Sent with every document, every surface.
#
# ``no-store, max-age=0`` + ``Pragma`` because a share URL is ephemeral by
# construction: the tunnel dies with the TUI screen, and a cached copy of an
# artifact outliving the share is exactly what the code gate exists to prevent.
# ``Referrer-Policy`` keeps the tunnel hostname out of the Referer of anything
# the visitor clicks next. ``nosniff`` and ``DENY`` are the two that stop a
# response being reinterpreted or reframed by someone else's page.
DOCUMENT_HEADERS: tuple[tuple[str, str], ...] = (
    ("Cache-Control", "no-store, max-age=0"),
    ("Pragma", "no-cache"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
)

# Shared CSP base. `default-src 'none'` means every fetch type is denied unless
# named below, so anything added to a bundle that reaches the network fails
# closed. Inline style and script are unavoidable — the whole point of these
# documents is that they are one file with no external references — but that is
# the *only* concession: with no external origins there is nowhere to exfiltrate
# to, and with no 'unsafe-eval' a payload cannot be assembled from a string.
#
# base-uri: a <base> tag injected into the document would silently retarget
# every relative URL on the page, including the join POST.
_BASE: tuple[tuple[str, str], ...] = (
    ("default-src", "'none'"),
    ("img-src", "data:"),
    ("style-src", "'unsafe-inline'"),
    ("script-src", "'unsafe-inline'"),
    ("font-src", "data:"),
    ("base-uri", "'none'"),
    ("frame-ancestors", "'none'"),
)


def policy(**overrides: str) -> str:
    """Build a CSP from the shared base, replacing or adding directives.

    Keyword names use underscores (``img_src``) for the hyphens a directive
    actually has (``img-src``), because Python. Directives keep the base's
    order and additions follow it, so a policy string is stable across runs and
    a test can compare it literally.
    """
    named = {key.replace("_", "-"): value for key, value in overrides.items()}
    directives = [(name, named.pop(name, value)) for name, value in _BASE]
    directives.extend(named.items())
    return "; ".join(f"{name} {value}" for name, value in directives)


# The artifact is a finished snapshot. It has no reason to talk to anything.
ARTIFACT_CSP = policy(connect_src="'none'", form_action="'none'")

# The gate does exactly one thing the artifact does not: POST the code back to
# its own origin. 'self' is the narrowest policy that permits it.
#
# form-action stays 'none' — the <form> exists for Enter-key semantics and its
# submit handler always calls preventDefault, so a real form navigation only
# happens if the script is broken, in which case it would leak the typed code
# into a URL. Denying it turns that into a no-op instead.
GATE_CSP = policy(connect_src="'self'", form_action="'none'")

# The live boards. Three directives are looser than the artifact's, and each one
# is a fact about the boards rather than a convenience:
#
# connect-src 'self' — the transport is long polling, not SSE and not a
#   WebSocket (``retro/server.py`` explains why: a Cloudflare quick tunnel
#   buffers a streaming body until the origin finishes it). Every request is a
#   complete same-origin fetch built from a relative path in ``runtime/api.ts``,
#   so 'self' costs the boards nothing.
#
# img-src 'self' data: — the invite QR is `<img src="/api/qr?token=…">`, served
#   as image/svg+xml by the board itself. It is *not* a data URI, so dropping
#   'self' here would silently blank the one thing a host shows a teammate.
#   `data:` still covers the inlined duck sprites.
#
# media-src https: — both boards play internet radio from ``yeaboi.music``.
#   An allowlist of the CHANNELS origins looks tidier and would break: one of
#   them is a redirector that lands on a different host, and CSP re-checks the
#   redirect target rather than the URL you wrote. `https:` is the narrowest
#   directive that survives that, and ``test_web_security`` pins every channel
#   URL to https so it stays sufficient as stations change.
BOARD_CSP = policy(
    connect_src="'self'",
    img_src="'self' data:",
    media_src="https:",
    form_action="'none'",
)

# An editable shared artifact. It sits between the two above: it is a finished
# document like the artifact, but it takes input from anyone holding the link
# like a board — and unlike a board, what it takes becomes a *stored* artifact
# that outlives the session. Two directives are looser than ARTIFACT_CSP's, and
# each is earned rather than copied:
#
# connect-src 'self' — the edit POST and the long poll, both same-origin fetches
#   built from relative paths. This is the directive ARTIFACT_CSP's 'none'
#   forbids, and the whole reason an editable document cannot be served under it.
#
# img-src 'self' data: — the invite QR is `<img src="/api/qr?token=…">`, served
#   by this server. A collaborative document wants the same invite affordance a
#   board has; `data:` still covers embedded screenshots.
#
# No media-src: there is no radio on a document, and a directive nothing needs
# is a directive nobody will notice is wrong.
EDIT_CSP = policy(
    connect_src="'self'",
    img_src="'self' data:",
    form_action="'none'",
)


def send_headers(
    handler: BaseHTTPRequestHandler,
    code: int,
    *,
    csp: str | None = None,
    extra: tuple[tuple[str, str], ...] = (),
) -> None:
    """Send a status line and the shared header set, and nothing else.

    Split out of :func:`send_document` for the one response that has no body to
    frame: a 304 from the boards' long poll, which is defined to carry no body
    and therefore needs no ``Content-Length`` for keep-alive to survive.
    """
    handler.send_response(code)
    for name, value in DOCUMENT_HEADERS:
        handler.send_header(name, value)
    for name, value in extra:
        handler.send_header(name, value)
    if csp is not None:
        handler.send_header("Content-Security-Policy", csp)


def send_document(
    handler: BaseHTTPRequestHandler,
    code: int,
    body: bytes,
    content_type: str,
    *,
    csp: str | None = None,
    extra: tuple[tuple[str, str], ...] = (),
) -> None:
    """Write a complete response with the shared header set.

    A free function taking the handler rather than a mixin the three handlers
    inherit: they already have unrelated base classes and their own ``_send``
    call sites, and a function can be unit-tested against a fake handler with no
    socket behind it.

    ``extra`` carries the per-response headers that are not policy — the long
    poll's ``ETag``, so far.
    """
    send_headers(
        handler, code, csp=csp, extra=(("Content-Type", content_type), ("Content-Length", str(len(body))), *extra)
    )
    handler.end_headers()
    handler.wfile.write(body)
