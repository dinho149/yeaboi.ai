"""Process entry point for ``yeaboi app`` — wiring, handshake, lifecycle.

Startup order matters and is the contract the desktop shell relies on:

1. single-instance lock (idempotent respawn re-prints the live handshake)
2. ``fs_policy.set_interactive(True)`` — the desktop session is interactive;
   denials queue consent requests instead of hard-failing forever
3. MCP dispatcher (optional — tool routes 503 without the ``mcp`` extra)
4. the consent desk and the awareness watcher — the two things that publish
   onto the ambient feed without a request waiting on them
5. bind ``127.0.0.1``, write the handshake file, print the one stdout line

stdout carries the handshake line and nothing else — logs go to
``~/.yeaboi/logs/app/`` via the central logging setup. Shutdown (SIGTERM,
SIGINT, or ``POST /api/shutdown``) stops the HTTP loop, stops the dispatcher,
and removes the handshake + lock files.
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from yeaboi.app import instancelock
from yeaboi.app.auth import mint_token
from yeaboi.app.awareness import AwarenessWatcher
from yeaboi.app.dispatch import DispatcherUnavailableError, McpDispatcher
from yeaboi.app.events import EventBus
from yeaboi.app.handshake import Handshake, clear_handshake, ready_line, write_handshake
from yeaboi.app.ops import OperationTable
from yeaboi.app.server import AppServer, serve

logger = logging.getLogger(__name__)


def run_app(port: int = 0, *, host: str = "127.0.0.1", _emit=None) -> int:
    """Run the desktop backend until asked to stop. Returns an exit code.

    ``_emit`` is an injection seam for tests (captures the handshake line
    without owning stdout); the real process always prints.
    """
    from yeaboi import __version__, fs_policy
    from yeaboi.sessions import CURRENT_SCHEMA_VERSION

    emit = _emit if _emit is not None else lambda line: print(line, flush=True)

    lock = instancelock.acquire()
    if isinstance(lock, instancelock.AlreadyRunning):
        # Idempotent respawn: hand the live instance to the caller and exit
        # cleanly — nothing was started, so nothing is torn down.
        emit(ready_line(lock.handshake))
        return 0

    # The desktop session is the third interactive category (TUI, headless,
    # desktop): consent prompts travel over the event feed instead of a popup.
    fs_policy.set_interactive(True)

    bus = EventBus()
    ops = OperationTable()
    dispatcher: McpDispatcher | None = McpDispatcher(bus)
    try:
        dispatcher.start()
    except DispatcherUnavailableError as exc:
        logger.warning("running without tool dispatch: %s", exc)
        dispatcher = None

    stop_event = threading.Event()
    app = AppServer(token=mint_token(), dispatcher=dispatcher, bus=bus, ops=ops, on_shutdown=stop_event.set)
    # Both watch for things no request is waiting on: a sandbox denial raised on
    # a worker thread, and a ceremony or ship gate that moved while the window
    # was closed. Started after the app so they publish onto a live bus.
    app.consent.start()
    awareness = AwarenessWatcher(bus, ships=app.ships)
    awareness.start()
    httpd = serve(host, port, app=app)
    bound_port = httpd.server_address[1]

    handshake = Handshake(
        url=f"http://{host}:{bound_port}",
        token=app.token,
        pid=os.getpid(),
        schema=CURRENT_SCHEMA_VERSION,
        version=__version__,
    )
    write_handshake(handshake)

    def _signal_stop(signum, frame) -> None:  # noqa: ARG001 - stdlib signature
        logger.info("signal %d received — stopping", signum)
        stop_event.set()

    # signal.signal only works from the main thread — which is the only place
    # the real process runs; tests drive run_app on a worker thread and stop
    # it over the API instead.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _signal_stop)
        signal.signal(signal.SIGINT, _signal_stop)

    server_thread = threading.Thread(target=httpd.serve_forever, name="app-http", daemon=True)
    server_thread.start()
    logger.info("yeaboi app serving on %s (pid=%d, tools=%s)", handshake.url, handshake.pid, dispatcher is not None)
    emit(ready_line(handshake))

    try:
        stop_event.wait()
    finally:
        logger.info("yeaboi app stopping")
        # Boards and shares first: they own cloudflared children, and a tunnel
        # that outlives the app keeps forwarding to a port nothing answers on.
        app.boards.stop_all()
        # The webhook receiver rides the same rule — its tunnel and socket
        # must not outlive the process that owns their state.
        from yeaboi.connectors.webhooks.server import stop_server as _stop_webhooks

        _stop_webhooks()
        # Ship runs next: each owns a coding-agent subprocess and possibly a
        # board of its own, and the cancel is cooperative.
        app.ships.stop_all()
        httpd.shutdown()
        server_thread.join(timeout=10)
        httpd.server_close()
        awareness.stop()
        app.consent.stop()
        if dispatcher is not None:
            dispatcher.stop()
        clear_handshake()
        instancelock.release(lock)
    return 0
