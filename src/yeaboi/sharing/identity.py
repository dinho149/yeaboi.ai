"""Cloudflare Access identity: verify who is on the other end of a shared board.

Every tunnel-borne request is re-verified here against Cloudflare's published
signing keys — signature, audience, issuer and expiry — because the edge's
``Cf-Access-Jwt-Assertion`` header is only a header once it reaches the origin.

Three properties are load-bearing and each has a test:

**Fail closed.** :meth:`AccessVerifier.verify` returns ``None`` for every
failure, every caller treats ``None`` as deny, a cold key cache denies, and
:func:`preflight` refuses to start the tunnel when the keys cannot be fetched.

**The algorithm allowlist.** ``algorithms=["RS256"]`` is what makes ``alg: none``
and HS256-with-the-RSA-public-key impossible by construction.

**Loopback is told apart by the ``Host`` header, not the socket.** cloudflared
connects from ``127.0.0.1``, so ``client_address`` cannot distinguish the host's
own browser from a remote teammate. :meth:`AccessGate.requires_verification`
keys on ``Host`` (and on Cloudflare's own edge headers), which is ours to assert
because the generated ingress pins ``httpHostHeader``.

``PyJWT[crypto]`` is lazy-imported and optional; a missing extra is reported by
:func:`preflight` as a named setup error, never an import crash in a TUI thread.

# See docs: "Guardrails" — input guardrails and human-in-the-loop
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from yeaboi.sharing.access import _is_loopback

logger = logging.getLogger(__name__)

#: How long a successfully-fetched key set is used before a background-ish
#: refresh is attempted. Cloudflare rotates Access signing keys roughly every
#: six weeks, so an hour is far tighter than it needs to be — cheap insurance,
#: and it means a rotation never turns into a support ticket.
JWKS_TTL_SECONDS = 3600.0

#: Minimum gap between JWKS fetches. Without it, a stranger sending tokens with
#: random ``kid`` values would make *us* hammer Cloudflare once per request —
#: turning an unauthenticated endpoint into an outbound amplifier. An unknown
#: ``kid`` inside this window is simply denied.
JWKS_REFRESH_FLOOR_SECONDS = 60.0

#: Network timeout for a JWKS fetch. Short: this runs on a request thread when a
#: key rotates, and a hung fetch would park a board's handler.
JWKS_TIMEOUT_SECONDS = 5.0

#: The header Cloudflare Access stamps on every request it forwards, and the
#: cookie it sets in the browser. The cookie is the fallback because a request
#: that reached us with the cookie but no header did not come through the edge
#: the way we expect — but it is still a token, and it still has to verify.
JWT_HEADER = "Cf-Access-Jwt-Assertion"
JWT_COOKIE = "CF_Authorization"

#: A JWT is three base64 segments; Cloudflare's are ~1 KB. Anything an order of
#: magnitude past that is not a token we would accept, and parsing it is work an
#: unauthenticated caller should not be able to ask for.
_MAX_TOKEN_LEN = 8192


@dataclass(frozen=True)
class VerifiedUser:
    """One authenticated visitor, as Cloudflare Access vouched for them."""

    email: str
    subject: str

    @property
    def pid(self) -> str:
        """The participant id the board stores ownership against.

        Namespaced with ``cf:`` so it can never collide with the browser-minted
        UUID the quick tier uses. That is the whole point of the tier: card
        ownership in ``retro/board.py`` is keyed on a pid, and in the quick tier
        the client chooses its own, so any token holder can claim to be anyone.
        Here the server overwrites whatever the body claimed with this, and
        ``board.py`` needs no change at all — it was always asking for a pid the
        client could not choose.
        """
        return f"cf:{self.subject}"

    @property
    def display_name(self) -> str:
        """A name that is never the client's to choose.

        Falling back to the request body would hand the byline back to the
        caller — the one thing verified identity exists to prevent — so the
        fallback chain stays inside the verified claims. The ``subject`` leg
        is belt-and-braces: ``verify()`` refuses a token with no ``sub``
        (which is what a service token carries), so an empty ``email`` with a
        usable ``subject`` should not occur; if one ever does, the byline
        still comes from the token.
        """
        return self.email.split("@", 1)[0] or self.email or self.subject


class AccessVerifier:
    """Verifies Cloudflare Access JWTs against the team's published signing keys.

    Thread-safe: one instance is shared by every request thread of a board, and
    key-set refreshes are serialised under a lock so a rotation cannot start N
    simultaneous fetches.
    """

    def __init__(
        self,
        team: str,
        aud: str,
        *,
        fetch: Callable[[str], dict] | None = None,
    ) -> None:
        self.team = team
        self.aud = aud
        self.issuer = f"https://{team}.cloudflareaccess.com"
        self.jwks_url = f"{self.issuer}/cdn-cgi/access/certs"
        self._fetch = fetch or _fetch_json
        self._lock = threading.Lock()
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0
        self._attempted_at = 0.0

    # -- key management ---------------------------------------------------

    def warm(self) -> bool:
        """Fetch the key set once, up front. False means *do not start the tunnel*.

        Called by :func:`preflight`. A board published on a hostname whose tokens
        we cannot verify would have to either refuse everyone (a broken share) or
        trust the edge blindly (not the tier the host asked for), so the honest
        answer is to not publish it.
        """
        return self._refresh(force=True)

    def _refresh(self, *, force: bool = False) -> bool:
        """Refetch the JWKS. Returns True if we now hold at least one key."""
        with self._lock:
            now = time.monotonic()
            if not force and (now - self._attempted_at) < JWKS_REFRESH_FLOOR_SECONDS:
                return bool(self._keys)
            self._attempted_at = now
            try:
                document = self._fetch(self.jwks_url)
            except Exception as e:  # noqa: BLE001 - any fetch failure is "keep the old keys"
                logger.warning("access: could not fetch Cloudflare Access keys (%s): %s", self.jwks_url, e)
                return bool(self._keys)
            keys = _parse_jwks(document)
            if not keys:
                logger.warning("access: Cloudflare Access key set was empty or unparseable (%s)", self.jwks_url)
                return bool(self._keys)
            # Replace wholesale rather than merge: a key Cloudflare has stopped
            # publishing has been retired, and continuing to accept tokens signed
            # with it is exactly what rotation is meant to end.
            self._keys = keys
            self._fetched_at = now
            logger.info("access: loaded %d Cloudflare Access signing key(s)", len(keys))
            return True

    def _key_for(self, kid: str) -> object | None:
        """The signing key for ``kid``, refreshing at most once to find it."""
        with self._lock:
            key = self._keys.get(kid)
            stale = (time.monotonic() - self._fetched_at) > JWKS_TTL_SECONDS
        if key is not None and not stale:
            return key
        # Either we have never seen this kid (rotation) or our set is old. One
        # refresh attempt, rate-limited by the floor above, then answer with
        # whatever we hold — including the key we already had, if the refresh
        # was only about staleness and the network is down.
        self._refresh()
        with self._lock:
            return self._keys.get(kid)

    # -- verification -----------------------------------------------------

    def verify(self, headers: Mapping[str, str]) -> VerifiedUser | None:
        """Return the verified visitor, or ``None`` — which every caller reads as deny."""
        token = _token_from(headers)
        if not token:
            return None
        try:
            import jwt  # noqa: PLC0415 - optional dependency, see the module docstring
        except ImportError:
            logger.warning("access: PyJWT is not installed — install yeaboi[access]; refusing the request")
            return None

        try:
            kid = str(jwt.get_unverified_header(token).get("kid", ""))
        except Exception:  # noqa: BLE001 - a malformed token is simply denied
            return None
        if not kid:
            return None
        key = self._key_for(kid)
        if key is None:
            logger.info("access: no signing key for kid=%s — denying", kid[:16])
            return None

        try:
            claims = jwt.decode(
                token,
                key=key,  # type: ignore[arg-type]
                # The allowlist is the control. Without it PyJWT would honour the
                # token's own `alg`, which is how `alg: none` and the
                # HS256-signed-with-the-RSA-public-key confusion both work.
                algorithms=["RS256"],
                audience=self.aud,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except Exception as e:  # noqa: BLE001 - every failure is a denial, none is an error
            logger.info("access: token rejected (%s)", type(e).__name__)
            return None

        subject = str(claims.get("sub", ""))
        email = str(claims.get("email", "")).strip().lower()
        if not subject:
            return None
        return VerifiedUser(email=email, subject=subject)


class AccessGate:
    """What a request handler asks: must this request be verified, and by whom?

    One per server. Holds the hostname the tier is published at, the verifier,
    and the admin allowlist — so a handler's ``_authed`` stays one line and the
    tier's rules live in exactly one place.
    """

    def __init__(self, hostname: str, verifier: AccessVerifier, admin_emails: frozenset[str]) -> None:
        self.hostname = hostname.strip().lower()
        self.verifier = verifier
        self.admin_emails = admin_emails

    def requires_verification(self, headers: Mapping[str, str]) -> bool:
        """True when this request must carry a verified Access token.

        The ``Host`` header, not the socket address — see the module docstring
        for why ``client_address`` cannot answer this. Loopback is the host's own
        browser and stays token-gated as in the quick tier; the published
        hostname is tunnel-borne and must verify; **anything else also must
        verify**, because an unrecognised ``Host`` is a request we cannot place,
        and the safe answer to that is the stricter one.
        """
        host = (headers.get("Host") or "").strip().lower()
        # A request that carries Cloudflare's own edge headers came through the
        # tunnel whatever its Host says. The Host rule depends on cloudflared
        # honouring originRequest.httpHostHeader; if that ever stops applying,
        # this keeps the tier from silently degrading to token gating.
        if headers.get("Cf-Ray") or headers.get("CF-Connecting-IP"):
            return True
        if _is_loopback(host):
            return False
        if host.split(":", 1)[0] != self.hostname:
            # Not a denial by itself — the line below already requires
            # verification — but worth saying, because it means something
            # reached this port claiming a name we do not serve.
            logger.info("access: request for an unexpected Host (%s) — verification required", host[:100])
        return True

    def verify(self, headers: Mapping[str, str]) -> VerifiedUser | None:
        return self.verifier.verify(headers)

    def is_admin(self, user: VerifiedUser | None) -> bool:
        """True when this verified person is a host.

        Exact, case-insensitive set membership. Never a substring test: ``in``
        against a joined string would make ``ada@example.com`` match
        ``ada@example.com.evil.net``.
        """
        return user is not None and bool(user.email) and user.email.lower() in self.admin_emails


# -- the handler-facing helpers --------------------------------------------
#
# Free functions taking the handler, matching ``access.client_key`` — the three
# servers share no base class, and a mixin would have to be inserted into three
# inheritance chains to add what is really two lines each.


def gate_of(handler: object) -> AccessGate | None:
    """The Access gate for this handler's server, or ``None`` in the quick tier."""
    return getattr(getattr(handler, "server", None), "access_gate", None)


