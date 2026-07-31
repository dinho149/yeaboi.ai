"""Tests for the mode-card status chip (_card_badge / _build_mode_row).

The chip is drawn onto the two block-font title rows, which the welcome screen's
click hit-testing measures. These tests pin both halves of that: what renders,
and that the row height never moves.
"""

import io

from rich.console import Console, Group
from rich.panel import Panel

from yeaboi.beta import BETA_LABEL, BETA_RGB
from yeaboi.ui.mode_select.screens._screens import (
    _INTAKE_CARDS,
    _MODE_CARDS,
    _OFFLINE_CARDS,
    _build_mode_row,
    _build_mode_screen,
    _card_badge,
)


def _render(renderables, width: int = 120) -> str:
    """Render mode rows the way the screen does — grouped inside a page panel.

    Rich only honours a Text's ``no_wrap``/``overflow`` when it is rendered as a
    child renderable; a bare ``console.print(text)`` re-derives them from the
    print call and folds instead. Rendering through Panel(Group(...)) is both the
    faithful path and the one where the crop guarantee actually applies.
    """
    console = Console(file=io.StringIO(), width=width)
    console.print(Panel(Group(*renderables), width=width))
    return console.file.getvalue()


def _performance_card() -> dict:
    return next(card for card in _MODE_CARDS if card["key"] == "performance")


class TestCardBadge:
    def test_explicit_badge_wins(self):
        assert _card_badge({"badge": "BETA", "available": True}) == "BETA"

    def test_unavailable_card_is_coming_soon(self):
        assert _card_badge({"available": False}) == "COMING SOON"

    def test_available_card_without_a_badge_has_no_chip(self):
        assert _card_badge({"available": True}) == ""

    def test_runtime_roster_card_has_no_chip(self):
        # The Performance roster synthesises a card per engineer and feeds it to
        # the same renderer. It sets `available` but never `badge`, so it must
        # render no chip — an engineer is not a beta feature.
        assert _card_badge({"title": "Ada Lovelace", "color": "rgb(220,110,90)", "available": True}) == ""


class TestPerformanceCardRegistration:
    def test_performance_is_flagged_beta(self):
        assert _performance_card()["badge"] == BETA_LABEL

    def test_performance_stays_available(self):
        # Beta is not "coming soon". `available` gates Enter, the click handler
        # and the welcome-screen jump key — flipping it would take the mode away,
        # which is a different decision than labelling it.
        assert _performance_card()["available"] is True

    def test_no_other_card_carries_a_badge(self):
        badged = [
            card["key"]
            for card in (*_MODE_CARDS, *_INTAKE_CARDS, *_OFFLINE_CARDS)
            if card.get("badge") and card.get("key") != "performance"
        ]
        assert badged == []

    def test_every_card_still_has_the_core_keys(self):
        for card in (*_MODE_CARDS, *_INTAKE_CARDS, *_OFFLINE_CARDS):
            assert {"title", "description", "available", "color"} <= set(card)


class TestChipRendering:
    def test_chip_shows_when_the_card_is_selected(self):
        idx = _MODE_CARDS.index(_performance_card())
        out = _render([_build_mode_screen(idx, width=120, height=40, desc_reveal=999)])
        assert BETA_LABEL in out

    def test_chip_shows_when_the_card_is_not_selected(self):
        # A status marker you only see after arrowing onto the card isn't labelling.
        idx = _MODE_CARDS.index(_performance_card())
        other = 0 if idx != 0 else 1
        out = _render([_build_mode_screen(other, width=120, height=40, desc_reveal=999)])
        assert BETA_LABEL in out

    def test_chip_is_hidden_until_the_intro_sweep_finishes_the_row(self):
        card = _performance_card()
        mid_sweep = _render(_build_mode_row(card, selected=False, sweep_front=6.0), width=120)
        assert BETA_LABEL not in mid_sweep

        finished = _render(_build_mode_row(card, selected=False, sweep_front=9_999.0), width=120)
        assert BETA_LABEL in finished

    def test_chip_uses_no_block_glyphs(self):
        # mode_at_row locates title rows by scanning for block-font glyphs; a chip
        # drawn with box characters would register as a title row of its own.
        chip_only = _render(
            _build_mode_row(
                {"title": " ", "color": "rgb(220,110,90)", "available": True, "badge": "BETA"}, selected=False
            )
        )
        assert not any(ch in chip_only for ch in "█▀▄")


