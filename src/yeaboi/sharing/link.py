"""The secure-link half of a live surface: cloudflared, the tier, and expiry.

A board binds loopback, so a Cloudflare tunnel is the only way a teammate
reaches it. Bringing that tunnel up is slow (binary download, edge handshake,
DNS gate) and every surface that does it runs the same sequence on a worker
thread while its own loop keeps drawing: fetch the binary → decide the tier →
arm the gate → start → publish the URL → narrate whatever went wrong.

That sequence lived three times over — in the retro page, the poker page and the
output-share screen — with the same expiry callback, the same never-fall-back
rule and three sets of nearly-identical status strings. :class:`SecureLink` is
the one copy. It owns the thread, the tunnel and the status text; a caller owns
only what it draws.

The link never falls back to a public quick tunnel when the Access tier refuses:
a host who configured Access and silently got a ``trycloudflare.com`` URL is
worse off than one who got no share at all.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: How long ``tunnel.start()`` may take before it is treated as failed. Covers
#: the URL, edge registration, a possible ``--region`` retry and the DNS gate.
START_TIMEOUT_SECONDS = 45

#: Below this many seconds of tunnel life left, :meth:`SecureLink.expiry_notice`
#: starts warning — long enough to wrap up or re-share before the invite dies.
EXPIRY_WARNING_SECONDS = 300

STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_OFF = "off"
"""Tunnels are disabled (``YEABOI_NO_TUNNEL``) — nothing was published."""

_FETCHING = "Setting up the secure link — fetching cloudflared (first use, ~40MB)…"
_STARTING = "Starting secure Cloudflare tunnel (verifying it's reachable)…"
_NO_BINARY = "Secure link failed — could not obtain cloudflared (see logs)."
_OFF = "Sharing is off (YEABOI_NO_TUNNEL) — this board is yours only."
_READY = "Link ready — send it and the code to your team."
_EXPIRED = "Secure link expired after the configured timeout — reconnect to get a fresh link."


class SecureLink:
    """Owns one board's Cloudflare tunnel, from setup through expiry.

    ``server`` is any board server exposing ``port``, ``set_public_url`` and
    ``set_access_gate``; ``set_share_state`` is called when the server has one
    (the retro board's invite panel reads it).

    ``tunnel_factory`` replaces the whole cloudflared path with
    ``port -> url | None`` so the lifecycle is testable without a network.
    """

    def __init__(
        self,
        server: object,
        *,
        surface: str,
        on_ready: Callable[[], None] | None = None,
        tunnel_factory: Callable[[int], str | None] | None = None,
    ) -> None:
        self.server = server
        self.surface = surface
        self._on_ready = on_ready
        self._tunnel_factory = tunnel_factory
        self._lock = threading.Lock()
        self._tunnel: object | None = None
        self._state = STATE_IDLE
        self._status = ""
        self._url = ""
        self._expired = False

    # -- what a caller draws ----------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def status(self) -> str:
        """The current one-line narration (progress, result, or failure)."""
        with self._lock:
            return self._status

    @property
    def url(self) -> str:
        """The participant URL, or ``""`` until the tunnel is up."""
        with self._lock:
            return self._url

    @property
    def starting(self) -> bool:
        return self.state == STATE_STARTING

    @property
    def failed(self) -> bool:
        return self.state == STATE_FAILED

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._expired

    def expiry_notice(self) -> str:
        """Time-critical link-health text, or ``""`` when there is none.

        A ceremony can run 90 minutes and a quick tunnel gets a fresh random
        hostname on every launch, so once this one expires the invite already
        sent to everyone is permanently dead. Callers render this *above* their
        own sticky status text — it is the one message that must not be
        swallowed by whatever the host last pressed.
        """
        if self.expired:
            return self.status
        with self._lock:
            tunnel = self._tunnel
        remaining = tunnel.time_until_expiry() if tunnel is not None else None
        if remaining is None or remaining > EXPIRY_WARNING_SECONDS:
            return ""
        minutes = max(1, -(-int(remaining) // 60))  # ceil to whole minutes
        return f"Secure link expires in ~{minutes} min — reconnecting will need a fresh invite."

    def snapshot(self) -> dict:
        """Everything a non-terminal surface needs to render the link."""
        return {
            "state": self.state,
            "status": self.status,
            "url": self.url,
            "failed": self.failed,
            "expired": self.expired,
            "starting": self.starting,
            "notice": self.expiry_notice(),
        }

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Bring the link up on a worker thread. A no-op while one is running.

        Safe to call again after a failure — that is the Retry Link path.
        """
        from yeaboi.config import tunnels_disabled

        with self._lock:
            if self._state == STATE_STARTING:
                return
            if tunnels_disabled() and self._tunnel_factory is None:
                # Opt-out for dry runs and offline networks. The board still
                # works on loopback; it just has nothing to share.
                self._state, self._status, self._expired = STATE_OFF, _OFF, False
                logger.info("%s: tunnel disabled by YEABOI_NO_TUNNEL — board is host-only", self.surface)
                self._set_share_state("off")
                return
            self._state, self._status, self._expired = STATE_STARTING, _FETCHING, False

        logger.info("%s: starting secure link setup", self.surface)
        threading.Thread(target=self._worker, name=f"{self.surface}-tunnel-setup", daemon=True).start()

    def stop(self) -> None:
        """Stop the tunnel. Safe to call more than once, and mid-setup.

        ``CloudflareTunnel.stop`` also cancels an in-flight ``start``, which is
        why the worker publishes the tunnel *before* starting it.
        """
        with self._lock:
            tunnel, self._tunnel, self._url = self._tunnel, None, ""
        if tunnel is None:
            return
        try:
            tunnel.stop()
        except Exception:  # noqa: BLE001 — teardown must never raise into a caller
            logger.debug("%s: tunnel stop failed", self.surface, exc_info=True)

    # -- internals ---------------------------------------------------------

    def _set(self, **values) -> None:
        with self._lock:
            for name, value in values.items():
                setattr(self, f"_{name}", value)

    def _set_share_state(self, state: str) -> None:
        """Tell the server how to describe the link on its own invite panel."""
        setter = getattr(self.server, "set_share_state", None)
        if setter is not None:
            setter(state)

    def _fail(self, status: str) -> None:
        self._set(state=STATE_FAILED, status=status)
        self._set_share_state("failed")

    def _on_expired(self) -> None:
        """Runs on the tunnel's timer thread once the configured timeout passes.

        Un-publishes server-side too, so ``/api/invite`` and the QR stop handing
        out a dead link.
        """
        self._set(tunnel=None, url="", expired=True)
        self.server.set_public_url("")  # type: ignore[attr-defined]
        self._fail(_EXPIRED)
        logger.info("%s: secure link expired", self.surface)

    def _worker(self) -> None:
        try:
            if self._tunnel_factory is not None:
                url = self._tunnel_factory(self.server.port)  # type: ignore[attr-defined]
                if not url:
                    self._fail("Secure link failed — the tunnel did not start.")
                    return
                self._publish(url)
                return

            from yeaboi.sharing.tunnel import ensure_cloudflared, open_tunnel

            binary = ensure_cloudflared()
            if binary is None:
                logger.warning("%s: secure link failed — could not obtain cloudflared binary", self.surface)
                self._fail(_NO_BINARY)
                return
            self._set(status=_STARTING)

            # One call decides the tier — quick tunnel or Access named tunnel
            # plus the identity gate the server verifies against.
            transport = open_tunnel(self.server.port, surface=self.surface, binary=binary, on_expire=self._on_expired)  # type: ignore[attr-defined]
            if transport.tunnel is None:
                logger.warning("%s: secure link unavailable — %s", self.surface, transport.error)
                self._fail(f"Secure link unavailable — {transport.error}")
                return
            # Armed before start(), so verification is on before the door is.
            self.server.set_access_gate(transport.gate)  # type: ignore[attr-defined]
            # Published before start(), which blocks for the whole handshake
            # budget: stop() must be able to reach a tunnel that is still coming
            # up, or closing a board mid-setup orphans a cloudflared child.
            self._set(tunnel=transport.tunnel)
            url = transport.tunnel.start(timeout=START_TIMEOUT_SECONDS)
            if not url:
                transport.tunnel.stop()
                self._set(tunnel=None)
                # Access-tier failures are host *setup* errors — a credentials
                # file that is not there, a tunnel id Cloudflare does not know —
                # so name the one that happened rather than sending the host to
                # their router.
                detail = getattr(transport.tunnel, "last_error", "")
                logger.warning("%s: secure link failed — %s", self.surface, detail or "tunnel did not start in time")
                self._fail(f"Secure link failed — {detail}." if detail else "Secure link failed — see the logs.")
                return
            self._publish(url)
        except Exception as exc:  # noqa: BLE001 — a failed link must not touch the board
            logger.error("%s: secure link setup failed: %s", self.surface, exc, exc_info=True)
            self._fail(f"Secure link failed — {exc}")

    def _publish(self, url: str) -> None:
        """Record a live URL and tell the server its own public address."""
        public = url.rstrip("/") + "/"
        # Token-free: teammates still enter the join code, and the host token is
        # never handed out in a shareable link.
        self._set(url=public, state=STATE_READY, status=_READY)
        self.server.set_public_url(public)  # type: ignore[attr-defined]
        # Clears a previous attempt's failure, so a retry puts the board's own
        # invite panel back to "ready" too.
        self._set_share_state("pending")
        logger.info("%s: secure link ready (port=%s)", self.surface, getattr(self.server, "port", "?"))
        if self._on_ready is not None:
            try:
                self._on_ready()
            except Exception:  # noqa: BLE001 — a caller's reaction is not the link's problem
                logger.debug("%s: on_ready callback failed", self.surface, exc_info=True)