# How long one token's verification is trusted before it is re-checked. A
# handler instance serves every request on a keep-alive connection, and a
# board's browser holds one connection through 25 s long polls for a whole
# ceremony — an unbounded memo would keep serving a token whose ``exp`` passed
# mid-session. A minute bounds that staleness; the re-verify is sub-millisecond.
_IDENTITY_MEMO_SECONDS = 60.0


def verified_user(handler: object) -> VerifiedUser | None:
    """The verified visitor behind this request, memoized per token and briefly.

    Keyed on the token string rather than "once per request" because one handler
    instance serves every request on a keep-alive connection. Two requests
    carrying the same token are the same person, so this is equivalent to
    per-request work without a reset hook in three ``handle_one_request``
    implementations — but the memo also expires (``_IDENTITY_MEMO_SECONDS``) so
    the token's own ``exp`` is re-judged on a live connection, keeping the
    fail-closed property honest for sessions that outlive their token.
    """
    gate = gate_of(handler)
    if gate is None:
        return None
    headers = getattr(handler, "headers", {})
    token = _token_from(headers)
    now = time.monotonic()
    cached = getattr(handler, "_identity_cache", None)
    if cached is not None and cached[0] == token and now - cached[2] < _IDENTITY_MEMO_SECONDS:
        return cached[1]
    user = gate.verify(headers)
    handler._identity_cache = (token, user, now)  # type: ignore[attr-defined]
    return user


