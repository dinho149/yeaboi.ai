"""Sign in to a music service: Authorization Code + PKCE, driven a poll at a time.

The shape is :class:`yeaboi.claude_auth.SubscriptionSignIn`'s — a session the
app server holds, the renderer polls, and a credential that is persisted on
the poll that first sees it and never echoed. What differs is the transport:
the vendor sends the user's browser back to a loopback listener of our own.

That listener is its own tiny HTTP server, never the app wire: the app server
answers only to its bearer token, and an OAuth redirect carries none. It binds
``127.0.0.1`` on a FIXED port (8643, ``YEABOI_OAUTH_PORT`` overrides) because
Spotify matches the registered redirect URI exactly, port included — so a busy
port is a hard error naming the fix, never a port walk. It serves exactly one
path per sign-in and answers with a page that says "you can close this tab";
nothing from the query is echoed or logged.

Access tokens are cached in memory and refreshed with a minute's skew; a
refresh the vendor refuses (``invalid_grant`` — Spotify's six-month consent
expiry included) signs the user out cleanly rather than failing every read.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from yeaboi.connectors import http, oauth_clients, registry

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8643
CALLBACK_PATH = "/callback"
#: How long the browser has to come back before the sign-in gives up.
SIGNIN_TIMEOUT_SECONDS = 300
#: Refresh an access token this long before the vendor says it expires.
REFRESH_SKEW_SECONDS = 60


class SignedOutError(Exception):
    """The vendor no longer honours the stored refresh token."""


@dataclass(frozen=True)
class OAuthProvider:
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    #: Extra query on the authorize URL (Google's offline access, for one).
    extra_authorize_params: tuple[tuple[str, str], ...] = ()
    #: Fetch the account's display name with a fresh access token.
    identity: Callable[[str], str] = lambda token: ""
    #: Whether a refresh hands back a new refresh token to persist.
    refresh_rotates: bool = False
    #: Whether the token exchange sends the (non-confidential) client secret.
    sends_secret: bool = False


def _spotify_identity(token: str) -> str:
    resp = http.get_json("https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        return ""
    data = resp.json()
    return str(data.get("display_name") or data.get("id") or "")


def _youtube_identity(token: str) -> str:
    resp = http.get_json(
        "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        return ""
    items = resp.json().get("items") or []
    return str(items[0].get("snippet", {}).get("title", "")) if items else ""


PROVIDERS: dict[str, OAuthProvider] = {
    "spotify": OAuthProvider(
        authorize_url="https://accounts.spotify.com/authorize",
        token_url="https://accounts.spotify.com/api/token",  # noqa: S106 — an endpoint, not a credential
        scopes=(
            "playlist-read-private",
            "playlist-read-collaborative",
            "user-library-read",
            "user-read-recently-played",
            "user-read-playback-state",
            "user-modify-playback-state",
        ),
        identity=_spotify_identity,
        refresh_rotates=True,
    ),
    "youtube_music": OAuthProvider(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106 — an endpoint, not a credential
        scopes=("https://www.googleapis.com/auth/youtube.readonly",),
        extra_authorize_params=(("access_type", "offline"), ("prompt", "consent")),
        identity=_youtube_identity,
        sends_secret=True,
    ),
}


# -- pure helpers --------------------------------------------------------------


def oauth_port() -> int:
    """The port the callback listener binds — fixed, because the registered redirect URI names it."""
    raw = os.environ.get("YEABOI_OAUTH_PORT", "").strip()
    try:
        port = int(raw) if raw else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def redirect_uri(key: str, port: int | None = None) -> str:
    """The literal loopback IP, which Spotify requires in place of ``localhost``."""
    return f"http://127.0.0.1:{port or oauth_port()}{CALLBACK_PATH}/{key}"


def pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` per RFC 7636: S256, base64url, no padding."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(provider: OAuthProvider, client_id: str, redirect: str, state: str, challenge: str) -> str:
    params = [
        ("client_id", client_id),
        ("response_type", "code"),
        ("redirect_uri", redirect),
        ("scope", " ".join(provider.scopes)),
        ("state", state),
        ("code_challenge_method", "S256"),
        ("code_challenge", challenge),
        *provider.extra_authorize_params,
    ]
    return f"{provider.authorize_url}?{urlencode(params)}"


class CallbackError(ValueError):
    """The redirect did not carry a usable code."""


def parse_callback(path: str, expected_state: str) -> str:
    """The authorization code in a callback path, or :class:`CallbackError`.

    Nothing from the query is ever put in a message: the vendor's ``error``
    is named by its code alone, and a wrong state says only that it was wrong.
    """
    query = parse_qs(urlparse(path).query)
    error = (query.get("error") or [""])[0]
    if error:
        raise CallbackError("Sign-in was refused" if error == "access_denied" else "Sign-in did not complete")
    state = (query.get("state") or [""])[0]
    if not state or not secrets.compare_digest(state.encode("utf-8"), expected_state.encode("utf-8")):
        raise CallbackError("Sign-in did not match the one that was started")
    code = (query.get("code") or [""])[0]
    if not code:
        raise CallbackError("Sign-in came back without a code")
    return code


# -- the listener -------------------------------------------------------------

_DONE_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>yeaboi</title>"
    "<body style='font-family:system-ui;padding:3rem'><h1>Signed in</h1>"
    "<p>You can close this tab and go back to yeaboi.</p></body>"
)
_FAILED_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>yeaboi</title>"
    "<body style='font-family:system-ui;padding:3rem'><h1>Sign-in did not complete</h1>"
    "<p>Go back to yeaboi and try again.</p></body>"
)


class _CallbackServer:
    """One loopback HTTP server, alive for one sign-in, that catches one redirect."""

    def __init__(self, key: str, expected_state: str, port: int) -> None:
        self.key = key
        self.expected_state = expected_state
        self.code = ""
        self.error = ""
        self._event = threading.Event()
        wanted = f"{CALLBACK_PATH}/{key}"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 — BaseHTTPRequestHandler's name
                # The path carries the code, so nothing of the request is logged.
                logger.debug("oauth: callback request received")

            def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's convention
                if urlparse(self.path).path != wanted:
                    self._page(404, _FAILED_PAGE)
                    return
                # Settle the result before answering: a client that has read the
                # page must find the session done, not a moment from it.
                try:
                    outer.code = parse_callback(self.path, outer.expected_state)
                    status, page = 200, _DONE_PAGE
                except CallbackError as exc:
                    outer.error = str(exc)
                    status, page = 400, _FAILED_PAGE
                outer._event.set()
                self._page(status, page)

            def _page(self, status: int, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        # Raises OSError on a busy port: the caller turns that into a message.
        self._server = HTTPServer(("127.0.0.1", port), Handler)
        self._server.timeout = 0.2
        self._thread = threading.Thread(target=self._serve, name="oauth-callback", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        deadline = time.monotonic() + SIGNIN_TIMEOUT_SECONDS
        while not self._event.is_set() and time.monotonic() < deadline:
            self._server.handle_request()
        self._server.server_close()

    @property
    def done(self) -> bool:
        return self._event.is_set()

    def close(self) -> None:
        """Stop listening and release the port before returning.

        The next sign-in binds the same fixed port straight away; a socket the
        serve thread is still holding would refuse it with a misleading
        "port busy".
        """
        self._event.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3)


# -- the session ---------------------------------------------------------------


class OAuthSignIn:
    """A running sign-in for one connector, driven a poll at a time.

    Lifecycle: :meth:`start`, then :meth:`poll` until :attr:`done`; :meth:`cancel`
    at any point. Every method is safe to call in any state.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        self.url = ""
        self.account = ""
        self.error = ""
        self.persisted = False
        self._verifier = ""
        self._state = ""
        self._redirect = ""
        self._client: oauth_clients.OAuthClient | None = None
        self._listener: _CallbackServer | None = None
        self._worker: threading.Thread | None = None
        self._refresh_token = ""

    @property
    def done(self) -> bool:
        return bool(self.persisted or self.error)

    @property
    def ok(self) -> bool:
        return self.persisted

    @property
    def message(self) -> str:
        if self.persisted:
            return f"Signed in as {self.account}" if self.account else "Signed in"
        return self.error or "Sign-in cancelled"

    def start(self) -> bool:
        """Bind the listener and build the URL. False (with :attr:`error`) if it could not."""
        provider = PROVIDERS.get(self.key)
        connector = registry.by_key(self.key)
        if provider is None or connector is None or not connector.can_sign_in:
            self.error = "This service has no sign-in"
            return False
        client = oauth_clients.resolve(self.key)
        if client is None:
            field = oauth_clients.CLIENT_ID_ENVS.get(self.key, "")
            self.error = (
                f"yeaboi's {connector.label} app is not configured in this build — "
                f"paste your own client ID ({field}) in Settings"
            )
            logger.warning("oauth: %s sign-in has no client id", self.key)
            return False
        self._client = client
        self._verifier, challenge = pkce_pair()
        self._state = new_state()
        port = oauth_port()
        self._redirect = redirect_uri(self.key, port)
        try:
            self._listener = _CallbackServer(self.key, self._state, port)
        except OSError:
            self.error = (
                f"port {port} is busy — set YEABOI_OAUTH_PORT and update the Redirect URI "
                f"in your own {connector.label} app"
            )
            logger.warning("oauth: %s sign-in could not bind port %d", self.key, port)
            return False
        self.url = authorize_url(provider, client.client_id, self._redirect, self._state, challenge)
        # Which app, without touching the client object: a field of it is a
        # secret, and a log line must not be derived from one.
        own_field = oauth_clients.CLIENT_ID_ENVS.get(self.key, "")
        kind = "own" if own_field and os.environ.get(own_field, "").strip() else "built-in"
        logger.info("oauth: %s sign-in started (%s client)", self.key, kind)
        return True

    def poll(self) -> None:
        """Advance: once the redirect landed, exchange the code off this thread; persist when done."""
        listener = self._listener
        if listener is None or self.done:
            return
        if listener.error and not self.error:
            self.error = listener.error
            logger.warning("oauth: %s callback failed: %s", self.key, self.error)
            return
        if listener.code and self._worker is None:
            self._worker = threading.Thread(
                target=self._exchange, args=(listener.code,), name="oauth-exchange", daemon=True
            )
            self._worker.start()
            return
        if not listener.done and not listener._thread.is_alive() and not self.error:
            self.error = "Sign-in timed out — try again"
            logger.warning("oauth: %s sign-in timed out", self.key)
            return
        if self._refresh_token and not self.persisted:
            self._persist()

    def _exchange(self, code: str) -> None:
        provider = PROVIDERS[self.key]
        client = self._client
        assert client is not None
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect,
            "client_id": client.client_id,
            "code_verifier": self._verifier,
        }
        if provider.sends_secret and client.client_secret:
            data["client_secret"] = client.client_secret
        try:
            resp = http.post_form(provider.token_url, data=data)
        except Exception as exc:  # noqa: BLE001 — any transport failure is one message
            self.error = f"Could not reach {self.key}'s sign-in service: {type(exc).__name__}"
            logger.warning("oauth: %s token exchange failed: %s", self.key, type(exc).__name__)
            return
        if resp.status_code != 200:
            self.error = _exchange_message(self.key, resp.status_code)
            logger.warning("oauth: %s token exchange returned %d", self.key, resp.status_code)
            return
        payload = _token_payload(resp)
        if payload is None:
            self.error = "The sign-in service answered with something other than a token"
            logger.warning("oauth: %s token exchange body was not a token", self.key)
            return
        refresh = str(payload.get("refresh_token") or "")
        access = str(payload.get("access_token") or "")
        if not refresh:
            self.error = "Sign-in finished but no token was returned"
            logger.warning("oauth: %s exchange carried no refresh token", self.key)
            return
        _cache_put(self.key, access, _expires_in(payload))
        try:
            self.account = provider.identity(access) if access else ""
        except Exception:  # noqa: BLE001 — a name is a nicety, not the credential
            self.account = ""
        self._refresh_token = refresh

    def _persist(self) -> None:
        from yeaboi.config import apply_config_value

        connector = registry.by_key(self.key)
        assert connector is not None
        apply_config_value(connector.signin_env, self._refresh_token)
        if connector.account_env:
            apply_config_value(connector.account_env, self.account)
        self.persisted = True
        self._refresh_token = ""
        logger.info("oauth: %s signed in as %s", self.key, self.account or "(unnamed)")

    def cancel(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        if not self.done:
            self.error = "Sign-in cancelled"
        self._refresh_token = ""


def _token_payload(resp) -> dict | None:
    """The token endpoint's JSON object, or None for anything else."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a non-JSON 200 is just not a token
        return None
    return body if isinstance(body, dict) else None


def _expires_in(payload: dict) -> int:
    try:
        return max(1, int(payload.get("expires_in") or 3600))
    except (TypeError, ValueError):
        return 3600


def _exchange_message(key: str, status: int) -> str:
    if key == "spotify" and status in (400, 401, 403):
        return (
            "Spotify refused the sign-in — if you are not allowlisted on yeaboi's app, "
            "paste your own client ID in Settings"
        )
    if status in (400, 401, 403):
        return "The sign-in was refused — check the client ID in Settings"
    return f"The sign-in service answered {status}"


# -- the access-token cache ------------------------------------------------------

_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def _cache_put(key: str, access: str, expires_in: int) -> None:
    with _cache_lock:
        _cache[key] = (access, time.monotonic() + max(0, expires_in - REFRESH_SKEW_SECONDS))


def sign_out(key: str) -> None:
    """Forget the token and the name; the vendor keeps its own consent record."""
    from yeaboi.config import apply_config_value

    connector = registry.by_key(key)
    if connector is None or not connector.can_sign_in:
        return
    apply_config_value(connector.signin_env, "")
    if connector.account_env:
        apply_config_value(connector.account_env, "")
    with _cache_lock:
        _cache.pop(key, None)
    # Pages read as the old account must not be served to the next one.
    from yeaboi.connectors import library

    library.forget(key)
    logger.info("oauth: %s signed out", key)


def bearer_for(key: str) -> str:
    """A live access token for ``key``, refreshed when stale. Raises :class:`SignedOutError`."""
    with _cache_lock:
        cached = _cache.get(key)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    return _refresh(key)


def _refresh(key: str) -> str:
    provider = PROVIDERS.get(key)
    connector = registry.by_key(key)
    if provider is None or connector is None or not connector.can_sign_in:
        raise SignedOutError("This service has no sign-in")
    refresh = os.environ.get(connector.signin_env, "").strip()
    if not refresh:
        raise SignedOutError(f"Sign in to {connector.label} to browse your library")
    client = oauth_clients.resolve(key)
    if client is None:
        raise SignedOutError(f"{connector.label}'s sign-in is not configured")
    data = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client.client_id}
    if provider.sends_secret and client.client_secret:
        data["client_secret"] = client.client_secret
    try:
        resp = http.post_form(provider.token_url, data=data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oauth: %s refresh failed: %s", key, type(exc).__name__)
        raise SignedOutError(f"Could not reach {connector.label} — try again") from None
    if resp.status_code in (400, 401):
        # invalid_grant: revoked, or past the consent's lifetime. A stored token
        # the vendor refuses is worse than none, so it goes.
        logger.warning("oauth: %s refresh refused (%d); signing out", key, resp.status_code)
        sign_out(key)
        raise SignedOutError(f"{connector.label} signed you out — sign in again")
    if resp.status_code != 200:
        logger.warning("oauth: %s refresh returned %d", key, resp.status_code)
        raise SignedOutError(f"{connector.label} answered {resp.status_code} — try again")
    payload = _token_payload(resp)
    access = str(payload.get("access_token") or "") if payload else ""
    if not access:
        raise SignedOutError(f"{connector.label} returned no token — sign in again")
    _cache_put(key, access, _expires_in(payload or {}))
    rotated = str(payload.get("refresh_token") or "")
    if provider.refresh_rotates and rotated and rotated != refresh:
        from yeaboi.config import apply_config_value

        apply_config_value(connector.signin_env, rotated)
    return access


def reset_cache_for(key: str) -> None:
    """Forget one cached access token so the next read refreshes it."""
    with _cache_lock:
        _cache.pop(key, None)


def reset_cache() -> None:
    """Test seam."""
    with _cache_lock:
        _cache.clear()
