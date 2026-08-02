"""Filesystem-sandbox consent popup — Allow once / Always allow / Deny.

# See docs: "Guardrails" — human-in-the-loop; this is the TUI consent surface
# for the filesystem sandbox (src/yeaboi/fs_policy.py).

Two entry styles share the same popup:

- Pre-flight (main thread): a screen is about to read a user-typed path —
  call :func:`_preflight_path_consent` BEFORE touching the file, so the user
  consents up front and the feature proceeds (or aborts) cleanly.
- Post-turn (planning session): agent tools run on a worker thread where a
  blocking terminal prompt would deadlock the Live display, so fs_policy
  queues denials instead; the session's main loop pops them between turns
  and shows this popup (see ui/session/phases/_phases.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Selection order mirrors the button row: Allow once / Always allow / Deny.
_CHOICES = ("allow_once", "allow_always", "deny")
_LABELS = ["Allow once", "Always allow", "Deny"]
_HINTS = (
    "Allow for this run only — forgotten when yeaboi exits",
    "Add to the whitelist (YEABOI_ALLOWED_PATHS in ~/.yeaboi/.env)",
    "Refuse — yeaboi will not touch this path",
)


def _fs_consent_popup(console, live, read_key, frame_time, supports_timeout, req) -> str:
    """Show the sandbox consent popup for one ConsentRequest.

    Returns "allow_once" | "allow_always" | "deny". Left/Right/Tab move,
    Enter/Space select, Esc/q = deny. Modeled on _confirm_move_data
    (ui/mode_select) — same console/live/read_key plumbing and Panel styling.
    """
    from rich.align import Align
    from rich.console import Group
    from rich.text import Text

    from yeaboi.ui.shared._components import (
        PAD,
        SETTINGS_THEME,
        build_action_buttons,
        build_page_panel,
        build_popup,
        settings_title,
    )

    verb = "write to" if req.mode == "write" else "read from"
    feature = req.context or "A feature"
    logger.info("fs consent popup shown: mode=%s path=%s context=%s", req.mode, req.path, req.context or "-")

    sel = 0
    while True:
        w, h = console.size
        lines: list = [Text(""), settings_title(width=w), Text("")]
        lines.append(Text(PAD + "Allow file access?", style="bold white", justify="left"))
        lines.append(Text(PAD + _HINTS[sel], style=SETTINGS_THEME.muted, justify="left"))
        lines.append(Text(""))
        lines.append(
            Align.center(
                build_popup(
                    f"{feature} wants to {verb}:\n{req.path}",
                    width=min(w - 8, 68),
                    border_style=SETTINGS_THEME.warn,
                )
            )
        )
        lines.append(
            Text(
                PAD + "yeaboi only accesses ~/.yeaboi unless you allow a path",
                style=SETTINGS_THEME.dim,
                justify="left",
            )
        )
        lines.append(Text(""))
        btn_top, btn_mid, btn_bot = build_action_buttons(_LABELS, sel)
        lines += [btn_top, btn_mid, btn_bot]
        live.update(build_page_panel(Group(*lines), theme=SETTINGS_THEME, border_style=SETTINGS_THEME.sep, height=h))
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        if not k:  # idle tick / consumed mouse event
            continue
        if k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(len(_LABELS) - 1, sel + 1)
        elif k == "tab":
            sel = (sel + 1) % len(_LABELS)
        elif k in ("enter", " "):
            choice = _CHOICES[sel]
            logger.info("fs consent choice: %s for %s", choice, req.path)
            return choice
        elif k in ("esc", "q"):
            logger.info("fs consent choice: deny (esc) for %s", req.path)
            return "deny"


def _apply_consent(choice: str, req) -> bool:
    """Apply a popup choice to the sandbox. Returns True when access was granted.

    "allow_once" grants for this process only; "allow_always" persists the path
    to the YEABOI_ALLOWED_PATHS whitelist in ~/.yeaboi/.env; "deny" is a no-op.
    """
    from yeaboi import config, fs_policy

    if choice == "allow_once":
        fs_policy.grant_session(req.path)  # logs the grant itself
        return True
    if choice == "allow_always":
        config.add_allowed_path(str(req.path))
        logger.info("fs consent: %s permanently whitelisted", req.path)
        return True
    logger.info("fs consent: %s denied by user", req.path)
    return False


def _preflight_path_choice(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    path,
    *,
    mode: str = "read",
    context: str = "",
) -> str:
    """Like :func:`_preflight_path_consent`, but says WHICH kind of yes it got.

    Returns ``"already_allowed"``, ``"allow_once"``, ``"allow_always"`` or
    ``"deny"``. The distinction matters whenever a caller wants to *save* the
    path: an ``allow_once`` grant dies with the process, so persisting a folder
    backed by one produces a config that works now and silently reviews nothing
    on every scheduled run afterwards. Callers that only need to read the path
    once should keep using the boolean wrapper.
    """
    from yeaboi import fs_policy
    from yeaboi.fs_policy import ConsentRequest

    if fs_policy.is_allowed(path, mode=mode):
        return "already_allowed"
    # Resolve the same way fs_policy does (expanduser + symlink-following
    # resolve) so the granted root matches what enforcement will check.
    resolved = Path(path).expanduser().resolve(strict=False)
    req = ConsentRequest(resolved, mode, context)
    choice = _fs_consent_popup(console, live, read_key, frame_time, supports_timeout, req)
    return choice if _apply_consent(choice, req) else "deny"


def _preflight_path_consent(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    path,
    *,
    mode: str = "read",
    context: str = "",
) -> bool:
    """Main-thread pre-flight: ensure `path` is allowed, asking the user if not.

    Returns True when the path is inside the sandbox (or the user just granted
    it), False when the user denied. Call this BEFORE reading a user-typed path
    so the eventual file access never raises SandboxViolationError.
    """
    choice = _preflight_path_choice(
        console, live, read_key, frame_time, supports_timeout, path, mode=mode, context=context
    )
    return choice != "deny"
