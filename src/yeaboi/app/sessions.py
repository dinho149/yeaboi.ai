"""Sessions: turning a cookie into a user, and back.

The ceremonies authenticate with ``?token=`` in the query string, which is right
for a link you paste to a teammate and wrong for an application: a query token
lands in browser history, in ``Referer``, and in every server log. An app session
is a cookie — ``HttpOnly`` so script cannot read it, ``SameSite=Lax`` so it does
not ride along on a cross-site POST, ``Secure`` when the request arrived over
TLS.

``SameSite=Lax`` already stops the classic cross-site form POST, so the CSRF
token here is defence in depth rather than the only guard. It is a *double
submit*: the same value is set as a readable cookie and must be echoed in a
header, which an attacker on another origin cannot do because they cannot read
the cookie.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from yeaboi.app.store import AppStore

#: How long a session lasts. Long enough not to annoy, short enough to matter.
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60

SESSION_COOKIE = "yeaboi_session"
CSRF_COOKIE = "yeaboi_csrf"
CSRF_HEADER = "X-Yeaboi-CSRF"


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf: str
    expires_at: float


class SessionStore:
    """Issue, resolve, and revoke sessions.

    Backed by the same SQLite file as everything else, so a restart does not sign
    everyone out — which is the whole difference between this and the in-memory
    token the boards use.
    """

    def __init__(self, store: AppStore) -> None:
        self._store = store

    def issue(self, user_id: str) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        expires = now + SESSION_TTL_SECONDS
        with self._store._connect() as conn:  # noqa: SLF001 - same package, one owner
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now, expires),
            )
        return IssuedSession(token=token, csrf=csrf, expires_at=expires)

    def resolve(self, token: str) -> str | None:
        """The user id behind a session token, or ``None``.

        Expired rows are deleted on the way past rather than by a sweeper: the
        only moment anyone cares whether a session is stale is when it is
        presented, and doing it here means there is no second mechanism to fail.
        """
        if not token:
            return None
        now = time.time()
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute("SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                return None
        return str(row["user_id"])

    def revoke(self, token: str) -> None:
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def revoke_all(self, user_id: str) -> None:
        """Sign a user out everywhere — the "lost my laptop" button."""
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def cookie_headers(session: IssuedSession, *, secure: bool) -> tuple[tuple[str, str], ...]:
    """The ``Set-Cookie`` pair for a freshly issued session.

    The session cookie is ``HttpOnly``; the CSRF cookie deliberately is **not**,
    because the browser has to read it to echo it back. That asymmetry is the
    entire double-submit design, so it is spelled out here rather than looking
    like an oversight.
    """
    flags = "Path=/; SameSite=Lax" + ("; Secure" if secure else "")
    max_age = int(SESSION_TTL_SECONDS)
    return (
        ("Set-Cookie", f"{SESSION_COOKIE}={session.token}; HttpOnly; {flags}; Max-Age={max_age}"),
        ("Set-Cookie", f"{CSRF_COOKIE}={session.csrf}; {flags}; Max-Age={max_age}"),
    )


def clear_cookie_headers(*, secure: bool) -> tuple[tuple[str, str], ...]:
    flags = "Path=/; SameSite=Lax" + ("; Secure" if secure else "")
    return (
        ("Set-Cookie", f"{SESSION_COOKIE}=; HttpOnly; {flags}; Max-Age=0"),
        ("Set-Cookie", f"{CSRF_COOKIE}=; {flags}; Max-Age=0"),
    )
