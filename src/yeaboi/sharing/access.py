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


def participant_url(headers: Mapping[str, str], fallback_host: str) -> str:
    """Return the token-free URL a participant should open, as *they* would type it.

    Built from the request rather than from the server's own address, because the
    server has no idea what it is reachable as: the same process answers on a LAN
    IP and on a ``trycloudflare.com`` hostname, and only the request knows which
    one this visitor came in on.

    The scheme comes from ``X-Forwarded-Proto``, which cloudflared sets. Without
    it the tunnel URL would come out as ``http://`` — which does still work, via a
    redirect, and is why this went unnoticed while the QR was the only consumer.
    It stops being harmless once the same string is copied to the clipboard and
    pasted into a chat, where it is read by a human and cached by a link
    unfurler. A forged header can only mislabel a link the caller already has.
    """
    host = headers.get("Host") or fallback_host
    scheme = "https" if (headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
    return f"{scheme}://{host}/"


def invite_payload(headers: Mapping[str, str], fallback_host: str, join_code: str) -> dict[str, str]:
    """The body of ``GET /api/invite`` on both live boards.

    A function rather than two dict literals in two handlers so the wire fixture
    in ``tests/unit/test_web_wire_shapes.py`` can build the real thing. A fixture
    that reconstructs a payload by hand pins only the reconstruction, and would
    keep passing while the endpoint it claims to describe drifts away from it.

    Contains no secret: the join code is what the reader typed to get here, and
    the URL is the one in their address bar. The host link, which is neither, is
    never returned — see the handlers.
    """
    return {
        "shareUrl": participant_url(headers, fallback_host),
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
