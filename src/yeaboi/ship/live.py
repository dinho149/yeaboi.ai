"""The live board's lifecycle for one ship run — server, tunnel, callback fan-in.

A thin orchestrator the ship TUI drives: it owns the :class:`ShipBoard`, its
loopback :class:`ShipServer`, and the Cloudflare tunnel, and it routes the
engine's two callbacks — ``on_progress`` (the phase checklist) and
``on_agent_line`` (the agent's ``stream-json`` output) — into the board.

Two lifecycle facts shape it:

- **The tunnel start is slow** (binary download + edge handshake + DNS gate, up
  to ~45 s) and must never block the run. :meth:`start` binds the loopback
  server synchronously — instant — then brings the tunnel up on a daemon thread
  and publishes the URL to the running server when it lands.
- **The board is created from the run id**, which only exists once the engine
  mints it (``on_run_id``). So the TUI constructs this session inside that
  callback and then feeds it the later progress/agent events.

The tunnel is injectable (``tunnel_factory``) so the whole lifecycle is testable
without a network: a test passes a factory that returns a URL (or ``None``) and
asserts the board projects it, with no cloudflared involved.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: A tunnel factory: given the loopback port, return the public URL (or ``None``
#: if a tunnel could not be established). The default brings up a real
#: Cloudflare quick tunnel; tests pass a stub.
TunnelFactory = Callable[[int], str | None]


class ShipBoardSession:
    """Owns the live board (server + tunnel) for one ship run."""

    def __init__(
        self,
        run_id: str,
        *,
        db_path: Path | None = None,
        story_title: str = "",
        project_name: str = "",
        tunnel_factory: TunnelFactory | None = None,
    ) -> None:
        from yeaboi.config import get_ship_server_port  # noqa: PLC0415 — lazy, keeps import cheap
        from yeaboi.ship.board import ShipBoard  # noqa: PLC0415
        from yeaboi.ship.server import ShipServer  # noqa: PLC0415

        self.board = ShipBoard(run_id, db_path=db_path, story_title=story_title, project_name=project_name)
        self.server = ShipServer(self.board, port=get_ship_server_port())
        self._tunnel_factory = tunnel_factory
        self._tunnel = None  # the real CloudflareTunnel, when one is used
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Bind the loopback server now; bring the tunnel up in the background."""
        self.server.start()
        self._started = True
        from yeaboi.config import tunnels_disabled  # noqa: PLC0415

        if tunnels_disabled() and self._tunnel_factory is None:
            logger.info("ship board: tunnels disabled; board is loopback-only at %s", self.host_url)
            return
        threading.Thread(target=self._bring_up_tunnel, name="ship-board-tunnel", daemon=True).start()

    def _bring_up_tunnel(self) -> None:
        """Establish the tunnel and publish its URL. Never raises into the thread."""
        try:
            if self._tunnel_factory is not None:
                url = self._tunnel_factory(self.server.port)
            else:
                from yeaboi.sharing.tunnel import CloudflareTunnel  # noqa: PLC0415

                self._tunnel = CloudflareTunnel(self.server.port)
                url = self._tunnel.start()
            if url:
                self.server.set_public_url(url)
                logger.info("ship board: shareable at %s (code %s)", url, self.server.display_code)
            else:
                logger.warning("ship board: tunnel did not come up; board is loopback-only")
        except Exception:  # noqa: BLE001 — a failed tunnel must not touch the run
            logger.debug("ship board: tunnel setup failed", exc_info=True)

    def stop(self) -> None:
        """Tear down the tunnel and server. Safe to call more than once."""
        tunnel = self._tunnel
        self._tunnel = None
        if tunnel is not None:
            try:
                tunnel.stop()
            except Exception:  # noqa: BLE001
                logger.debug("ship board: tunnel stop failed", exc_info=True)
        if self._started:
            self._started = False
            self.server.stop()

    # -- engine callback fan-in -------------------------------------------

    def note_component(self, item: dict) -> None:
        """Route one ``on_progress`` component event to the board."""
        self.board.note_component(item)

    def note_agent_line(self, line: str) -> None:
        """Route one ``on_agent_line`` stream-json event to the board."""
        self.board.note_agent_line(line)

    # -- what the TUI shows the host --------------------------------------

    @property
    def host_url(self) -> str:
        """The host's private link (token + admin). Do not share."""
        return self.server.url

    @property
    def share_url(self) -> str:
        """The tunnel URL to hand out, or ``""`` until it is up."""
        return self.server.share_url

    @property
    def display_code(self) -> str:
        """The short join code teammates type on the gate."""
        return self.server.display_code
