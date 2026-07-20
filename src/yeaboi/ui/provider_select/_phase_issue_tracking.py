"""Issue tracking (Jira / Azure DevOps Boards) phase of the provider selection wizard.

# See docs: "Architecture" — this module handles the issue tracker
# multi-field form input with verification and animated feedback.
# Supports both Jira (Atlassian) and Azure DevOps Boards as tracker backends.
"""

from __future__ import annotations

import logging
import math
import sys
import termios
import time
import tty
from typing import Any

from rich.console import Console
from rich.live import Live

from yeaboi.ui.provider_select._config import _save_progress
from yeaboi.ui.provider_select._constants import _ISSUE_TRACKING_OPTIONS
from yeaboi.ui.provider_select._nav import StepNav, nav_for_key
from yeaboi.ui.provider_select._verification import _verify_azdevops, _verify_jira
from yeaboi.ui.provider_select.screens._screens_vc import _build_issue_tracking_screen
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS
from yeaboi.ui.shared._music_bar import make_live

logger = logging.getLogger(__name__)


def _run_issue_tracking(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    provider: dict[str, Any],
    api_key: str,
    vc: dict[str, Any],
    vc_token: str,
    *,
    live: Live | None = None,
    llm_model: str = "",
) -> dict[str, str] | StepNav | None:
    """Run the issue tracking phase with provider selection.

    First shows a provider picker (Jira / Azure DevOps Boards / Skip),
    then shows the appropriate credential form and verifies.

    If live is None, creates its own Live context (for debug skip).
    Otherwise uses the existing Live display.
    """
    import threading

    from yeaboi.ui.provider_select.screens._screens import _build_provider_row, _build_screen_frame

    # --- Step 1: Provider selection ---
    tracker_options = _ISSUE_TRACKING_OPTIONS
    # Provider-like dicts for ASCII art rendering (same pattern as LLM provider screen)
    _tracker_cards = [
        {"name": "Jira", "color": "rgb(70,100,180)"},
        {"name": "Azure DevOps", "color": "rgb(70,100,180)"},
        {"name": "Skip", "color": "rgb(70,100,180)"},
    ]
    tracker_selected = 0

    def _run_tracker_selection(_live: Live) -> int | StepNav | None:
        """Show tracker provider picker. Returns index, a StepNav (←/→/F), or None on Esc."""
        nonlocal tracker_selected
        from rich.align import Align
        from rich.text import Text

        def _render_tracker_menu():
            rows = []
            for i, card in enumerate(_tracker_cards):
                rows.append(_build_provider_row(card, selected=(i == tracker_selected)))

            body = [item for row in rows for item in (Align.center(row), Text(""))]
            if body:
                body = body[:-1]
            body_h = len(rows) * 3 - 1 if rows else 0
            w, h = console.size
            return _build_screen_frame(
                subtitle="Issue tracking · ↑↓ choose · Enter select · ←→ section · F finish",
                step=1,
                body_items=body,
                body_height=body_h,
                width=w,
                height=h,
            )

        # Drain stdin
        import select as _sel

        _drain_fd = sys.stdin.fileno()
        _drain_old = termios.tcgetattr(_drain_fd)
        try:
            tty.setcbreak(_drain_fd)
            while _sel.select([_drain_fd], [], [], 0.05)[0]:
                sys.stdin.read(1)
        finally:
            termios.tcsetattr(_drain_fd, termios.TCSADRAIN, _drain_old)

        _live.update(_render_tracker_menu())
        while True:
            key = read_key()
            # Section navigation (←/→ between chips, F to finish) takes priority
            # over the menu's own ↑/↓/Enter so the user can jump straight out.
            nav = nav_for_key(key, 1)
            if nav is not None:
                logger.info("issue tracking: section navigation (%r)", nav)
                return nav
            if key in ("up", "scroll_up"):
                tracker_selected = (tracker_selected - 1) % len(tracker_options)
            elif key in ("down", "scroll_down"):
                tracker_selected = (tracker_selected + 1) % len(tracker_options)
            elif key == "enter":
                logger.info("issue tracking: tracker chosen: %s", tracker_options[tracker_selected]["name"])
                return tracker_selected
            elif key == "esc":
                logger.info("issue tracking: user pressed Esc (back)")
                return None
            _live.update(_render_tracker_menu())

    def _run_form(_live: Live, tracker_idx: int) -> dict[str, str] | None:
        """Show the credential form for the chosen tracker and verify."""
        chosen = tracker_options[tracker_idx]
        if not chosen["fields"]:
            # "Skip" selected — return minimal result
            logger.info("issue tracking: skipped")
            _save_progress({})
            return {
                "name": provider["full_name"],
                "env_var": provider["env_var"],
                "provider_val": provider["provider_val"],
                "prefix": provider["prefix"],
                "instructions": provider["instructions"],
                "api_key": api_key,
                "llm_model": llm_model,
                "vc_env_var": vc["env_var"],
                "vc_token": vc_token,
                "issue_tracking": {},
            }

        fields = chosen["fields"]
        tracker_name = chosen["name"]
        is_azdevops = tracker_name == "Azure DevOps Boards"

        it_selected = 0
        it_n = len(fields)
        _cfg = existing_config or {}
        it_values: dict[int, str] = {}
        for i, field in enumerate(fields):
            it_values[i] = _cfg.get(field["env_var"], "")
        it_errors: dict[int, str] = {}
        it_verified: dict[int, bool] = {}

        # Drain stdin
        import select as _sel

        _drain_fd = sys.stdin.fileno()
        _drain_old = termios.tcgetattr(_drain_fd)
        try:
            tty.setcbreak(_drain_fd)
            while _sel.select([_drain_fd], [], [], 0.05)[0]:
                sys.stdin.read(1)
        finally:
            termios.tcsetattr(_drain_fd, termios.TCSADRAIN, _drain_old)

        w, h = console.size
        _live.update(
            _build_issue_tracking_screen(
                it_selected,
                it_values,
                width=w,
                height=h,
                fields=fields,
                subtitle="Issue tracking",
                title_text=tracker_name,
            )
        )

        while True:
            key = read_key()

            if key in ("up", "scroll_up"):
                it_selected = (it_selected - 1) % it_n
            elif key in ("down", "scroll_down"):
                it_selected = (it_selected + 1) % it_n
            elif key == "enter":
                missing = False
                for i, field in enumerate(fields):
                    if field["required"] and not it_values.get(i, "").strip():
                        it_errors[i] = f"{field['label']} is required"
                        if not missing:
                            it_selected = i
                        missing = True

                if missing:
                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            errors=it_errors,
                            fields=fields,
                            subtitle="Issue tracking",
                            title_text=tracker_name,
                        )
                    )
                    continue

                # Verify credentials
                verify_result: list[tuple[bool, str]] = []

                if is_azdevops:
                    org_url = it_values[0].strip()
                    project_name = it_values[1].strip()
                    pat = it_values[2].strip()

                    def _do_verify():
                        verify_result.append(_verify_azdevops(org_url, project_name, pat))
                else:
                    jira_url = it_values[0].strip()
                    jira_email = it_values[1].strip()
                    jira_token_val = it_values[2].strip()

                    def _do_verify():
                        verify_result.append(_verify_jira(jira_url, jira_email, jira_token_val))

                logger.info("issue tracking: verifying %s credentials", tracker_name)
                thread = threading.Thread(target=_do_verify, daemon=True)
                thread.start()

                pulse_start = time.monotonic()
                while thread.is_alive():
                    elapsed = time.monotonic() - pulse_start
                    intensity = (math.sin(elapsed * 6) + 1) / 2
                    v = int(60 + 140 * intensity)
                    bo = {i: f"rgb({v},{v},{v})" for i in range(it_n)}
                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            border_overrides=bo,
                            fields=fields,
                            subtitle="Issue tracking",
                            title_text=tracker_name,
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)

                thread.join()
                ok, msg = verify_result[0]
                if ok:
                    logger.info("issue tracking: %s credentials verified", tracker_name)
                else:
                    logger.warning("issue tracking: %s verification failed — %s", tracker_name, msg)

                if ok:
                    green_r, green_g, green_b = 80, 220, 120
                    for frame in range(10):
                        t = frame / 9
                        intensity = math.sin(t * math.pi)
                        r = int(green_r + (255 - green_r) * intensity)
                        g = int(green_g + (255 - green_g) * intensity)
                        b = int(green_b + (255 - green_b) * intensity)
                        bo = {i: f"rgb({r},{g},{b})" for i in range(it_n)}
                        w, h = console.size
                        _live.update(
                            _build_issue_tracking_screen(
                                it_selected,
                                it_values,
                                width=w,
                                height=h,
                                verified={i: True for i in range(it_n)},
                                border_overrides=bo,
                                fields=fields,
                                subtitle="Issue tracking",
                                title_text=tracker_name,
                            )
                        )
                        time.sleep(FRAME_TIME_30FPS)

                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            verified={i: True for i in range(it_n)},
                            fields=fields,
                            subtitle="Issue tracking",
                            title_text=tracker_name,
                        )
                    )
                    time.sleep(0.6)

                    issue_data = {}
                    for i, field in enumerate(fields):
                        val = it_values.get(i, "").strip()
                        if val:
                            issue_data[field["env_var"]] = val

                    _save_progress(issue_data)
                    logger.info("issue tracking: %s configured (%d keys)", tracker_name, len(issue_data))
                    return {
                        "name": provider["full_name"],
                        "env_var": provider["env_var"],
                        "provider_val": provider["provider_val"],
                        "prefix": provider["prefix"],
                        "instructions": provider["instructions"],
                        "api_key": api_key,
                        "llm_model": llm_model,
                        "vc_env_var": vc["env_var"],
                        "vc_token": vc_token,
                        "issue_tracking": issue_data,
                    }
                else:
                    # Point error at the token/PAT field (index 2 for both Jira and AzDO)
                    it_errors[2] = msg
                    it_selected = 2
                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            errors=it_errors,
                            fields=fields,
                            subtitle="Issue tracking",
                            title_text=tracker_name,
                        )
                    )
                    continue

            elif key == "esc":
                logger.info("issue tracking: form cancelled (Esc)")
                return None
            elif key == "clear":
                it_values[it_selected] = ""
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)
            elif key == "backspace":
                it_values[it_selected] = it_values[it_selected][:-1]
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)
            elif key == "tab":
                it_selected = (it_selected + 1) % it_n
            elif key.startswith("paste:"):
                it_values[it_selected] = it_values.get(it_selected, "") + key[6:]
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)
            elif key == "ctrl+v":
                # Credential fields never reach an LLM — reject image paste with a notice.
                from yeaboi.ui.shared._attachments import UNSUPPORTED_MESSAGE

                it_errors[it_selected] = UNSUPPORTED_MESSAGE
            elif len(key) == 1 and key.isprintable():
                it_values[it_selected] = it_values.get(it_selected, "") + key
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)

            w, h = console.size
            _live.update(
                _build_issue_tracking_screen(
                    it_selected,
                    it_values,
                    width=w,
                    height=h,
                    errors=it_errors,
                    verified=it_verified,
                    fields=fields,
                    subtitle="Issue tracking",
                    title_text=tracker_name,
                )
            )

    def _run_full(_live: Live) -> dict[str, str] | StepNav | None:
        tracker_idx = _run_tracker_selection(_live)
        if isinstance(tracker_idx, StepNav):
            return tracker_idx
        if tracker_idx is None:
            return None
        return _run_form(_live, tracker_idx)

    if live is not None:
        return _run_full(live)
    else:
        w, h = console.size
        # Use a placeholder screen for the Live context
        from rich.text import Text

        with make_live(
            Text(""),
            console=console,
            refresh_per_second=30,
            screen=True,
        ) as new_live:
            return _run_full(new_live)
