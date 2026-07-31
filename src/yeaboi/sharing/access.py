"""Shared access credentials and brute-force protection for browser sharing."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping

_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_token() -> str:
    """Return a fresh ~128-bit URL-safe access token."""
    return secrets.token_urlsafe(16)


def make_join_code() -> str:
    """Return an unambiguous, human-typable ``XXXX-XXXX`` access code."""
    raw = "".join(secrets.choice(_JOIN_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def participant_url(headers: Mapping[str, str], fallback_host: str, public_url: str = "") -> str:
    """Return the token-free URL a participant should open, as *they* would type it.

    ``public_url`` — the server's Cloudflare tunnel URL, once it has one — wins
    outright. Every server here binds loopback and is shared only through the
    tunnel, so the host's own browser reaches the board at ``127.0.0.1``. Deriving
    the invite from *that* request would hand the host a link no teammate can
    open. The tunnel URL is the one address that is true for everyone.

    Without it we fall back to the request, because the server has no idea what
    it is reachable as: the same process answers on loopback and on a
    ``trycloudflare.com`` hostname, and only the request knows which one this
    visitor came in on. That path still runs for a participant who arrived
    *through* the tunnel.

    **Returns ``""`` when that fallback would be a loopback address.** A caller
    with no tunnel yet is a host looking at their own board, and ``127.0.0.1`` is
    the one answer that is actively harmful: the invite panel copies whatever
    this returns the moment it opens, so handing it back puts an address on the
    clipboard that resolves to the reader's own machine. Nothing is strictly
    better than that, and every consumer already renders an empty value as "not
    ready" rather than as a link.

    The scheme comes from ``X-Forwarded-Proto``, which cloudflared sets. Without
    it the tunnel URL would come out as ``http://`` — which does still work, via a
    redirect, and is why this went unnoticed while the QR was the only consumer.
    It stops being harmless once the same string is copied to the clipboard and
    pasted into a chat, where it is read by a human and cached by a link
    unfurler. A forged header can only mislabel a link the caller already has.
    """
    if public_url:
        return public_url
    host = headers.get("Host") or fallback_host
    if _is_loopback(host):
        return ""
    scheme = "https" if (headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
    return f"{scheme}://{host}/"


def _is_loopback(host: str) -> bool:
    """True if ``host`` (optionally ``host:port``) names this machine only.

    Deliberately a name check, not a resolve: the question is whether the string
    would be meaningless to a reader elsewhere, and ``localhost`` is meaningless
    to them whatever it resolves to here.

    IPv6 needs the bracket form handled separately — ``[::1]:5173`` has three
    colons, so the ``host:port`` split that works for names and IPv4 would leave
    the port glued to the address.
    """
    host = host.strip()
    if host.startswith("["):  # [::1] or [::1]:5173
        name = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:  # name:port or 1.2.3.4:port
        name = host.rsplit(":", 1)[0]
    else:  # bare name, bare IPv4, or unbracketed IPv6
        name = host
    name = name.lower()
    return (
        name == "localhost"
        or name.endswith(".localhost")
        or name.startswith("127.")
        or name in ("::1", "0:0:0:0:0:0:0:1")
    )


def invite_payload(
    headers: Mapping[str, str],
    fallback_host: str,
    join_code: str,
    public_url: str = "",
) -> dict[str, str]:
    """The body of ``GET /api/invite`` on both live boards.

    A function rather than two dict literals in two handlers so the wire fixture
    in ``tests/unit/test_web_wire_shapes.py`` can build the real thing. A fixture
    that reconstructs a payload by hand pins only the reconstruction, and would
    keep passing while the endpoint it claims to describe drifts away from it.

    Contains no secret: the join code is what the reader typed to get here, and
    the URL is the board's public tunnel address. The host link, which is
    neither, is never returned — see the handlers.
    """
    return {
        "shareUrl": participant_url(headers, fallback_host, public_url),
        "joinCode": join_code,
    }


class JoinLimiter:
    """Thread-safe failed-code throttle shared by Retro and static output sharing."""

    _MAX_FAILS = 8
    _LOCKOUT_S = 300.0

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}
        self._clock = clock

    def blocked(self, ip: str) -> bool:
        with self._lock:
            entry = self._fails.get(ip)
            if entry is None:
                return False
            count, first = entry
            if count < self._MAX_FAILS:
                return False
            if self._clock() - first < self._LOCKOUT_S:
                return True
            del self._fails[ip]
            return False

    def record_failure(self, ip: str) -> None:
        with self._lock:
            count, first = self._fails.get(ip, (0, self._clock()))
            if self._clock() - first >= self._LOCKOUT_S:
                count, first = 0, self._clock()
            self._fails[ip] = (count + 1, first)

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
