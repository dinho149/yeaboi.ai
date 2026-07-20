"""Confluence (Docs) phase of the provider selection wizard.

# See docs: "Architecture" — this module handles the Confluence sub-step of the
# Docs wizard step. Unlike Notion (an independent doc tool with its own token),
# Confluence rides on Jira's Atlassian auth (JIRA_BASE_URL/EMAIL/API_TOKEN — see
# tools/confluence.py); the only extra credential is the space key. The wizard
# therefore only runs this phase when Jira was configured in the Issue Tracking
# step and passes those creds in for live verification.
#
# It reuses the generic multi-field form renderer (_build_issue_tracking_screen)
# and the same pulsing verify/confirm animation as the Notion/Jira forms — just
# with _CONFLUENCE_FIELDS and a live _verify_confluence check.
"""

from __future__ import annotations

import logging
import math
import time

from rich.console import Console
from rich.live import Live

from yeaboi.ui.provider_select._config import _save_progress
from yeaboi.ui.provider_select._constants import _CONFLUENCE_FIELDS, _CONFLUENCE_STANDALONE_FIELDS
from yeaboi.ui.provider_select._nav import StepNav, nav_for_key
from yeaboi.ui.provider_select._phase_notion import _drain
from yeaboi.ui.provider_select._verification import _verify_confluence, _verify_jira
from yeaboi.ui.provider_select.screens._screens import (
    _ACCENT,
    _build_provider_row,
    _build_screen_frame,
)
from yeaboi.ui.provider_select.screens._screens_vc import _build_issue_tracking_screen
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS

logger = logging.getLogger(__name__)

_TITLE = "Confluence"
_SUBTITLE = "Docs"
_DOCS_STEP = 2  # _STEPS[2] == "Docs"


def _has_jira_creds(jira_creds: dict[str, str] | None) -> bool:
    """True when the shared Atlassian creds from the Issue Tracking step are complete."""
    jira_creds = jira_creds or {}
    return all(jira_creds.get(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))


def _run_confluence_selection(console: Console, read_key, live: Live) -> str | StepNav | None:
    """Show the Confluence / Skip picker. Returns "confluence"/"skip", a StepNav (←/→/F), or None (Esc).

    Kept for backward compatibility; the unified Docs picker (_phase_docs._run_docs)
    now owns provider selection and calls _run_confluence_form directly.
    """
    from rich.align import Align
    from rich.text import Text

    cards = [{"name": "Confluence", "color": _ACCENT}, {"name": "Skip", "color": _ACCENT}]
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
                step=_DOCS_STEP,
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
        nav = nav_for_key(key, _DOCS_STEP)
        if nav is not None:
            return nav
        if key in ("up", "scroll_up"):
            pick = (pick - 1) % len(cards)
        elif key in ("down", "scroll_down"):
            pick = (pick + 1) % len(cards)
        elif key == "enter":
            return "confluence" if pick == 0 else "skip"
        elif key == "esc":
            return None
        _render_menu()


def _run_confluence(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    live: Live,
    *,
    jira_creds: dict[str, str],
) -> dict[str, str] | StepNav | None:
    """Show the optional Confluence / Skip picker then the credential form.

    Backward-compatible standalone entry point. Returns a dict of collected Confluence
    env vars, an empty dict when the user picks Skip, a StepNav for section navigation,
    or None when the user presses Esc to go back.
    """
    choice = _run_confluence_selection(console, read_key, live)
    if isinstance(choice, StepNav):
        logger.info("Confluence sub-step: section navigation (%r)", choice)
        return choice
    if choice is None:
        logger.info("Confluence sub-step: user pressed Esc (back)")
        return None
    if choice == "skip":
        logger.info("Confluence sub-step: skipped")
        return {}
    return _run_confluence_form(console, read_key, existing_config, live, jira_creds=jira_creds)