def identity_required(handler: object) -> bool:
    """True when this request must present a verified token to be served at all."""
    gate = gate_of(handler)
    return gate is not None and gate.requires_verification(getattr(handler, "headers", {}))


def effective_pid(handler, claimed: str) -> str:
    """The pid to read state as: the verified one when the tier is on.

    The read paths (long-poll state, duel role) need this as much as the write
    paths do. A browser sends its own generated id on every poll, so without
    this a verified participant's own cards come back with ``mine`` false and
    they lose the edit and delete controls on them.
    """
    if not identity_required(handler):
        return claimed
    user = verified_user(handler)
    return user.pid if user is not None else ""


def enforce_identity(handler: object, pid: str, author: str) -> tuple[str, str]:
    """Replace a client-asserted identity with the verified one, where there is one.

    **This is what the tier buys.** Card ownership in ``retro/board.py`` is keyed
    on a participant id, and in the quick tier the browser mints its own — so any
    holder of the board token can post ``{"pid": "someone-else"}`` and edit or
    delete that person's cards. Here the server overwrites the claim before the
    board ever sees it, and ``board.py`` needs no change at all: it was always
    asking for a pid the client could not choose.

    The display name is overwritten too, not just the pid. Ownership is what
    ``board.py`` enforces, but a board where the *name on the card* is still
    freely chosen is a board where an accountable pid sits under an
    unaccountable byline — and being able to see who wrote what is the reason a
    team turns this tier on.

    Off the Access path (and for the host's own loopback requests) the client's
    values pass through unchanged, which is what keeps the quick tier working
    exactly as before.
    """
    if not identity_required(handler):
        return pid, author
    user = verified_user(handler)
    if user is None:
        # Fail closed: an unverified request under the tier gets no identity at
        # all rather than the one it asked for. Every caller checks _authed()
        # first, so this is unreachable today — and must stay safe if that
        # ordering ever changes.
        return "", ""
    return user.pid, user.display_name


