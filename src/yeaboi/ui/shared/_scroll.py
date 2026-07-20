"""Shared scroll-offset helpers for the full-screen TUI.

# See docs: "Architecture" — the TUI is a hand-rolled scroller on rich.Live.
# Each screen renders content to text lines and slices lines[offset:offset+h].

Historically every scroll loop tracked its own offset and let the *screen
builder* clamp for display only (``max(0, min(offset, max_scroll))``). The
clamped value was thrown away, so the loop's counter kept growing past the end
— then scrolling back up wasted N dead keypresses with no visible movement (the
"impossible to scroll sometimes" bug).

The fix: the builder is the single source of truth for how far a screen can
scroll (line-based screens use ``max_scroll(total, viewport)``; variable-height
screens compute their own ``max_scroll``). It hands that back to the loop via
``publish_geometry`` into a shared ``scroll_meta`` dict, and the loop advances
its offset with ``apply_scroll`` using those exact bounds — so the loop counter
and the on-screen position can never diverge.
"""

from __future__ import annotations

# One wheel notch moves this many lines. Arrow keys still move one line at a
# time; the wheel is accelerated because each notch is a discrete "chunk" of
# intent and one-line-per-notch feels sluggish on long content.
WHEEL_STEP = 3

# Every key that apply_scroll() acts on. Scroll loops branch on `k in SCROLL_KEYS`
# so a single membership test routes all scrolling (arrows, wheel, page, home/end)
# through one handler instead of a per-key elif chain.
SCROLL_KEYS = (
    "up",
    "down",
    "scroll_up",
    "scroll_down",
    "pageup",
    "pagedown",
    "home",
    "end",
)


def max_scroll(total_lines: int, viewport_h: int) -> int:
    """Largest valid offset for a line-based screen so the last line is reachable.

    The single definition of this arithmetic for screens that scroll a flat list
    of lines. Variable-height screens (item cards) compute their own maximum and
    pass it straight to :func:`publish_geometry`.
    """
    return max(0, total_lines - viewport_h)


def clamp_scroll(offset: int, total_lines: int, viewport_h: int) -> int:
    """Clamp an offset into ``[0, max_scroll(total_lines, viewport_h)]``."""
    return max(0, min(offset, max_scroll(total_lines, viewport_h)))


def coalesce_scroll(offset: int, first_key: str, meta: dict, read_key_fn) -> int:
    """Apply ``first_key`` then fold in every already-buffered scroll key.

    A fast wheel flick or a held arrow key delivers a *burst* of scroll events.
    Processing them one-per-loop-iteration — each followed by a repaint — lets the
    terminal input buffer back up faster than it drains, which splits escape
    sequences and tears the view ("fast scroll breaks it, slow scroll is fine").

    This drains the burst in one shot: it keeps reading non-blocking
    (``timeout=0``) and applies each scroll key to ``offset``, stopping at the
    first non-scroll key — which it pushes back via :func:`_input.push_back_key`
    so the caller's next ``read_key`` still sees it (no lost keypress). The caller
    then repaints once for the whole burst.

    Falls back to a single ``apply_scroll`` when ``read_key_fn`` can't poll
    non-blocking (e.g. a test stub that ignores ``timeout``).
    """
    mo = meta.get("max_offset", 0)
    vh = meta.get("viewport_h", 1)
    offset = apply_scroll(offset, first_key, mo, vh)
    for _ in range(4096):  # safety bound against a misbehaving reader
        try:
            nxt = read_key_fn(timeout=0.0)
        except TypeError:
            break  # reader doesn't support a timeout — can't drain, that's fine
        if nxt in SCROLL_KEYS:
            offset = apply_scroll(offset, nxt, mo, vh)
        elif nxt == "":
            break  # input drained
        else:
            from yeaboi.ui.shared._input import push_back_key

            push_back_key(nxt)  # a real key ended the burst — hand it back
            break
    return offset


def coalesce_steps(first_key: str, read_key_fn, *, down, up) -> int:
    """Drain a burst of one-step navigation keys; return the net step (+down / -up).

    The offset-based :func:`coalesce_scroll` is for content viewports; this is its
    sibling for **selection carousels** (menus that do ``sel = (sel ± 1) % n``). A
    fast wheel flick or held arrow otherwise fires one selection change + one
    repaint per event, and on an animated screen (ASCII art, shimmer, description
    reveal that restarts each move) that stutters. This folds the whole burst into
    a single net movement so the caller repaints once.

    ``down`` / ``up`` are the key-name collections that step forward / back by one.
    A key in neither ends the burst and is pushed back via
    :func:`_input.push_back_key` so the caller's next ``read_key`` still sees it.
    Falls back to just ``first_key`` when ``read_key_fn`` can't poll non-blocking.
    """
    delta = 1 if first_key in down else (-1 if first_key in up else 0)
    for _ in range(4096):  # safety bound
        try:
            nxt = read_key_fn(timeout=0.0)
        except TypeError:
            break
        if nxt in down:
            delta += 1
        elif nxt in up:
            delta -= 1
        elif nxt == "":
            break
        else:
            from yeaboi.ui.shared._input import push_back_key

            push_back_key(nxt)
            break
    return delta


def publish_geometry(meta: dict | None, max_offset: int, viewport_h: int) -> None:
    """Screen builder → scroll loop hand-off of the true scroll geometry.

    A scroll loop passes an empty dict as its screen builder's ``scroll_meta``.
    The builder — which alone knows its real geometry (viewport reduced by sticky
    headers / warning banners, or content it wraps internally) — calls this once
    it has computed the maximum offset and viewport height, so the loop's next
    :func:`apply_scroll` clamps to exactly what is on screen. The loop counter can
    never run past the last displayed line again.

    ``max_offset`` is the largest valid scroll offset (a line count for flat
    screens, an item/line index for variable-height ones). No-op when ``meta`` is
    None — builders are also called outside scroll loops.
    """
    if meta is None:
        return
    meta["max_offset"] = max(0, max_offset)
    meta["viewport_h"] = max(1, viewport_h)


def apply_scroll(
    offset: int,
    key: str,
    max_offset: int,
    viewport_h: int,
    *,
    page: int | None = None,
    wheel_step: int = WHEEL_STEP,
) -> int:
    """Return the new, clamped scroll offset after handling ``key``.

    Recognised keys (names produced by ``read_key``):
      - ``"up"`` / ``"down"`` — one line
      - ``"scroll_up"`` / ``"scroll_down"`` — one wheel notch (``wheel_step`` lines)
      - ``"pageup"`` / ``"pagedown"`` — one page (``page`` lines, default: a viewport)
      - ``"home"`` / ``"end"`` — jump to the top / bottom

    Any other key returns ``offset`` unchanged. The result is always clamped into
    ``[0, max_offset]``, so callers assign it straight back to their offset
    variable and never track a separate maximum. Pass ``max_offset`` /
    ``viewport_h`` from the ``scroll_meta`` the paired screen builder published.
    """
    max_offset = max(0, max_offset)
    page_step = page if page is not None else max(1, viewport_h - 1)

    if key == "up":
        offset -= 1
    elif key == "down":
        offset += 1
    elif key == "scroll_up":
        offset -= wheel_step
    elif key == "scroll_down":
        offset += wheel_step
    elif key == "pageup":
        offset -= page_step
    elif key == "pagedown":
        offset += page_step
    elif key == "home":
        offset = 0
    elif key == "end":
        offset = max_offset

    return max(0, min(offset, max_offset))
