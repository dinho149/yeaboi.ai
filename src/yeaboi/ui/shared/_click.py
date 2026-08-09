"""Shared mouse-click hit-testing for the TUI.

The input layer (:mod:`yeaboi.ui.shared._input`) already turns SGR mouse
reports into ``"click:<x>:<y>"`` events (1-based terminal cell coords). Only the
main menu consumed them historically (via ``mode_at_row``); these helpers let
every other input loop map a click onto its own layout without re-deriving each
screen's geometry by hand.

Button rows are hit-tested against the **actually rendered frame** rather than a
per-screen formula. Static formulas are fragile here: button rows are not always
bottom-anchored (short modal screens top-flow them), the ASCII titles vary in
height, and dynamic popups shift everything below them. Rendering the current
panel once per click (clicks are rare — never per frame) sidesteps all of that
and keeps every caller identical: build the panel, then ask
:func:`button_click` which button — if any — a click landed on.

The horizontal button geometry (:func:`button_spans`) is shared with
:func:`build_action_buttons` so the two stay in lock-step.
"""

from __future__ import annotations

from rich.console import Console
from rich.segment import Segment

from yeaboi.ui.shared._components import _BTN_GAP, _BTN_MIN_W, PAD


def parse_click(key: str) -> tuple[int, int] | None:
    """Parse a ``"click:<x>:<y>"`` event into 1-based ``(x, y)``, or None.

    Returns None for anything that is not a click event, and for malformed
    coordinates (never raises), so loops can call it on every key unconditionally.
    """
    if not isinstance(key, str) or not key.startswith("click:"):
        return None
    try:
        x, y = (int(p) for p in key.split(":")[1:3])
    except ValueError:
        return None
    return x, y


def stage_click(panel, x: int, y: int) -> str | None:
    """Which setup stage a click landed on, or None.

    The analysis setup page publishes ``panel._stage_regions`` — one
    ``(x0, y0, x1, y1, stage)`` span per set it is NOT currently editing, in
    absolute 1-based terminal coords. It counts those rows as it lays the page
    out rather than deriving them from a formula, so unlike :func:`button_click`
    there is nothing to re-render here: the answer is already on the panel.
    """
    for x0, y0, x1, y1, stage in getattr(panel, "_stage_regions", ()):
        if x0 <= x <= x1 and y0 <= y <= y1:
            return stage
    return None


def option_click(panel, x: int, y: int):
    """Which option row of the set being edited a click landed on, or None.

    Companion to :func:`stage_click` over ``panel._option_regions``. The value
    returned is the stage's own CURSOR value — an int, or a ``(row, col)`` pair
    for the ragged sources grid — not a row number, so the loop can move its
    cursor there and then run exactly the code the keyboard would.
    """
    for x0, y0, x1, y1, target in getattr(panel, "_option_regions", ()):
        if x0 <= x <= x1 and y0 <= y <= y1:
            return target
    return None


def button_spans(labels: list[str], *, pad: str = PAD) -> list[tuple[int, int]]:
    """Return the 1-based inclusive ``(start_col, end_col)`` span of each button
    as laid out by :func:`build_action_buttons`.

    Kept in lock-step with that builder: ``pad`` left margin, each button
    ``max(_BTN_MIN_W - 2, len(label) + 2) + 2`` cells wide (inner + two border
    cells), separated by ``_BTN_GAP`` spaces.
    """
    spans: list[tuple[int, int]] = []
    col = len(pad)  # 0-based column of the first button's left border cell
    for label in labels:
        if spans:
            col += _BTN_GAP
        width = max(_BTN_MIN_W - 2, len(label) + 2) + 2
        spans.append((col + 1, col + width))  # 1-based, inclusive
        col += width
    return spans


def _row_text(line: list[Segment]) -> str:
    """Flatten a rendered line (list of Segments) to its plain text."""
    return "".join(seg.text for seg in line)


def _button_runs(text: str) -> list[tuple[int, int]]:
    """Return 1-based inclusive ``(start, end)`` column spans of each ``╭──╮`` run.

    A :func:`build_action_buttons` button's top border is exactly ``╭`` + one or
    more ``─`` + ``╮``. Scanning the rendered row for these runs finds the buttons
    at their true on-screen columns regardless of the panel's border/padding
    offset — clicks are in absolute terminal coords, so this must be too.
    """
    runs: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "╭":
            j = i + 1
            while j < n and text[j] == "─":
                j += 1
            if j < n and text[j] == "╮":
                runs.append((i + 1, j + 1))  # 1-based inclusive
                i = j + 1
                continue
        i += 1
    return runs


def button_click(
    console: Console,
    panel,
    x: int,
    y: int,
    labels: list[str],
    *,
    pad: str = PAD,
) -> int | None:
    """Map a click at 1-based ``(x, y)`` to a button index in ``panel``, or None.

    Renders ``panel`` at the console's current size and finds the
    :func:`build_action_buttons` row — the row bearing exactly ``len(labels)``
    ``╭──╮`` button-top runs — then checks whether the click fell inside a
    button's three-row band and horizontal span. Robust to where the row landed
    (top-flowed, bottom-anchored, or shifted by a popup) and to the panel's
    border/padding offset.

    Returns None when the click missed every button (or the row isn't present).
    """
    n_btn = len(labels)
    if n_btn == 0:
        return None
    try:
        lines = console.render_lines(panel, console.options, pad=True)
    except Exception:  # noqa: BLE001 - a render hiccup should never break input handling
        return None
    for r, line in enumerate(lines):
        runs = _button_runs(_row_text(line))
        if len(runs) != n_btn:
            continue
        top_row = r + 1  # 1-based row of the button top border
        if not (top_row <= y <= top_row + 2):  # the three button rows
            continue
        for i, (start, end) in enumerate(runs):
            if start <= x <= end:
                return i
        return None
    return None
