"""Render tests for the Retro TUI screen builder, theme, and page wiring."""

from io import StringIO

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.agent.state import RetroCard
from yeaboi.retro.board import RetroBoard
from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS
from yeaboi.ui.mode_select.screens._screens_secondary import _build_retro_screen
from yeaboi.ui.shared._components import RETRO_THEME, retro_title


def _render(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


class TestComponents:
    def test_theme_is_teal(self):
        assert RETRO_THEME.accent == "rgb(80,190,190)"

    def test_title_returns_text(self):
        assert isinstance(retro_title(), Text)

    def test_mode_card_registered(self):
        keys = {c["key"] for c in _MODE_CARDS}
        assert "retro" in keys

    def test_color_registered(self):
        from yeaboi.ui.shared._animations import COLOR_RGB

        assert COLOR_RGB["rgb(80,190,190)"] == (80, 190, 190)

    def test_button_colors_registered(self):
        from yeaboi.ui.shared._components import _BTN_COLORS

        assert "Generate Action Items" in _BTN_COLORS and "Close" in _BTN_COLORS


def _data(board):
    return {
        "session_name": "demo-2026-07-10",
        "display_code": "A3F9-1B2C",
        "message": "Server ready",
        "grids": board.cards_by_grid(),
    }


class TestBuildRetroScreen:
    def test_returns_panel_with_cards(self):
        b = RetroBoard("s")
        b.add_card(grid="went_well", text="ci is fast", author="Sam")
        b.add_ai_cards(["fix flaky tests"])
        panel = _build_retro_screen(_data(b), width=100, height=30)
        assert isinstance(panel, Panel)

    def test_handles_empty_grids(self):
        panel = _build_retro_screen(
            {"session_name": "", "display_code": "—", "message": "", "grids": {}},
            width=80,
            height=24,
        )
        assert isinstance(panel, Panel)

    def test_scroll_offset_accepted(self):
        b = RetroBoard("s")
        for i in range(40):
            b.add_card(grid="demos", text=f"card {i}", author="x")
        panel = _build_retro_screen(_data(b), width=80, height=20, scroll_offset=10, action_sel=1)
        assert isinstance(panel, Panel)

    def test_remote_url_and_custom_actions(self):
        b = RetroBoard("s")
        data = _data(b)
        data["public_url"] = "https://calm-tree-1234.trycloudflare.com/"
        data["actions"] = ["Copy Invite", "Retry Link", "Export", "Close"]
        panel = _build_retro_screen(data, width=100, height=30, action_sel=1)
        assert isinstance(panel, Panel)

    def test_missing_optional_keys_default(self):
        # public_url / actions absent — must not raise (backward-compatible builder).
        panel = _build_retro_screen(
            {"session_name": "x", "display_code": "A-B", "message": "", "grids": {}},
            width=80,
            height=24,
        )
        assert isinstance(panel, Panel)


class TestLinkLines:
    """Label+URL rows, which are the only body rows that can outgrow the panel.

    Everything else in these screens is one Text and therefore one terminal row,
    which is what lets the viewport reserve a fixed number of lines. A URL is not
    — a tunnel hostname, or a host link carrying a token and an admin secret, is
    routinely wider than 80 columns. Rich soft-wraps it silently, the viewport
    then draws more rows than it reserved, and whatever sits below is pushed off
    the bottom of the panel. That used to be invisible; with a wrapping action bar
    it costs a whole row of buttons.
    """

    def _lines(self, label: str, url: str, width: int = 80):
        from yeaboi.ui.mode_select.screens._screens_secondary import _link_lines

        return _link_lines(label, url, width=width, label_style="", url_style="")

    def test_short_pair_stays_on_one_line(self):
        lines = self._lines("Share code", "http://192.168.1.24:5173/")
        assert len(lines) == 1
        assert "http://192.168.1.24:5173/" in lines[0].plain

    def test_long_url_moves_to_its_own_line(self):
        url = "http://192.168.1.24:5173/?token=" + "x" * 22 + "&admin=" + "y" * 16
        lines = self._lines("Host link (yours only)", url)
        assert len(lines) >= 2
        assert lines[0].plain.strip() == "Host link (yours only):"

    def test_no_line_ever_exceeds_the_panel(self):
        # The property that matters. A line over budget does not error — it wraps,
        # and the cost lands somewhere else entirely.
        for width in (60, 80, 100, 120):
            urls = (
                "http://a/",
                "https://calm-tree-1234.trycloudflare.com/",
                "http://192.168.1.24:5173/?token=" + "x" * 40,
            )
            for url in urls:
                for line in self._lines("Participant link", url, width):
                    assert len(line.plain) <= width - 8, (width, url, line.plain)

    def test_the_whole_url_survives_the_split(self):
        # Split mid-token on purpose: a URL has no safe break point, and a host
        # reading one off the screen needs all of it, not most of it.
        url = "http://192.168.1.24:5173/?token=" + "x" * 40 + "&admin=" + "y" * 20
        joined = "".join(line.plain.strip() for line in self._lines("Host link", url))
        assert url in joined


class TestJoinBlock:
    """Which link the host is about to paste into the team chat.

    This block was a flat list of four labels — Share code, LAN URL, Remote URL,
    Host link — and the wrong pick hands the room the admin secret. The grouping
    is the safety feature, so it is worth asserting rather than eyeballing.
    """

    def _lines(self, **extra) -> str:
        b = RetroBoard("s")
        data = _data(b)
        data.update(extra)
        return _render(_build_retro_screen(data, width=100, height=44))

    def test_leads_with_the_participant_link_and_the_code(self):
        out = self._lines()
        assert "Send this to your team" in out
        assert "Participant link" in out
        assert "Share code" in out

    def test_says_the_link_is_coming_rather_than_showing_a_local_one(self):
        # Before the tunnel lands the board is loopback-only, so there is no
        # address that would work for a teammate. Saying "preparing" is the honest
        # answer; the LAN URL that used to sit here worked for the same room only
        # and got pasted to remote people anyway.
        out = self._lines()
        assert "preparing" in out
        assert "Setting up the secure link" in out
        assert "127.0.0.1" not in out
        assert "192.168" not in out

    def test_never_calls_the_board_wifi_only(self):
        # The reach caveat is gone with the LAN link it described.
        assert "Same Wi-Fi only" not in self._lines()
        assert "Same Wi-Fi only" not in self._lines(public_url="https://calm-tree-1234.trycloudflare.com/")

    def test_promotes_the_tunnel_link_once_there_is_one(self):
        out = self._lines(public_url="https://calm-tree-1234.trycloudflare.com/")
        assert "one link that carries the code" in out
        assert "calm-tree-1234" in out
        assert "preparing" not in out

    def test_stops_promising_a_link_once_the_tunnel_has_given_up(self):
        # Left on the waiting copy, this block would keep saying "a few seconds"
        # forever, directly under a status line saying the opposite.
        out = self._lines(link_failed=True)
        assert "unavailable" in out
        assert "Retry Link" in out
        assert "preparing" not in out
        assert "a few seconds" not in out

    def test_marks_the_host_link_as_not_for_sharing(self):
        out = self._lines(host_url="https://calm-tree-1234.trycloudflare.com/?token=x&admin=a")
        assert "yours only" in out.lower()
        assert "never send it" in out

    def test_says_nothing_about_joining_on_a_saved_snapshot(self):
        # A replayed run has no server behind it; printing a dead link and a
        # code that resolves to nothing would be worse than printing neither.
        out = self._lines(snapshot=True, host_url="http://x/?token=y")
        assert "Send this to your team" not in out
        assert "Host link" not in out


class TestActionBarFits:
    def test_seven_buttons_wrap_instead_of_running_off_an_80_column_panel(self):
        # The bug this guards: five buttons already came to 92 columns, so the
        # last was drawn past the panel edge — selectable and invisible.
        b = RetroBoard("s")
        data = _data(b)
        # The widest the row ever gets: every standing button plus the Retry
        # Link that appears when the tunnel fails.
        data["actions"] = [
            "Copy Invite",
            "Copy Host Link",
            "Generate Action Items",
            "Export",
            "Anonymize",
            "Close",
            "Retry Link",
        ]
        out = _render(_build_retro_screen(data, width=80, height=40), width=80)
        assert "Copy Invite" in out
        assert "Close" in out
        assert "Retry Link" in out
        # The bar is budgeted against the panel INTERIOR, not the console. Packed
        # to the console width a row comes to exactly 80, the interior is 78, and
        # every row soft-wraps — which silently pushes the last bank of buttons
        # off the bottom. Assert no rendered row touches the border.
        for line in out.splitlines():
            assert len(line) <= 80, (len(line), line)
        for line in out.splitlines():
            assert len(line) <= 80


class TestCarriedActionItems:
    def test_carried_block_renders_with_status(self):
        b = RetroBoard("s")
        data = _data(b)
        data["carried"] = [
            RetroCard(id="k1", grid="action_items", text="finish the docs", origin="carryover", status="done"),
            RetroCard(id="k2", grid="action_items", text="revisit CI", origin="carryover", status="carried_over"),
        ]
        out = _render(_build_retro_screen(data, width=100, height=40))
        assert "Last sprint's actions" in out
        assert "1/2 resolved" in out
        assert "finish the docs" in out and "[Done]" in out
        assert "[Carried Over]" in out

    def test_no_carried_block_when_empty(self):
        b = RetroBoard("s")
        out = _render(_build_retro_screen(_data(b), width=100, height=40))
        assert "Last sprint's actions" not in out
