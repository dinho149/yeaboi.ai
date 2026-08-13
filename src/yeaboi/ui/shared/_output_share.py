"""Shared TUI flow for temporarily publishing one generated HTML artifact."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.sharing.editable import EditableShare
from yeaboi.sharing.server import OutputShareServer, ShareDocument
from yeaboi.ui.shared._animations import loading_border_color
from yeaboi.ui.shared._click import button_click, parse_click
from yeaboi.ui.shared._components import PAD, Theme, build_action_buttons, build_page_panel

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
                Text(
                    PAD + "Copy Invite sends one link that carries the code. Back or Stop Sharing closes it.",
                    style=theme.dim,
                ),
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
    return build_page_panel(
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
        theme=theme,
        border_style=loading_border_color(shimmer_tick or 0.0) if loading else theme.sep,
        height=height,
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
    editable: EditableShare | None = None,
    on_edit: Callable | None = None,
) -> int:
    """Start a code-gated server+tunnel and own them until the user leaves.

    With ``editable`` the shared document is correctable: teammates who join can
    fix what the run got wrong, and this screen shows a live count of what they
    have changed. Returns how many corrections were recorded **in this session**,
    so the caller can decide whether to commit them — this function deliberately
    does not, because its teardown also runs on Esc, on Back and on any
    exception, and a path that rewrites the host's stored report from a crash
    handler is not one anybody asked for.
    """
    from yeaboi.sharing.tunnel import CloudflareTunnel, ensure_cloudflared

    # Read before anything can join: a reopened share has already replayed every
    # correction on record, and those are not news.
    already_recorded = len(editable.document.edits()) if editable is not None else 0

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
            server = OutputShareServer(document, editable=editable, on_edit=on_edit)
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

            def _on_expired() -> None:
                # Runs on the tunnel's timer thread once TUNNEL_TIMEOUT_MINUTES
                # elapses. There is no retry affordance on this screen — collapse
                # to the same "Back only" terminal state as any other setup
                # failure (e.g. the ensure_cloudflared() failure above), rather
                # than inventing a new control.
                with lock:
                    srv = state.get("server")
                if srv is not None:
                    srv.set_public_url("")  # type: ignore[union-attr]
                _set(active=False, done=True, public_url="", error="Secure link expired after the configured timeout.")
                logger.info("output sharing expired (mode=%s)", document.source_mode)

            tunnel = CloudflareTunnel(server.port, binary=binary, on_expire=_on_expired)
            _set(tunnel=tunnel)
            public_url = tunnel.start(timeout=45)
            if not public_url:
                _set(error="Cloudflare did not provide a reachable URL. See the logs for details.", done=True)
                return
            if cancel.is_set():
                tunnel.stop()
                _set(done=True)
                return
            server.set_public_url(public_url.rstrip("/") + "/")
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
                if editable is not None and editable.document.edits():
                    actions.insert(1, "Discard Edits")
            else:
                actions = ["Back"]
                sel = 0
            status = error or str(snapshot["status"])
            if editable is not None and active:
                count = len(editable.document.edits())
                if count:
                    who = editable.document.editors()
                    people = f" by {len(who)} " + ("person" if len(who) == 1 else "people")
                    status = f"Sharing is live — {count} " + ("edit" if count == 1 else "edits") + people + "."
            if leaving and not done:
                status = "Cancelling setup and cleaning up…"

            w, h = console.size
            panel = _build_output_share_screen(
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
            live.update(panel)
            if leaving and done:
                break

            key = read_key(timeout=frame_time) if supports_timeout else read_key()
            # Click a button → select it and act, exactly like arrow-to + Enter.
            clicked = parse_click(key)
            if clicked is not None:
                idx = button_click(console, panel, clicked[0], clicked[1], actions)
                if idx is None:
                    continue  # clicked off the button row — ignore
                sel = idx
                key = "enter"
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
                    from yeaboi.sharing.access import invite_url

                    server = snapshot["server"]
                    # One link with the code in the fragment. A URL and a sentence
                    # in one clipboard payload is what used to break here — see
                    # invite_url. `active` already means the tunnel is up, so this
                    # is non-empty, but the helper's empty case is the same "not
                    # ready" answer the button is gated on anyway.
                    invite = invite_url(str(snapshot["public_url"]), server.display_code)  # type: ignore[union-attr]
                    message = "Copied invite to clipboard." if copy_text(invite) else "Couldn't copy — see logs."
                    logger.info("output sharing invite copy requested (mode=%s)", document.source_mode)
                elif action == "Discard Edits" and editable is not None:
                    dropped = 0
                    while editable.document.drop_last() is not None:
                        dropped += 1
                    # Precise about what just happened: the document is back to
                    # what the run produced, but the log is append-only and
                    # still holds every one of them, which is what the edit
                    # history and the next run's context will keep reading.
                    message = (
                        f"Removed {dropped} " + ("correction" if dropped == 1 else "corrections") + " from this "
                        "document — the edit history still shows them."
                    )
                    logger.info("output sharing edits discarded (mode=%s)", document.source_mode)
                    sel = 0
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
        # The *delta*, not the total. A session that reopens a previously
        # corrected document replays its whole log before anyone joins, so the
        # total is non-zero the moment the screen opens — and the caller commits
        # a fresh `origin='edited'` row whenever this is non-zero. Returning the
        # total meant opening the share and pressing Back immediately reported
        # "Saved 1 correction." and appended a duplicate row, once per cycle.
        recorded = (len(editable.document.edits()) - already_recorded) if editable is not None else 0
        logger.info("output sharing closed (mode=%s, new edits=%d)", document.source_mode, recorded)
    return recorded
