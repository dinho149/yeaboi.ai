"""Bearer-token auth for the desktop backend.

One token per process, minted at startup and handed to exactly one client (the
Electron main process) via the stdout handshake. It travels only in the
``Authorization`` header — never in a query parameter, because query strings
end up in logs, and unlike the board servers we control the only client.

There are no sessions, cookies, or CSRF here on purpose: a cookie is ambient
(any local page could ride it), a header is deliberate, and the desktop shell
is a program, not a browser tab.
"""

from __future__ import annotations

import secrets

#: 32 url-safe random bytes — the same strength the sharing gate uses.
_TOKEN_BYTES = 32

_BEARER_PREFIX = "Bearer "


def mint_token() -> str:
    """Mint the process's bearer token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def check_bearer(headers, token: str) -> bool:
    """True when ``headers`` carries exactly this process's token.

    Constant-time on the token comparison so response timing does not leak how
    much of a guess was right. An empty configured token never authenticates —
    two missing values are equal, which would make "no token yet" a pass.
    """
    if not token:
        return False
    supplied = headers.get("Authorization", "") or headers.get("authorization", "")
    if not supplied.startswith(_BEARER_PREFIX):
        return False
    return secrets.compare_digest(supplied[len(_BEARER_PREFIX) :], token)
