"""Render tests for the setup wizard's two picker screens.

``_build_select_screen`` (pick an LLM provider) and ``_build_vc_select_screen``
(pick a version-control provider) are the first and last pickers of the
first-run wizard. Both are pure builders returning the page ``Panel`` that
``select_provider``'s ``live.update()`` loop draws, and both carry animation
arguments (``visible``, ``fade_style``/``fade_indices``, ``selected_style``,
``shimmer_tick``) that only the transition code passes — so a regression in any
of those branches is invisible until someone runs setup by hand.

Rendering goes through the returned page ``Panel`` exactly as the Live loop does
it (see ``ui/provider_select/_transitions.py``). That is already the "render
inside a Panel context" path the TUI standards require: the rows are child
renderables of the panel, so Rich honours their justification rather than
re-deriving it from a bare ``console.print(text)``.
"""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS, _VC_OPTIONS
from yeaboi.ui.provider_select.screens._screens import _ACCENT, _build_select_screen
from yeaboi.ui.provider_select.screens._screens_vc import _build_vc_select_screen
from yeaboi.ui.shared._ascii_font import render_ascii_text

# A style the wizard never uses, so finding its SGR escape in a capture proves
# the argument under test reached the row it was meant to paint.
_PROBE = "rgb(1,2,3)"
_PROBE_FG = "38;2;1;2;3"


# The frame is fixed-height and does not scroll, so a short terminal genuinely
# crops the lowest rows (and the progress bar with them). Tests that assert on
# content build at a height that fits the whole picker; the crop behaviour gets
# its own test rather than silently swallowing every other assertion.
_TALL = 40
# The classic terminal height, and the builders' own default — a real user's
# frame, not a contrived one.
_SHORT = 24


def _render(panel: Panel, *, width: int = 80, height: int = _TALL) -> str:
    """Render a page panel to plain text (no escapes), the way the Live loop draws it.

    Both dimensions are pinned: the pickers size their vertical centring from the
    ``height`` argument, and a non-tty console's ambient height would otherwise
    make the capture order-dependent.
    """
    console = Console(file=io.StringIO(), width=width, height=height, highlight=False)
    console.print(panel)
    return console.file.getvalue()


def _render_ansi(panel: Panel, *, width: int = 80, height: int = _TALL) -> str:
    """Render with truecolor escapes retained, so per-row styles are assertable."""
    console = Console(
        file=io.StringIO(),
        width=width,
        height=height,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        highlight=False,
    )
    console.print(panel)
    return console.file.getvalue()


def _select(selected: int = 0, **kwargs) -> Panel:
    """Build the LLM picker at a frame height that fits every row."""
    kwargs.setdefault("height", _TALL)
    return _build_select_screen(selected, **kwargs)


def _vc(selected: int = 0, **kwargs) -> Panel:
    """Build the version-control picker at a frame height that fits every row."""
    kwargs.setdefault("height", _TALL)
    return _build_vc_select_screen(selected, **kwargs)


def _art_top(name: str) -> str:
    """The top row of a name's two-line block-font art, as the picker draws it."""
    return render_ascii_text(name)[0]


def _accent_bg() -> str:
    """Truecolor background SGR fragment for the wizard accent (the active chip)."""
    r, g, b = re.fullmatch(r"rgb\((\d+),(\d+),(\d+)\)", _ACCENT).groups()
    return f"48;2;{r};{g};{b}"


def _active_chip(out: str) -> str | None:
    """Label of the progress chip painted with the accent background in *out*.

    ``_build_progress`` gives the active step ``bold white on <accent>``; done
    steps get a green background and future steps a dim grey one, so the accent
    background uniquely identifies the active chip in a truecolor capture.
    """
    match = re.search(rf"{_accent_bg()}m ([A-Za-z ]+?) \x1b", out)
    return match.group(1) if match else None


