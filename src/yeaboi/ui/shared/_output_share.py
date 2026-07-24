"""Shared TUI flow for temporarily publishing one generated HTML artifact."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.sharing.server import OutputShareServer, ShareDocument
from yeaboi.ui.shared._animations import loading_border_color
from yeaboi.ui.shared._components import PAD, Theme, build_action_buttons

logger = logging.getLogger(__name__)


def _build_output_share_screen(
    *,
    title_fn: Callable,
    theme: Theme,
    document_title: str,
    status: str,
    public_url: str = "",
    join_code: str = "",
    message: str = "",
    actions: list[str] | None = None,
    action_sel: int = 0,
    width: int = 100,
    height: int = 30,
    shimmer_tick: float | None = None,
    loading: bool = False,
) -> Panel:
    """Render the shared output-sharing lifecycle screen.

    # See docs: "Architecture" — shared TUI components and fixed page structure
    """
    actions = actions or ["Back"]
    try:
        title = title_fn(shimmer_tick, width=width)
    except TypeError:
        title = title_fn(shimmer_tick)

    body: list = [
        Text(PAD + "Share this output online", style=f"bold {theme.accent_bright}"),
        Text(PAD + document_title, style=theme.value),
        Text(""),
        Text(
            PAD + "Anyone with the temporary URL and access code can view this output while this screen is open.",
            style=theme.warn,
        ),
        Text(""),
    ]
    if loading:
        tick = shimmer_tick or 0.0
        spinners = ("◐", "◓", "◑", "◒")
        spinner = spinners[int(tick * 5) % len(spinners)]
        dots = "." * (int(tick * 2.5) % 4)
        elapsed = int(tick)
        body.extend(
            [
                Text(
                    PAD + f"{spinner}  Establishing secure share{dots}",
                    style=f"bold {theme.accent_bright}",
                ),
                Text(PAD + f"   {status}", style=theme.muted),
                Text(PAD + f"   Elapsed: {elapsed}s  ·  Esc cancels", style=theme.dim),
            ]
        )
    else:
        body.append(Text(PAD + status, style=theme.muted))
    if public_url:
        body.extend(
            [
                Text(""),
                Text(PAD + "Public URL", style=f"bold {theme.accent}"),
                Text(PAD + public_url, style=theme.value, overflow="fold"),
                Text(""),
                Text(PAD + "Access code", style=f"bold {theme.accent}"),
                Text(PAD + join_code, style=f"bold {theme.accent_bright}"),
                Text(""),
                Text(PAD + "Copy Invite includes both values. Back or Stop Sharing closes the link.", style=theme.dim),
            ]
        )
    if message:
        body.extend([Text(""), Text(PAD + message, style=theme.good if message.startswith("Copied") else theme.warn)])

    # Keep the action bar pinned at the bottom while the status body gets the
    # remaining rows, matching every other shared screen.
    reserved = 18
    body_rows = max(4, height - reserved)
    visible = body[:body_rows]
    while len(visible) < body_rows:
        visible.append(Text(""))
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)
    return Panel(
        Group(
            Text(""),
            title,
            Text(""),
            Text(PAD + "Temporary, code-gated Cloudflare share", style=theme.muted),
            Text(""),
            *visible,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        ),
        height=height,
        padding=(1, 2),
        border_style=loading_border_color(shimmer_tick or 0.0) if loading else theme.sep,
    )


def run_output_share(
    console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    document: ShareDocument,
    theme: Theme,
    title_fn: Callable,
) -> None:
    """Start a code-gated server+tunnel and own them until the user leaves."""
    from yeaboi.sharing.tunnel import CloudflareTunnel, ensure_cloudflared

    state: dict[str, object] = {
        "status": "Preparing a protected local snapshot…",
        "public_url": "",
        "error": "",
        "done": False,
        "active": False,
        "server": None,
        "tunnel": None,
    }
    lock = threading.Lock()
    cancel = threading.Event()

    def _set(**values) -> None:
        with lock:
            state.update(values)

    def _worker() -> None:
        server: OutputShareServer | None = None
        tunnel: CloudflareTunnel | None = None
        try:
            server = OutputShareServer(document)
            server.start()
            _set(server=server, status="Setting up Cloudflare sharing (first use may download ~40 MB)…")
            binary = ensure_cloudflared()
            if binary is None:
                _set(error="Could not obtain cloudflared. The output was not published.", done=True)
                return
            if cancel.is_set():
                _set(done=True)
                return
            _set(status="Starting the secure tunnel and checking public DNS…")
            tunnel = CloudflareTunnel(server.port, binary=binary)
            _set(tunnel=tunnel)
            public_url = tunnel.start(timeout=45)
            if not public_url:
                _set(error="Cloudflare did not provide a reachable URL. See the logs for details.", done=True)
                return
            if cancel.is_set():
                tunnel.stop()
                _set(done=True)
                return
            _set(
                public_url=public_url.rstrip("/") + "/",
                status="Sharing is live.",
                active=True,
                done=True,
            )
            logger.info("output sharing ready (mode=%s, port=%d)", document.source_mode, server.port)
        except Exception as exc:
            logger.error("output sharing failed (mode=%s): %s", document.source_mode, exc, exc_info=True)
            _set(error="Could not start online sharing. See the logs for details.", done=True)

    logger.info("Share Online pressed (mode=%s)", document.source_mode)
    worker = threading.Thread(target=_worker, name="output-share-setup", daemon=True)
    worker.start()
    sel = 0
    message = ""
    started = time.monotonic()
    leaving = False

    try:
        while True:
            with lock:
                snapshot = dict(state)
            active = bool(snapshot["active"])
            error = str(snapshot["error"])
            done = bool(snapshot["done"])
            if active:
                actions = ["Copy Invite", "Stop Sharing", "Back"]
            else:
                actions = ["Back"]
                sel = 0
            status = error or str(snapshot["status"])
            if leaving and not done:
                status = "Cancelling setup and cleaning up…"

            w, h = console.size
            live.update(
                _build_output_share_screen(
                    title_fn=title_fn,
                    theme=theme,
                    document_title=document.title,
                    status=status,
                    public_url=str(snapshot["public_url"]),
                    join_code=(
                        snapshot["server"].display_code if snapshot.get("server") is not None and active else ""  # type: ignore[union-attr]
                    ),
                    message=message,
                    actions=actions,
                    action_sel=sel,
                    width=w,
                    height=max(16, h - 1),
                    shimmer_tick=time.monotonic() - started,
                    loading=not done,
                )
            )
            if leaving and done:
                break

            key = read_key(timeout=frame_time) if supports_timeout else read_key()
            if key == "left":
                sel = max(0, sel - 1)
            elif key == "right":
                sel = min(len(actions) - 1, sel + 1)
            elif key in ("esc", "q"):
                cancel.set()
                leaving = True
            elif key in ("enter", " "):
                action = actions[sel]
                if action == "Copy Invite" and active:
                    from yeaboi.clipboard import copy_text

                    server = snapshot["server"]
                    invite = f"{snapshot['public_url']}\nAccess code: {server.display_code}"  # type: ignore[union-attr]
                    message = "Copied invite to clipboard." if copy_text(invite) else "Couldn't copy — see logs."
                    logger.info("output sharing invite copy requested (mode=%s)", document.source_mode)
                elif action in ("Stop Sharing", "Back"):
                    cancel.set()
                    leaving = True
                    if done:
                        break
    finally:
        cancel.set()
        # Tear down already-published resources before waiting. A first-use
        # binary download is not cancellable, but the worker checks ``cancel``
        # before it can launch cloudflared afterwards.
        with lock:
            early_tunnel = state.get("tunnel")
            early_server = state.get("server")
        if early_tunnel is not None:
            early_tunnel.stop()  # type: ignore[union-attr]
        if early_server is not None:
            early_server.stop()  # type: ignore[union-attr]
        worker.join(timeout=50)
        with lock:
            tunnel = state.get("tunnel")
            server = state.get("server")
        if tunnel is not None:
            tunnel.stop()  # type: ignore[union-attr]
        if server is not None:
            server.stop()  # type: ignore[union-attr]
        logger.info("output sharing closed (mode=%s)", document.source_mode)
