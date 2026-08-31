"""The one door every connector's HTTP goes through.

A connector's base URL can be user-owned — self-hosted Grafana, self-hosted
Sentry — which makes it an SSRF vector the LLM-facing tools never had: those
talk to fixed vendor hosts. :func:`assert_safe_url` is what stands between a
stored setting and a request to the cloud metadata endpoint.

Credentials never reach a log or a message from here: transport exceptions go
through the same redaction ``provider_verification._connection_error`` uses.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: Hostnames that are never a legitimate integration endpoint, whatever they
#: resolve to. Checked before resolution so a DNS failure can't skip the guard.
_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal", "instance-data"})

DEFAULT_TIMEOUT = 10


class UnsafeUrlError(ValueError):
    """A URL a connector must not request."""


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Anything not on the public internet, including the cloud metadata address.

    ``is_private`` alone misses a few of these, and 169.254.169.254 is the one
    that actually matters — it is how a stored base URL becomes stolen instance
    credentials.
    """
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def assert_safe_url(url: str) -> str:
    """Return ``url`` if a connector may request it, else raise ``UnsafeUrlError``.

    https only, no credentials in the URL, and the host must not be — or resolve
    to — a private, loopback, link-local or metadata address. Resolution failure
    is NOT fatal: an unresolvable host cannot reach anything, and failing here
    would turn a transient DNS blip into a confusing configuration error.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https":
        raise UnsafeUrlError("URL must start with https://")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL must not embed credentials")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeUrlError("URL has no host")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise UnsafeUrlError(f"{host} is not a reachable integration host")

    # A literal is decided on the spot; a name is decided on what it resolves to.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_private(literal):
            raise UnsafeUrlError(f"{host} is not a public address")
        return url.strip()

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        logger.info("connectors: could not resolve %s — allowing, the request will fail on its own", host)
        return url.strip()
    for info in infos:
        try:
            resolved = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_private(resolved):
            raise UnsafeUrlError(f"{host} resolves to a private address")
    return url.strip()


def get_json(url: str, *, headers: dict[str, str], timeout: int = DEFAULT_TIMEOUT):
    """``GET`` a guarded URL. Returns the raw httpx response.

    Raises ``UnsafeUrlError`` before any request leaves. Transport errors are
    the caller's to translate — ``probe_status`` is the wrapper that does.
    """
    import httpx

    return httpx.get(assert_safe_url(url), headers=headers, timeout=timeout)


def post_form(url: str, *, data: dict[str, str], timeout: int = DEFAULT_TIMEOUT):
    """``POST`` a form body to a guarded URL. Returns the raw httpx response.

    The one writeless POST a read-only layer needs: an OAuth token exchange.
    It goes through the same guard as every GET so the one cloud provider that
    speaks plain REST cannot become the one that skips it.
    """
    import httpx

    return httpx.post(assert_safe_url(url), data=data, timeout=timeout)


def probe_status(url: str, *, headers: dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """``(status_code, "")`` for a completed request, or ``(0, message)``.

    The message is redacted, so a transport error quoting the failed request
    cannot carry a credential into a log line or onto a screen.
    """
    from yeaboi.provider_verification import _connection_error

    try:
        resp = get_json(url, headers=headers, timeout=timeout)
    except UnsafeUrlError as exc:
        return 0, str(exc)
    except Exception as exc:
        return 0, _connection_error(exc)
    return resp.status_code, ""
