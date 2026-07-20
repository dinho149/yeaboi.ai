"""Notion (Docs) phase of the provider selection wizard.

# See docs: "Architecture" — this module handles the standalone Notion
# credential step. Unlike Confluence (which rides on Jira's Atlassian auth in the
# Issue Tracking form), Notion is an independent doc tool with its own integration
# token, so it gets its own optional wizard step.
#
# It reuses the generic multi-field form renderer (_build_issue_tracking_screen,
# which accepts `fields=` and `subtitle=`) and the same pulsing verify/confirm
# animation as the Jira/AzDO form — just with _NOTION_FIELDS and a live
# _verify_notion check.
"""

from __future__ import annotations

import logging
import math
import sys
import termios
import time
import tty

from rich.console import Console
from rich.live import Live

from yeaboi.ui.provider_select._config import _save_progress
from yeaboi.ui.provider_select._constants import _NOTION_FIELDS
from yeaboi.ui.provider_select._nav import StepNav, nav_for_key
from yeaboi.ui.provider_select._verification import _verify_notion
from yeaboi.ui.provider_select.screens._screens import (
    _ACCENT,
    _build_provider_row,
    _build_screen_frame,
)
from yeaboi.ui.provider_select.screens._screens_vc import _build_issue_tracking_screen
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS

logger = logging.getLogger(__name__)

_TITLE = "Notion"
_SUBTITLE = "Docs"


def _drain() -> None:
    """Drain buffered stdin so a held key from the previous screen doesn't leak in."""
    import select as _sel

    _drain_fd = sys.stdin.fileno()
    _drain_old = termios.tcgetattr(_drain_fd)
    try:
        tty.setcbreak(_drain_fd)
        while _sel.select([_drain_fd], [], [], 0.05)[0]:
            sys.stdin.read(1)
    finally:
        termios.tcsetattr(_drain_fd, termios.TCSADRAIN, _drain_old)


def _run_notion_selection(console: Console, read_key, live: Live) -> str | StepNav | None:
    """Show the Notion / Skip picker. Returns "notion"/"skip", a StepNav (←/→/F), or None (Esc).

    Kept for backward compatibility; the unified Docs picker (_phase_docs._run_docs)
    now owns provider selection and calls _run_notion_form directly.
    """
    from rich.align import Align
    from rich.text import Text

    cards = [{"name": "Notion", "color": _ACCENT}, {"name": "Skip", "color": _ACCENT}]
    pick = 0

    def _render_menu() -> None:
        rows = [_build_provider_row(c, selected=(i == pick)) for i, c in enumerate(cards)]
        body = [item for row in rows for item in (Align.center(row), Text(""))]
        if body:
            body = body[:-1]
        body_h = len(rows) * 3 - 1 if rows else 0
        w, h = console.size
        live.update(
            _build_screen_frame(
                subtitle="Docs · ↑↓ choose · Enter select · ←→ section · F finish",
                step=2,
                body_items=body,
                body_height=body_h,
                width=w,
                height=h,
                title_text=_TITLE,
            )
        )

    _drain()
    _render_menu()
    while True:
        key = read_key()
        # Section navigation (←/→ between chips, F to finish) short-circuits
        # the picker so the user can leave the Docs step without choosing.
        nav = nav_for_key(key, 2)
        if nav is not None:
            return nav
        if key in ("up", "scroll_up"):
            pick = (pick - 1) % len(cards)
        elif key in ("down", "scroll_down"):
            pick = (pick + 1) % len(cards)
        elif key == "enter":
            return "notion" if pick == 0 else "skip"
        elif key == "esc":
            return None
        _render_menu()


def _run_notion(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    live: Live,
) -> dict[str, str] | StepNav | None:
    """Show the optional Notion / Skip picker then the credential form.

    Backward-compatible standalone entry point. Returns a dict of collected Notion
    env vars, an empty dict when the user picks Skip (or submits an empty token),
    a StepNav for section navigation, or None when the user presses Esc to go back.
    """
    choice = _run_notion_selection(console, read_key, live)
    if isinstance(choice, StepNav):
        logger.info("Notion sub-step: section navigation (%r)", choice)
        return choice
    if choice is None:
        logger.info("Notion sub-step: user pressed Esc (back)")
        return None
    if choice == "skip":
        logger.info("Notion sub-step: skipped")
        return {}
    return _run_notion_form(console, read_key, existing_config, live)