def _has_subtitle(out: str, text: str) -> bool:
    """True when *text* appears on the frame's subtitle row, not just anywhere.

    The VC picker's subtitle is "Version Control" and its active progress chip is
    labelled "Version Control" too (``_STEPS[3]``), so a plain ``in`` check passes
    with the subtitle row deleted — it only re-proves the chip. The chip row is
    the one drawn with the ▟/▛ parallelogram caps, so excluding those lines
    isolates the subtitle.
    """
    return any(text in line and "▟" not in line and "▛" not in line for line in out.splitlines())


# ---------------------------------------------------------------------------
# _build_select_screen — the LLM provider picker (wizard step 0)
# ---------------------------------------------------------------------------


class TestSelectScreen:
    def test_returns_page_panel(self):
        assert isinstance(_select(0), Panel)

    def test_subtitle_renders(self):
        assert _has_subtitle(_render(_select(0)), "Select your LLM provider")

    def test_every_provider_row_renders(self):
        # Provider names are drawn as two-line block art, not plain text, so the
        # assertion goes through the same font helper the picker uses.
        out = _render(_select(0))
        for card in _PROVIDER_CARDS:
            assert _art_top(card["name"]) in out, f"{card['name']} row missing"

    def test_selected_row_shows_its_tagline(self):
        # The selected card's separator row doubles as its tagline — the "what am
        # I signing up for" line. Ollama's is the one that makes the free option
        # visible before any card is entered.
        ollama = next(i for i, c in enumerate(_PROVIDER_CARDS) if c["provider_val"] == "ollama")
        out = _render(_select(ollama))
        assert _PROVIDER_CARDS[ollama]["tagline"] in out

    def test_unselected_rows_hide_their_taglines(self):
        # Only one tagline is ever on screen; the rest are plain separators.
        out = _render(_select(0))
        assert _PROVIDER_CARDS[0]["tagline"] in out
        for card in _PROVIDER_CARDS[1:]:
            assert card["tagline"] not in out

    def test_visible_restricts_the_rows_drawn(self):
        # The intro/outro transitions reveal and retract rows one at a time.
        out = _render(_select(0, visible=[0]))
        assert _art_top(_PROVIDER_CARDS[0]["name"]) in out
        for card in _PROVIDER_CARDS[1:]:
            assert _art_top(card["name"]) not in out

    def test_selection_outside_visible_draws_no_tagline(self):
        # Mid-transition the selection can sit on a row that is not on screen yet;
        # every drawn row then falls through to the plain separator branch.
        out = _render(_select(4, visible=[0, 1]))
        for card in _PROVIDER_CARDS:
            assert card["tagline"] not in out

    def test_empty_visible_still_renders_the_frame(self):
        # The first transition frame has nothing revealed yet — the subtitle and
        # the progress bar must still draw.
        assert _has_subtitle(_render(_select(0, visible=[])), "Select your LLM provider")
        assert _active_chip(_render_ansi(_select(0, visible=[]))) == "LLM Provider"

    def test_selected_style_overrides_the_selected_row(self):
        out = _render_ansi(_select(0, selected_style=_PROBE))
        assert _PROBE_FG in out

    def test_fade_style_applies_to_fade_indices(self):
        out = _render_ansi(_select(0, fade_indices=[2], fade_style=_PROBE))
        assert _PROBE_FG in out

    def test_fade_style_without_indices_is_inert(self):
        # fade_style alone must not repaint anything — the fading rows are named
        # by fade_indices, and an empty list is the steady state.
        out = _render_ansi(_select(0, fade_indices=[], fade_style=_PROBE))
        assert _PROBE_FG not in out

    def test_shimmer_tick_animates_the_selected_row(self):
        # The selected row shimmers per character; two ticks must not be identical
        # or the picker would look frozen.
        assert _render_ansi(_select(0, shimmer_tick=0.0)) != _render_ansi(_select(0, shimmer_tick=0.5))

    def test_step_argument_drives_the_active_chip(self):
        # The picker is reachable as a re-entry from a later section, so the chip
        # it highlights is caller-driven rather than pinned to step 0.
        assert _active_chip(_render_ansi(_select(0))) == "LLM Provider"
        assert _active_chip(_render_ansi(_select(0, step=2))) == "Docs"

    @pytest.mark.xfail(
        strict=True,
        reason="five provider rows overflow a 24-row frame and crop the progress bar away "
        "— see the cowork proposal for the provider-select height crop",
    )
    def test_progress_bar_survives_a_short_terminal(self):
        # The frame is fixed-height and does not scroll, so overflowing body rows
        # push the footer off the bottom. 24 rows is the classic terminal size and
        # the height the picker is actually built at (`h` comes from
        # `console.size` — see ui/provider_select/__init__.py), so a user on a
        # short terminal loses the whole "where am I in setup" progress bar.
        #
        # Asserting the line count instead would be vacuous: build_page_panel
        # passes an explicit `height` to Rich, which pads or crops to it whatever
        # the body contains.
        out = _render_ansi(_select(0, width=80, height=_SHORT), height=_SHORT)
        assert _active_chip(out) == "LLM Provider"


