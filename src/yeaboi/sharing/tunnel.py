"""Shared Cloudflare tunnel API — one entry point over both share tiers.

The quick-tunnel implementation remains in :mod:`yeaboi.retro.tunnel` for
compatibility with existing installations and imports; the Access tier's named
tunnel is in :mod:`yeaboi.sharing.access_tunnel`. New output-sharing code reaches
both through this mode-neutral module, leaving one pinned/download-verified
cloudflared and one place that decides which tier a board is on.

:func:`open_tunnel` is that place. Every caller gets the transport *and* the
identity gate from one call and grows no branch of its own — which is the point:
a tier decision spread across three screens is a tier decision that will
eventually differ between them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from yeaboi.retro.tunnel import CloudflareTunnel, ensure_cloudflared

logger = logging.getLogger(__name__)

__all__ = ["CloudflareTunnel", "ShareTransport", "ensure_cloudflared", "open_tunnel"]


@dataclass(frozen=True)
class ShareTransport:
    """What a screen needs to publish a board: a tunnel, a gate, or a reason.

    ``tunnel`` is unstarted — callers own when that happens, because both boards
    start theirs on a worker thread so the TUI stays responsive.

    ``gate`` is ``None`` in the quick tier, where the join code is the boundary
    and there is no identity to verify.

    ``error`` is non-empty only when the host asked for the Access tier and it is
    not usable. **It never means "fall back to a quick tunnel"** — the caller's
    contract is to stay on loopback and show the reason. A host who configured
    Access and silently got a public ``trycloudflare.com`` URL is worse off than
    one who got no share at all, because they believe something untrue about who
    can reach their board.
    """

    tunnel: CloudflareTunnel | None
    gate: object | None = None
    error: str = ""


def open_tunnel(
    port: int,
    *,
    surface: str,
    binary: object | None = None,
    on_expire: Callable[[], None] | None = None,
) -> ShareTransport:
    """Build the tunnel (and identity gate) for one board, per ``YEABOI_SHARE_MODE``.

    ``surface`` is ``retro``, ``poker`` or ``share`` — it selects the per-surface
    hostname override, which exists because one named tunnel serving two boards
    on one hostname sends teammates to whichever connector answers first. See
    :func:`yeaboi.sharing.access_tunnel.claim_hostname`.
    """
    from yeaboi.config import access_mode_enabled
    from yeaboi.sharing.identity import preflight

    # preflight is the whole tier decision, off-mode included: it is what knows
    # that Access variables with no YEABOI_SHARE_MODE ever chosen mean "refuse
    # with the remedy", never "publish a public quick tunnel anyway".
    gate, problem = preflight(surface)
    if gate is None and problem:
        logger.warning("access: not publishing %s — %s", surface, problem)
        return ShareTransport(None, None, problem)
    if not access_mode_enabled():
        return ShareTransport(CloudflareTunnel(port, binary=binary, on_expire=on_expire))  # type: ignore[arg-type]
    if gate is None:
        logger.warning("access: not publishing %s — tier unavailable", surface)
        return ShareTransport(None, None, "Cloudflare Access is not configured")

    from pathlib import Path

    from yeaboi.config import access_credentials_file, access_hostname, access_tunnel_id
    from yeaboi.sharing.access_tunnel import AccessTunnel

    tunnel = AccessTunnel(
        port,
        access_hostname(surface),
        tunnel_id=access_tunnel_id(),
        credentials=Path(access_credentials_file()),
        binary=binary,  # type: ignore[arg-type]
        on_expire=on_expire,
    )
    return ShareTransport(tunnel, gate)