# -- configuration ---------------------------------------------------------


def _fetch_json(url: str) -> dict:
    """GET a JSON document. Split out so tests can serve a fake JWKS with no network."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - fixed https URL
    with urllib.request.urlopen(req, timeout=JWKS_TIMEOUT_SECONDS) as resp:  # noqa: S310
        return json.load(resp)


def _parse_jwks(document: object) -> dict[str, object]:
    """Turn a JWKS document into ``{kid: key}``, skipping anything unusable."""
    try:
        import jwt  # noqa: PLC0415 - optional dependency
        from jwt.algorithms import RSAAlgorithm  # noqa: PLC0415
    except ImportError:
        return {}
    del jwt

    if not isinstance(document, dict):
        return {}
    keys: dict[str, object] = {}
    for entry in document.get("keys", []) or []:
        if not isinstance(entry, dict):
            continue
        kid = str(entry.get("kid", ""))
        # Only RSA keys, matching the algorithm allowlist in verify(). An EC or
        # symmetric entry here would never be usable anyway, and skipping it
        # quietly is better than letting from_jwk raise mid-refresh.
        if not kid or entry.get("kty") != "RSA":
            continue
        try:
            keys[kid] = RSAAlgorithm.from_jwk(json.dumps(entry))
        except Exception:  # noqa: BLE001 - one bad key must not lose the rest
            logger.info("access: skipping an unparseable signing key (kid=%s)", kid[:16])
    return keys


def _token_from(headers: Mapping[str, str]) -> str:
    """The Access JWT from the header, falling back to the cookie."""
    raw = (headers.get(JWT_HEADER) or "").strip()
    if not raw:
        raw = _cookie(headers.get("Cookie") or "", JWT_COOKIE)
    return raw if 0 < len(raw) <= _MAX_TOKEN_LEN else ""


def _cookie(header: str, name: str) -> str:
    """One cookie value out of a ``Cookie:`` header, without importing http.cookies.

    ``SimpleCookie`` silently drops the *entire* header when any pair in it is
    malformed, which would make a stray cookie from an unrelated app on the same
    hostname log everyone out.
    """
    for pair in header.split(";"):
        key, _, value = pair.partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


def preflight(surface: str, *, assume_mode: bool = False) -> tuple[AccessGate | None, str]:
    """Build the gate for ``surface``, or explain exactly what is missing.

    Returns ``(gate, "")`` when the tier is ready, ``(None, reason)`` when it is
    configured but incomplete or unreachable, and ``(None, "")`` when the tier is
    simply switched off.

    **A partial config is a loud failure, not a fallback.** Setting three of the
    five keys must never produce a ``trycloudflare.com`` URL: the host would
    believe they were behind their identity provider while the board sat on the
    weaker transport. The caller's contract is that a non-empty reason means
    *stay on loopback and say why*.
    """
    from yeaboi.config import (
        ACCESS_ENV_KEYS,
        access_admin_emails,
        access_aud,
        access_credentials_file,
        access_hostname,
        access_mode_enabled,
        access_team,
        access_tunnel_id,
    )

    if not assume_mode and not access_mode_enabled():
        import os  # noqa: PLC0415 - only needed on the "did they half-configure it?" path

        # Not the tier — but Access variables with YEABOI_SHARE_MODE never set is
        # the shape of a host who followed the setup and missed the last line,
        # and they must get this sentence, not a public trycloudflare.com URL.
        # An *explicit* "quick" is different: that host chose the public tier
        # (the Settings toggle writes it), so it stays silent.
        chosen = os.getenv("YEABOI_SHARE_MODE", "").strip()
        if not chosen and any(os.getenv(key, "").strip() for key in ACCESS_ENV_KEYS):
            return None, (
                "Cloudflare Access is configured but not switched on — in Settings ▸ Sharing, "
                "finish setup (Share Mode → verified users) or choose quick tunnels to share publicly"
            )
        return None, ""

    missing = [
        name
        for name, value in (
            ("CLOUDFLARE_TUNNEL_ID", access_tunnel_id()),
            ("CLOUDFLARE_TUNNEL_CREDENTIALS", access_credentials_file()),
            ("CLOUDFLARE_ACCESS_HOSTNAME", access_hostname(surface)),
            ("CLOUDFLARE_ACCESS_TEAM", access_team()),
            ("CLOUDFLARE_ACCESS_AUD", access_aud()),
        )
        if not value
    ]
    if missing:
        return None, f"Cloudflare Access is incomplete — set {', '.join(missing)}"

    try:
        import jwt  # noqa: F401, PLC0415 - presence check only
    except ImportError:
        return None, "Cloudflare Access needs PyJWT — install yeaboi[access]"

    from pathlib import Path  # noqa: PLC0415

    credentials = Path(access_credentials_file())
    if not credentials.is_file():
        return None, f"tunnel credentials file not found: {credentials}"

    verifier = AccessVerifier(access_team(), access_aud())
    if not verifier.warm():
        # Deliberately fatal. Serving a hostname whose tokens we cannot check
        # means every request 403s anyway — better to not publish it and say so.
        return None, "could not reach Cloudflare Access to fetch signing keys — not publishing the board"

    admins = access_admin_emails()
    if not admins:
        logger.info("access: no CLOUDFLARE_ACCESS_ADMIN_EMAILS set — no remote visitor gets host powers")
    return AccessGate(access_hostname(surface), verifier, admins), ""
