"""Sign-in: proving the address is yours.

## What this replaces, and why it had to

The first cut took an email address and issued a session for it. That is not
authentication — it is a name badge. Anyone could sign in as anyone, which is
tolerable on a laptop for an afternoon and is the single thing that stops this
being deployable.

## The shape

A **one-time token**, delivered out of band:

1. ``request_login`` mints a token, stores only its SHA-256, and hands the raw
   value to a :class:`Deliverer`.
2. The user comes back with the raw token.
3. ``consume_login`` hashes it, finds the row, checks it is neither expired nor
   spent, marks it spent, and returns the email.

Only then does a session exist. Possession of the token is the proof, so the
security of the whole thing is the security of the channel it was delivered on
— which is exactly the property that lets the channel be swapped for OAuth or
SSO later without any of the rest moving.

## The details that are load-bearing

* **The database stores a hash, never the token.** A stolen ``app.db`` yields
  nothing that can be presented.
* **Single use.** ``used_at`` is set inside the same transaction that reads the
  row, so two simultaneous redemptions cannot both win.
* **Spent is distinct from absent.** A replayed link and a forged one must be
  tellable apart, or neither can be alerted on.
* **Constant-time comparison** on the hash, so response timing does not leak
  how much of a guess was right.
* **A request for an unknown address behaves identically to a known one.** The
  endpoint must not become a way to ask which addresses have accounts.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from yeaboi.app.store import AppStore

logger = logging.getLogger(__name__)

#: How long a sign-in link is good for. Short: it is a one-shot credential
#: sitting in an inbox, and the user is by definition about to use it.
LOGIN_TTL_SECONDS = 15 * 60

#: The most links one address may ask for inside the window below. Without this
#: the endpoint is a mail cannon pointed at whoever the attacker names.
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 15 * 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def looks_like_email(email: str) -> bool:
    """Deliberately shallow.

    Anything stricter rejects real addresses, and the actual proof of ownership
    is the delivered token — validation here only stops obvious nonsense from
    reaching the deliverer.
    """
    local, _, domain = email.partition("@")
    return bool(local) and bool(domain) and "." in domain and " " not in email


@dataclass(frozen=True)
class LoginRequest:
    email: str
    token: str
    expires_at: float


class Deliverer(Protocol):
    """How a sign-in link reaches its owner.

    A protocol rather than a concrete mailer because the delivery channel *is*
    the trust anchor, and it is the piece most likely to differ between a
    laptop, a self-hosted box and a hosted deployment.
    """

    def deliver(self, request: LoginRequest) -> None: ...


class LogDeliverer:
    """Writes the link to the log. The default, and dev-only.

    yeaboi has no SMTP dependency and no outbound network posture, and adding
    one to make sign-in work locally would be a poor trade. So the default
    prints, loudly, and :class:`AppServer` refuses to use it when cookies are
    marked secure — the closest available signal for "this is not a laptop".
    """

    def __init__(self) -> None:
        self.delivered: list[LoginRequest] = []

    def deliver(self, request: LoginRequest) -> None:
        self.delivered.append(request)
        logger.warning(
            "SIGN-IN LINK for %s (dev delivery, expires in %d min): /signin?token=%s",
            request.email,
            LOGIN_TTL_SECONDS // 60,
            request.token,
        )


class SmtpDeliverer:
    """Emails the sign-in link.

    Uses ``smtplib`` and the ``STANDUP_SMTP_*`` settings the project already
    has, rather than a new dependency and a second set of environment
    variables. A user who has configured standup email has configured this.

    The STARTTLS dance mirrors ``standup/delivery.py`` deliberately: two
    different opinions about when to upgrade a connection, in one codebase, is
    how one of them ends up sending credentials in the clear.

    ``base_url`` is required and not guessed. The link has to be absolute, and a
    server behind a proxy or a tunnel cannot know its own public address — the
    Host header is attacker-controlled, so deriving it from the request would
    let someone mint a valid-looking link pointing at their own host.
    """

    def __init__(
        self,
        base_url: str,
        *,
        host: str = "",
        port: int = 0,
        user: str = "",
        password: str = "",
        sender: str = "",
    ) -> None:
        from yeaboi.config import (  # noqa: PLC0415 - keeps import cost off the app's hot path
            get_smtp_host,
            get_smtp_password,
            get_smtp_port,
            get_smtp_sender,
            get_smtp_user,
        )

        self.base_url = base_url.rstrip("/")
        self.host = host or get_smtp_host()
        self.port = port or get_smtp_port()
        self.user = user or get_smtp_user()
        self.password = password or get_smtp_password()
        self.sender = sender or get_smtp_sender()
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        if not self.host:
            raise ValueError("no SMTP host configured (STANDUP_SMTP_HOST)")

    def link(self, request: LoginRequest) -> str:
        from urllib.parse import quote  # noqa: PLC0415

        return f"{self.base_url}/signin?token={quote(request.token)}"

    def deliver(self, request: LoginRequest) -> None:
        import smtplib  # noqa: PLC0415
        from email.message import EmailMessage  # noqa: PLC0415

        message = EmailMessage()
        message["Subject"] = "Your yeaboi sign-in link"
        message["From"] = self.sender
        message["To"] = request.email
        message.set_content(
            "Someone asked to sign in to yeaboi with this address.\n\n"
            f"{self.link(request)}\n\n"
            f"The link works once and expires in {LOGIN_TTL_SECONDS // 60} minutes.\n"
            "If this was not you, nothing has happened and you can ignore this email."
        )
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.ehlo()
            if smtp.has_extn("STARTTLS"):
                smtp.starttls()
                smtp.ehlo()
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(message)
        # The token is deliberately absent from this log line. It is a live
        # credential, and "we sent it" is the fact worth recording.
        logger.info("sign-in link sent to %s", request.email)


class InsecureDelivererError(RuntimeError):
    """Raised when the dev deliverer would be used somewhere it must not be."""


class LoginTokens:
    """Mint and redeem one-time sign-in tokens."""

    def __init__(self, store: AppStore) -> None:
        self._store = store

    def request(self, email: str) -> LoginRequest | None:
        """Mint a token, or ``None`` when the address is over its rate limit.

        The caller must answer identically either way — see the module header.
        """
        email = normalise_email(email)
        now = time.time()
        with self._store._connect() as conn:  # noqa: SLF001 - same package, one owner
            recent = conn.execute(
                "SELECT COUNT(*) AS n FROM login_tokens WHERE email = ? AND created_at > ?",
                (email, now - LOGIN_RATE_WINDOW_SECONDS),
            ).fetchone()
            if recent["n"] >= LOGIN_RATE_LIMIT:
                logger.warning("sign-in rate limit reached for %s", email)
                return None
            token = secrets.token_urlsafe(32)
            expires = now + LOGIN_TTL_SECONDS
            conn.execute(
                "INSERT INTO login_tokens (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash(token), email, now, expires),
            )
        return LoginRequest(email=email, token=token, expires_at=expires)

    def consume(self, token: str) -> str | None:
        """Spend a token and return the address it proves, or ``None``.

        Every failure returns ``None`` rather than distinguishing itself to the
        caller: an endpoint that says *why* a token failed tells an attacker
        whether they are close.
        """
        if not token:
            return None
        digest = _hash(token)
        now = time.time()
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT token_hash, email, expires_at, used_at FROM login_tokens WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            # The lookup above is already an equality match; this guards the
            # comparison itself against timing analysis.
            if not secrets.compare_digest(str(row["token_hash"]), digest):
                return None
            if row["used_at"] is not None:
                logger.warning("sign-in token replayed for %s", row["email"])
                return None
            if row["expires_at"] <= now:
                return None
            # Marking spent inside the same transaction as the read is what
            # makes two simultaneous redemptions unable to both succeed.
            spent = conn.execute(
                "UPDATE login_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now, digest),
            )
            if spent.rowcount != 1:
                return None
        return str(row["email"])

    def purge_expired(self) -> int:
        """Drop rows that can no longer be redeemed. Housekeeping, not security."""
        with self._store._connect() as conn:  # noqa: SLF001
            cursor = conn.execute("DELETE FROM login_tokens WHERE expires_at <= ?", (time.time(),))
        return cursor.rowcount