def _run_confluence_form(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    live: Live,
    *,
    jira_creds: dict[str, str] | None,
) -> dict[str, str] | None:
    """Render the Confluence credential form and verify it.

    The Confluence/Skip choice is the caller's responsibility (the unified Docs picker
    or _run_confluence). Two modes:

    * **Reuse** — when ``jira_creds`` carries the shared Atlassian creds
      (JIRA_BASE_URL/EMAIL/API_TOKEN) from the Issue Tracking step, only the space key
      is asked for and those creds verify the space.
    * **Standalone** — when Jira wasn't configured, the full Atlassian login
      (CONFLUENCE_BASE_URL/EMAIL/API_TOKEN + optional space key) is collected inline so
      Confluence works on its own (see config.get_confluence_base_url).

    Returns a dict of collected env vars, an empty dict when the user submits nothing
    (optional), or None on Esc (back to the picker).
    """
    import threading

    logger.info("Entering Confluence setup form")

    reuse = _has_jira_creds(jira_creds)
    fields = _CONFLUENCE_FIELDS if reuse else _CONFLUENCE_STANDALONE_FIELDS
    n = len(fields)
    _cfg = existing_config or {}
    values: dict[int, str] = {i: _cfg.get(field["env_var"], "") for i, field in enumerate(fields)}
    errors: dict[int, str] = {}
    verified: dict[int, bool] = {}
    selected = 0

    # env_var → field index, so we read submitted values by name regardless of mode.
    idx_of = {field["env_var"]: i for i, field in enumerate(fields)}

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
                step=_DOCS_STEP,
                **kw,
            )
        )

    def _val(env_var: str) -> str:
        i = idx_of.get(env_var)
        return values.get(i, "").strip() if i is not None else ""

    _render()

    while True:
        key = read_key()

        if key in ("up", "scroll_up"):
            selected = (selected - 1) % n
        elif key in ("down", "scroll_down"):
            selected = (selected + 1) % n
        elif key == "enter":
            space_key = _val("CONFLUENCE_SPACE_KEY")

            if reuse:
                base_url = (jira_creds or {}).get("JIRA_BASE_URL", "")
                email = (jira_creds or {}).get("JIRA_EMAIL", "")
                token = (jira_creds or {}).get("JIRA_API_TOKEN", "")
                # Reuse mode: empty space key → skip Confluence entirely (optional).
                if not space_key:
                    logger.info("Confluence form: empty space key, skipping")
                    return {}
            else:
                base_url = _val("CONFLUENCE_BASE_URL")
                email = _val("CONFLUENCE_EMAIL")
                token = _val("CONFLUENCE_API_TOKEN")
                # Standalone: nothing entered at all → skip (optional). A partial login
                # is an error — surface it on the first blank required field.
                if not any([base_url, email, token, space_key]):
                    logger.info("Confluence form: nothing entered, skipping")
                    return {}
                missing = next(
                    (i for i, f in enumerate(fields) if f.get("required") and not values.get(i, "").strip()),
                    None,
                )
                if missing is not None:
                    errors[missing] = "Required"
                    selected = missing
                    _render(errors=errors)
                    continue

            # Verify with a pulsing border while the API call runs. With a space key we
            # check the space itself; without one (standalone) we validate the Atlassian
            # login via the shared identity endpoint (_verify_jira — same account).
            verify_result: list[tuple[bool, str]] = []

            def _do_verify():
                if space_key:
                    verify_result.append(_verify_confluence(base_url, email, token, space_key))
                else:
                    verify_result.append(_verify_jira(base_url, email, token))

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
                # Green success flash, matching the Notion/Jira verify animation.
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

                confluence_data = {}
                for i, field in enumerate(fields):
                    val = values.get(i, "").strip()
                    if val:
                        confluence_data[field["env_var"]] = val
                _save_progress(confluence_data)
                logger.info("Confluence form: saved (%s mode)", "reuse" if reuse else "standalone")
                return confluence_data

            # Verification failed — surface the error on the space-key field (reuse) or
            # the API-token field (standalone, the most likely culprit).
            err_field = idx_of.get("CONFLUENCE_SPACE_KEY" if reuse else "CONFLUENCE_API_TOKEN", 0)
            errors[err_field] = msg
            selected = err_field
            _render(errors=errors)
            continue

        elif key == "esc":
            logger.info("Confluence form: user pressed Esc (back)")
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