def _run_notion_form(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    live: Live,
) -> dict[str, str] | None:
    """Render the Notion credential form and verify the token.

    The Notion/Skip choice is the caller's responsibility (the unified Docs picker or
    _run_notion). Returns a dict of collected Notion env vars, an empty dict when the
    token is left blank (optional), or None when the user presses Esc to go back to
    the picker. Notion is optional — the step never blocks users who don't use it.
    """
    import threading

    logger.info("Entering Notion setup form")
    fields = _NOTION_FIELDS
    n = len(fields)
    _cfg = existing_config or {}
    values: dict[int, str] = {i: _cfg.get(field["env_var"], "") for i, field in enumerate(fields)}
    errors: dict[int, str] = {}
    verified: dict[int, bool] = {}
    selected = 0

    _drain()

    def _render(**kw) -> None:
        w, h = console.size
        live.update(
            _build_issue_tracking_screen(
                selected,
                values,
                width=w,
                height=h,
                fields=fields,
                subtitle=_SUBTITLE,
                title_text=_TITLE,
                step=2,  # Docs chip (_STEPS[2]); shared form defaults to Issue Tracking
                **kw,
            )
        )

    _render()

    while True:
        key = read_key()

        if key in ("up", "scroll_up"):
            selected = (selected - 1) % n
        elif key in ("down", "scroll_down"):
            selected = (selected + 1) % n
        elif key == "enter":
            token = values.get(0, "").strip()

            # Empty token → skip Notion entirely (it's optional).
            if not token:
                logger.info("Notion form: empty token, skipping")
                return {}

            # Verify the token with a pulsing border while the API call runs.
            verify_result: list[tuple[bool, str]] = []

            def _do_verify():
                verify_result.append(_verify_notion(token))

            logger.info("Notion form: verifying integration token")
            thread = threading.Thread(target=_do_verify, daemon=True)
            thread.start()

            pulse_start = time.monotonic()
            while thread.is_alive():
                elapsed = time.monotonic() - pulse_start
                intensity = (math.sin(elapsed * 6) + 1) / 2
                v = int(60 + 140 * intensity)
                _render(border_overrides={i: f"rgb({v},{v},{v})" for i in range(n)})
                time.sleep(FRAME_TIME_30FPS)

            thread.join()
            ok, msg = verify_result[0]

            if ok:
                logger.info("Notion form: token verified")
                # Green success flash, matching the Jira/VC verify animation.
                green_r, green_g, green_b = 80, 220, 120
                for frame in range(10):
                    t = frame / 9
                    intensity = math.sin(t * math.pi)
                    r = int(green_r + (255 - green_r) * intensity)
                    g = int(green_g + (255 - green_g) * intensity)
                    b = int(green_b + (255 - green_b) * intensity)
                    _render(
                        verified={i: True for i in range(n)},
                        border_overrides={i: f"rgb({r},{g},{b})" for i in range(n)},
                    )
                    time.sleep(FRAME_TIME_30FPS)
                _render(verified={i: True for i in range(n)})
                time.sleep(0.6)

                notion_data = {}
                for i, field in enumerate(fields):
                    val = values.get(i, "").strip()
                    if val:
                        notion_data[field["env_var"]] = val
                _save_progress(notion_data)
                logger.info("Notion form: saved (%d keys)", len(notion_data))
                return notion_data

            # Verification failed — surface the error on the token field.
            logger.warning("Notion form: verification failed — %s", msg)
            errors[0] = msg
            selected = 0
            _render(errors=errors)
            continue

        elif key == "esc":
            logger.info("Notion form: user pressed Esc (back)")
            return None
        elif key == "clear":
            values[selected] = ""
            errors.pop(selected, None)
            verified.pop(selected, None)
        elif key == "backspace":
            values[selected] = values[selected][:-1]
            errors.pop(selected, None)
            verified.pop(selected, None)
        elif key == "tab":
            selected = (selected + 1) % n
        elif key.startswith("paste:"):
            values[selected] = values.get(selected, "") + key[6:]
            errors.pop(selected, None)
            verified.pop(selected, None)
        elif key == "ctrl+v":
            # Credential fields never reach an LLM — reject image paste with a notice.
            from yeaboi.ui.shared._attachments import UNSUPPORTED_MESSAGE

            errors[selected] = UNSUPPORTED_MESSAGE
        elif len(key) == 1 and key.isprintable():
            values[selected] = values.get(selected, "") + key
            errors.pop(selected, None)
            verified.pop(selected, None)

        _render(errors=errors, verified=verified)