class TestComingSoonChip:
    """The generalised unavailable path — no card uses it today, but it's the
    branch this change replaced the old description suffix with."""

    def _card(self) -> dict:
        return {
            "title": "Ghost",
            "description": "Not built yet.",
            "available": False,
            "color": "rgb(160,160,180)",
        }

    def test_unavailable_card_renders_coming_soon(self):
        out = _render(_build_mode_row(self._card(), selected=False))
        assert "COMING SOON" in out

    def _chip_line(self, selected: bool) -> str:
        from rich.console import Console as RichConsole

        console = RichConsole(file=io.StringIO(), width=120, force_terminal=True, color_system="truecolor")
        console.print(Panel(Group(*_build_mode_row(self._card(), selected=selected)), width=120))
        return next(line for line in console.file.getvalue().splitlines() if "COMING SOON" in line)

    def test_chip_stays_grey_in_both_selection_states(self):
        # It may dim when unselected (every row does), but it must never change
        # hue — an amber chip on a disabled card would read as a beta marker.
        amber = f"{BETA_RGB[0]};{BETA_RGB[1]};{BETA_RGB[2]}"
        for selected in (False, True):
            line = self._chip_line(selected)
            assert amber not in line, f"selected={selected}"

    def test_selected_chip_uses_the_disabled_grey(self):
        assert "90;90;100" in self._chip_line(selected=True)

    def test_beta_chip_is_not_the_disabled_grey(self):
        from rich.console import Console as RichConsole

        console = RichConsole(file=io.StringIO(), width=120, force_terminal=True, color_system="truecolor")
        console.print(Panel(Group(*_build_mode_row(_performance_card(), selected=True)), width=120))
        chip_line = next(line for line in console.file.getvalue().splitlines() if BETA_LABEL in line)
        assert f"{BETA_RGB[0]};{BETA_RGB[1]};{BETA_RGB[2]}" in chip_line


class TestRowHeightIsUnaffected:
    def _row_count(self, card: dict, *, selected: bool, width: int) -> int:
        out = _render(_build_mode_row(card, selected=selected, desc_max_lines=2), width=width)
        # Rich prints each renderable on its own line; the title renderable is the
        # first, and it must occupy exactly the two block-font rows.
        return len([line for line in out.splitlines() if any(ch in line for ch in "█▀▄")])

    def test_badged_card_is_two_title_rows(self):
        assert self._row_count(_performance_card(), selected=False, width=120) == 2

    def test_badged_card_is_two_title_rows_when_selected(self):
        assert self._row_count(_performance_card(), selected=True, width=120) == 2

    def test_badged_card_is_two_title_rows_at_the_narrow_layout(self):
        # 108 columns is the narrowest layout that still shows the companion, and
        # the Performance wordmark plus chip clears the column budget by one.
        assert self._row_count(_performance_card(), selected=False, width=58) == 2

    def test_long_roster_name_crops_rather_than_folding(self):
        """The crop applies to every row, not just badged ones.

        The Performance roster feeds engineer names through this same renderer.
        A long name used to fold onto extra rows; it now crops. That's required
        for the row-height invariant, and this pins the behaviour change.
        """
        card = {
            "title": "Bartholomew Featherstonehaugh",
            "description": "3 open 1:1 actions",
            "available": True,
            "color": "rgb(220,110,90)",
        }
        assert self._row_count(card, selected=False, width=60) == 2

    def test_absurdly_long_title_with_a_chip_still_crops(self):
        card = {"title": "A" * 60, "description": "x", "available": True, "badge": "BETA", "color": "rgb(220,110,90)"}
        assert self._row_count(card, selected=False, width=60) == 2
