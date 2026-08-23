"""The one gate every mode passes through: is the LLM actually reachable.

A missing API key is caught cheaply today (``config.is_llm_configured()``), but
an expired or revoked one is not — it looks "configured" right up until the
first real call fails, and by then a mode has already fallen back to
deterministic, non-AI content (e.g. retro's "Address: <topic>" action items)
with nothing louder than a status line to say so. That is the bug this closes:
a live credential check, shown as a blocking screen the user must act on,
*before* a mode opens rather than after it has already produced fake output.

Modelled on :mod:`yeaboi.ui.shared._beta_notice`: one modal run-loop that takes
the caller's Live/console/read_key, so it composes with any frame-timed page
loop. Unlike the beta notice, nothing here is ever acknowledged-once-and-done —
a broken key is a transient problem, not a standing preference, so this checks
live and asks again every single time it is still broken. There is exactly one
call site (``ui.mode_select``, right before any mode is entered), so a mode
added later inherits this automatically instead of needing its author to
remember a per-mode call, the same gap that left the beta notice covering only
two of a dozen modes.
"""

from __future__ import annotations

import logging
import time

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.auth_state import CredentialStatus, check_llm_credentials
from yeaboi.ui.shared._click import button_click, parse_click
from yeaboi.ui.shared._components import (
    PAD,
    Theme,
    build_action_buttons,
    build_ascii_title,
    build_page_panel,
)

logger = logging.getLogger(__name__)

_ACTIONS = ["Continue anyway", "Back"]

# Rose/alert accent — already registered in COLOR_RGB (it's Agent Security's
# accent) so the shimmer path works without a new registration. Not reusing
# AGENT_SECURITY_THEME itself: this screen isn't that mode's, it just wants the
# same "something's wrong" hue. A standalone Theme, like CHANGELOG_THEME /
# FEEDBACK_THEME, for a page that doesn't belong to one tinted mode.
_LLM_GATE_THEME = Theme(accent="rgb(230,90,120)", accent_bright="rgb(255,130,160)")
_LLM_GATE_COLOR = "rgb(230,90,120)"


def _build_checking_screen(*, provider_label: str, width: int = 80, height: int = 24) -> Panel:
    """A brief "checking your key" frame while the live ping is in flight."""
    theme = _LLM_GATE_THEME
    title = build_ascii_title("Checking", _LLM_GATE_COLOR, width=width)
    lines: list = [
        Text(""),
        title,
        Text(""),
        Text(PAD + f"Verifying your {provider_label} credentials before this mode opens…", style=theme.desc),
        Text(""),
    ]
    return build_page_panel(Group(*lines), theme=theme, border_style=theme.sep, height=height)


def _build_llm_gate_screen(
    status: CredentialStatus,
    *,
    action_sel: int = 0,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Render the blocking "your key looks broken" page."""
    theme = _LLM_GATE_THEME
    title = build_ascii_title("Warning", _LLM_GATE_COLOR, width=width)

    headline = (
        f"No {status.provider_label} API key is configured."
        if not status.configured
        else f"Your {status.provider_label} API key looks invalid."
    )

    lines: list = [Text(""), title, Text("")]
    lines.append(Text(PAD + headline, style="bold white", justify="left"))
    lines.append(Text(""))
    if status.reason:
        lines.append(Text(PAD + status.reason, style=theme.desc, justify="left"))
        lines.append(Text(""))
    lines.append(
        Text(
            PAD + "Continuing will run without AI — you'll get deterministic placeholder content",
            style=theme.desc,
        )
    )
    lines.append(
        Text(PAD + '(e.g. "Address: <topic>") instead of a written analysis, not a broken app.', style=theme.desc)
    )
    lines.append(Text(""))
    lines.append(
        Text(PAD + "Fix it from Settings, or continue anyway and it'll ask again next time.", style=theme.muted)
    )
    lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(_ACTIONS, action_sel)
    lines += [btn_top, btn_mid, btn_bot]

    return build_page_panel(Group(*lines), theme=theme, border_style=theme.sep, height=height)


def _check_with_spinner(live, console, frame_time: float) -> CredentialStatus:
    """Run the live credential check off the render thread so the UI stays live.

    The ping can take up to several seconds (a real network round trip); a
    plain blocking call here would freeze input handling for that whole window.
    """
    from yeaboi.auth_state import provider_label as _provider_label
    from yeaboi.ui.shared._music_bar import duck_working_thread

    label = _provider_label()
    result_box: list = [None]

    def _work() -> None:
        result_box[0] = check_llm_credentials()

    thread = duck_working_thread(_work, name="llm-gate-probe")
    thread.start()
    while thread.is_alive():
        w, h = console.size
        live.update(_build_checking_screen(provider_label=label, width=w, height=h))
        time.sleep(frame_time)
    thread.join()
    return result_box[0]


def show_llm_gate(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    *,
    check=None,
) -> bool:
    """Live-check LLM credentials before a mode starts. True means proceed.

    ``check`` is an injection seam for tests (a callable returning
    ``CredentialStatus``); real callers should omit it and get the live probe.
    On an OK status this renders nothing at all and returns immediately — the
    common case stays exactly as fast and invisible as before this gate existed.
    """
    status = (check or (lambda: _check_with_spinner(live, console, frame_time)))()
    if status.ok:
        return True

    logger.warning("LLM gate blocked mode entry: provider=%s reason=%s", status.provider_label, status.reason)
    sel = 0
    while True:
        w, h = console.size
        panel = _build_llm_gate_screen(status, action_sel=sel, width=w, height=h)
        live.update(panel)
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        if not k:
            continue

        clicked = parse_click(k)
        if clicked is not None:
            idx = button_click(console, panel, clicked[0], clicked[1], _ACTIONS)
            if idx is None:
                continue
            sel = idx
            k = "enter"

        if k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(len(_ACTIONS) - 1, sel + 1)
        elif k in ("enter", " "):
            if sel == 0:
                logger.info("LLM gate acknowledged (continuing anyway) for %s", status.provider_label)
                return True
            logger.info("LLM gate declined for %s — returning to menu", status.provider_label)
            return False
        elif k in ("esc", "q"):
            logger.info("LLM gate dismissed (esc) for %s — returning to menu", status.provider_label)
            return False