# ---------------------------------------------------------------------------
# _build_vc_select_screen — the version-control picker (wizard step 3)
# ---------------------------------------------------------------------------


class TestVcSelectScreen:
    def test_returns_page_panel(self):
        assert isinstance(_vc(0), Panel)

    def test_subtitle_renders(self):
        # "Version Control" is also the active chip's label here, so this asserts
        # on the subtitle row specifically — see _has_subtitle.
        assert _has_subtitle(_render(_vc(0)), "Version Control")

    def test_every_option_row_renders(self):
        # GitHub and Skip — "Skip" is an option, not a key binding, so it has to
        # be visibly on the list.
        out = _render(_vc(0))
        for option in _VC_OPTIONS:
            assert _art_top(option["name"]) in out, f"{option['name']} row missing"

    def test_active_chip_is_version_control(self):
        # This picker hardcodes step 3 — it is the wizard's last section.
        assert _active_chip(_render_ansi(_vc(0))) == "Version Control"

    def test_visible_restricts_the_rows_drawn(self):
        out = _render(_vc(0, visible=[0]))
        assert _art_top(_VC_OPTIONS[0]["name"]) in out
        assert _art_top(_VC_OPTIONS[1]["name"]) not in out

    def test_empty_visible_still_renders_the_frame(self):
        assert _has_subtitle(_render(_vc(0, visible=[])), "Version Control")
        assert _active_chip(_render_ansi(_vc(0, visible=[]))) == "Version Control"

    def test_selected_style_overrides_the_selected_row(self):
        out = _render_ansi(_vc(0, selected_style=_PROBE))
        assert _PROBE_FG in out

    def test_fade_style_applies_to_fade_indices(self):
        out = _render_ansi(_vc(0, fade_indices=[1], fade_style=_PROBE))
        assert _PROBE_FG in out

    def test_fade_style_without_indices_is_inert(self):
        out = _render_ansi(_vc(0, fade_indices=[], fade_style=_PROBE))
        assert _PROBE_FG not in out

    def test_shimmer_tick_animates_the_selected_row(self):
        assert _render_ansi(_vc(0, shimmer_tick=0.0)) != _render_ansi(_vc(0, shimmer_tick=0.5))

    def test_selecting_the_second_option_moves_the_highlight(self):
        # The two rows must not render identically — the picker's only feedback
        # is which name is lit.
        assert _render_ansi(_vc(0)) != _render_ansi(_vc(1))

    def test_progress_bar_survives_a_short_terminal(self):
        # Two options rather than five, so this picker's body still fits a 24-row
        # frame and keeps its footer. Pinning that here is what makes the LLM
        # picker's xfail a statement about row count rather than about the frame.
        out = _render_ansi(_vc(0, width=80, height=_SHORT), height=_SHORT)
        assert _active_chip(out) == "Version Control"
