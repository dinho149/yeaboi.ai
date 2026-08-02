"""Render tests for the Daily Standup TUI screen builder and helpers."""

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.ui import mode_select
from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS
from yeaboi.ui.mode_select.screens._screens_secondary import (
    _SAVED_SETUP_ACTIONS,
    _build_standup_saved_setup_screen,
    _build_standup_screen,
    _build_standup_team_member_screen,
    _build_standup_team_source_screen,
)
from yeaboi.ui.shared._components import STANDUP_THEME, standup_title


def _render(panel: Panel, width: int) -> str:
    """Render a panel to plain text at an exact width, as a terminal would."""
    console = Console(width=width)
    with console.capture() as cap:
        console.print(panel)
    return cap.get()


# The full five-row summary the gate shows once every step has been confirmed.
_GATE_ROWS = [
    ("Trackers", "Jira, Azure DevOps"),
    ("Members", "Ahmet Ince, Alexandru Popa, Daniel Daraban +3 more"),
    ("Code", "1 GitHub repo(s) · 2 Azure project(s)\nyeaboi.ai, acme-core, acme-web"),
    ("Docs", "Confluence"),
    ("Last run", "2 days ago · 84% confidence"),
]


def _report() -> StandupReport:
    return StandupReport(
        date="2026-07-10",
        sprint_name="Sprint 5",
        sprint_day=3,
        sprint_total_days=10,
        confidence_pct=82,
        confidence_label="At risk",
        confidence_rationale="behind ideal burn",
        team_summary="steady progress",
        member_updates=(
            MemberUpdate(name="Alice", summary="login page", source="inferred"),
            MemberUpdate(name="Bob", summary="paired on auth", blockers="waiting on review", source="self-reported"),
        ),
        activity_counts=(("github", 2), ("jira", 1)),
    )


class TestComponents:
    def test_theme_is_magenta(self):
        assert STANDUP_THEME.accent == "rgb(200,100,180)"

    def test_title_returns_text(self):
        from rich.text import Text

        assert isinstance(standup_title(), Text)

    def test_mode_card_registered(self):
        keys = {c["key"] for c in _MODE_CARDS}
        assert "daily-standup" in keys

    def test_color_registered(self):
        from yeaboi.ui.shared._animations import COLOR_RGB

        assert COLOR_RGB["rgb(200,100,180)"] == (200, 100, 180)


class TestBuildStandupScreen:
    def test_live_page_initially_focuses_generate(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            mode_select,
            "_collect_standup_data",
            lambda: {
                "session_id": "s1",
                "session_name": "Demo",
                "report": None,
                "config": None,
                "schedule": {},
                "message": "",
            },
        )
        monkeypatch.setattr(
            "yeaboi.ui.mode_select.screens._screens_secondary._build_standup_screen",
            lambda *args, **kwargs: captured.append(kwargs["action_sel"]) or Text("standup"),
        )

        mode_select._run_standup_page(
            type("Console", (), {"size": (100, 36)})(),
            type("Live", (), {"update": lambda self, renderable: None})(),
            lambda **kwargs: "q",
            0.001,
            True,
        )

        assert captured[0] == 0

    def test_returns_panel_with_report(self):
        data = {
            "session_name": "demo-2026-07-10",
            "config": {"enabled": True, "time": "09:50", "weekdays": "1-5", "delivery_channels": ["terminal"]},
            "schedule": {"installed": True, "platform": "launchd"},
            "report": _report(),
            "message": "",
        }
        panel = _build_standup_screen(data, width=100, height=30)
        assert isinstance(panel, Panel)

    def test_handles_empty_data(self):
        panel = _build_standup_screen({}, width=80, height=24)
        assert isinstance(panel, Panel)

    def test_handles_no_report_no_config(self):
        data = {"session_name": "demo", "config": None, "schedule": {}, "report": None, "message": "hi"}
        panel = _build_standup_screen(data, width=80, height=24)
        assert isinstance(panel, Panel)

    def test_scrollable_at_small_height(self):
        # A tall report in a short viewport must still build (scrollbar path).
        data = {"session_name": "demo", "report": _report(), "schedule": {"installed": False}}
        panel = _build_standup_screen(data, width=60, height=12, scroll_offset=5)
        assert isinstance(panel, Panel)

    def test_action_selection_variants(self):
        data = {"report": _report(), "schedule": {}}
        for sel in range(4):  # Generate, Team, Identity, Back
            assert isinstance(_build_standup_screen(data, width=80, height=24, action_sel=sel), Panel)

    def test_saved_setup_gate_renders_summary_and_every_button(self):
        panel = _build_standup_saved_setup_screen(
            [("Trackers", "Jira"), ("Members", "Alice, Bob"), ("Docs", "Confluence")],
            action_sel=0,
            width=90,
            height=28,
        )
        assert isinstance(panel, Panel)
        out = _render(panel, 90)
        assert "Use your saved setup?" in out
        # A name list wraps inside a half-width card, so assert on the names
        # themselves rather than on the joined string the caller passed.
        assert "TRACKERS" in out and "Alice" in out and "Bob" in out
        for label in _SAVED_SETUP_ACTIONS:
            assert label in out

    def test_saved_setup_gate_buttons_are_registered(self):
        from yeaboi.ui.shared._components import _BTN_COLORS

        for label in _SAVED_SETUP_ACTIONS:
            assert label in _BTN_COLORS

    def test_saved_setup_gate_selection_variants(self):
        rows = [("Trackers", "Jira")]
        for sel in range(len(_SAVED_SETUP_ACTIONS)):
            assert isinstance(_build_standup_saved_setup_screen(rows, action_sel=sel, width=80, height=24), Panel)

    def test_saved_setup_gate_shows_the_setup_breadcrumb(self):
        out = _render(_build_standup_saved_setup_screen(_GATE_ROWS, width=100, height=34), 100)

        assert "STANDUP  ›  SAVED SETUP" in out
        assert "Nothing runs until you choose" in out

    def test_saved_setup_gate_draws_cards_when_wide(self):
        out = _render(_build_standup_saved_setup_screen(_GATE_ROWS, width=100, height=34), 100)

        # Every row becomes its own card, glyph and all.
        assert "◆ TRACKERS" in out and "◷ LAST RUN" in out
        assert "╭" in out

    def test_saved_setup_gate_falls_back_to_a_list_when_narrow(self):
        out = _render(_build_standup_saved_setup_screen(_GATE_ROWS, width=70, height=30), 70)

        assert "Trackers" in out and "Last run" in out
        assert "◆ TRACKERS" not in out  # no cards below the two-column threshold

    def test_saved_setup_gate_gives_a_multiline_value_its_own_row(self):
        rows = [("Code", "2 Azure project(s)\nacme-core, acme-web")]

        wide = _render(_build_standup_saved_setup_screen(rows, width=100, height=34), 100)
        assert "2 Azure project(s)" in wide
        assert "acme-core, acme-web" in wide

        # The compact list is one row per label, so the second line joins on.
        narrow = _render(_build_standup_saved_setup_screen(rows, width=70, height=30), 70)
        assert "2 Azure project(s) · acme-core, acme-web" in narrow

    def test_saved_setup_gate_drops_the_note_before_it_truncates_a_value(self):
        """Five cards do not fit 32 rows *and* the reassurance line — data wins."""
        out = _render(_build_standup_saved_setup_screen(_GATE_ROWS, width=120, height=32), 120)

        assert "yeaboi.ai, acme-core, acme-web" in out  # the Code card's second line survived
        assert "Nothing runs until you choose" not in out
        assert "…" not in out

    def test_saved_setup_gate_keeps_the_note_when_there_is_room(self):
        out = _render(_build_standup_saved_setup_screen(_GATE_ROWS[:2], width=120, height=32), 120)

        assert "Nothing runs until you choose" in out

    def test_saved_setup_gate_tolerates_an_unknown_row_label(self):
        out = _render(_build_standup_saved_setup_screen([("Sprint", "Sprint 12")], width=100, height=34), 100)

        assert "· SPRINT" in out  # neutral bullet, not a KeyError

    @pytest.mark.parametrize("size", [(70, 20), (80, 24), (100, 22), (100, 34), (120, 32), (140, 40)])
    def test_saved_setup_gate_clicks_land_on_the_buttons(self, size):
        """The cards' own ╭──╮ borders must not capture clicks meant for the actions.

        button_click identifies the action row as the first row carrying exactly
        len(labels) button-top runs, so a card row wide enough to draw three of
        them would swallow every click below it. The confirm-loop tests stub
        button_click out, so this is the only check on the real thing.
        """
        from yeaboi.ui.shared._click import button_click

        width, height = size
        console = Console(width=width, height=height)
        panel = _build_standup_saved_setup_screen(_GATE_ROWS, width=width, height=height)
        lines = _render(panel, width).rstrip("\n").split("\n")
        label_row = next(i for i, ln in enumerate(lines) if "Use saved" in ln)

        for expected, label in enumerate(_SAVED_SETUP_ACTIONS):
            x = lines[label_row].index(label) + 2  # 1-based, inside the label
            assert button_click(console, panel, x, label_row + 1, _SAVED_SETUP_ACTIONS) == expected

        # A click on a card, or in the dead space beside the buttons, misses.
        card_row = next((i for i, ln in enumerate(lines) if "TRACKERS" in ln), None)
        if card_row is not None:
            assert button_click(console, panel, 10, card_row + 1, _SAVED_SETUP_ACTIONS) is None
        assert button_click(console, panel, width - 3, label_row + 1, _SAVED_SETUP_ACTIONS) is None

    @pytest.mark.parametrize("size", [(70, 20), (80, 24), (88, 26), (100, 22), (100, 34), (140, 40)])
    def test_saved_setup_gate_keeps_the_buttons_on_screen(self, size):
        """The card grid must never grow past the viewport it was measured for."""
        width, height = size
        out = _render(_build_standup_saved_setup_screen(_GATE_ROWS, width=width, height=height), width)
        lines = out.rstrip("\n").split("\n")

        assert len(lines) == height  # nothing pushed past the panel's bottom border
        assert not [ln for ln in lines if len(ln.rstrip()) > width]  # no horizontal overflow
        # All three button rows drawn: the labels and the bottom border below them.
        label_row = next(i for i, ln in enumerate(lines) if "Use saved" in ln)
        assert "╰" in lines[label_row + 1]

    def test_team_source_picker_renders_saved_selection(self):
        panel = _build_standup_team_source_screen(
            [("jira", "Jira"), ("azure_devops", "Azure DevOps")],
            {0},
            0,
            width=90,
            height=28,
        )
        assert isinstance(panel, Panel)

        console = Console(width=100, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Choose update sources" in out
        assert "Jira" in out and "Azure DevOps" in out

    def test_team_member_picker_renders_checked_members_and_scrollbar(self):
        roster = [f"Engineer {idx}" for idx in range(20)]
        panel = _build_standup_team_member_screen(roster, {0, 3}, 3, width=80, height=28)
        assert isinstance(panel, Panel)

        console = Console(width=90, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Choose team members" in out
        assert "2 of 20 selected" in out

    def test_report_renders_as_themed_rows_not_emoji(self):
        # The dashboard should use the status strip (meters) and clean rows,
        # not the plaintext emoji dump used for Slack/email delivery.

        panel = _build_standup_screen({"report": _report(), "schedule": {"installed": False}}, width=100, height=60)
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "At risk" in out
        assert "▰" in out  # status-strip meters
        assert "🟡" not in out and "🟢" not in out  # no emoji in the TUI dashboard

    def test_status_strip_shows_sprint_day_and_confidence(self):

        panel = _build_standup_screen({"report": _report(), "schedule": {}}, width=110, height=40)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Sprint Sprint 5" in out
        assert "Day 3/10" in out
        assert "82%" in out
        # The old duplicated header block is gone.
        assert "Latest Standup" not in out
        assert "Sections" not in out

    def test_status_strip_no_report(self):

        panel = _build_standup_screen({"report": None, "schedule": {}}, width=100, height=30)
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        assert "No standup yet" in cap.get()

    def test_banner_shows_first_warning(self):

        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed", "second"))
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=110, height=40)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "⚠ 2 notices · Jira: authentication failed" in out

    def test_long_notice_capped_and_stays_within_border(self):
        # A long run-on warning must not stretch edge-to-edge on a wide terminal nor push
        # the notice past the panel's right border (ambiguous-width ⚠/— safety margin).
        import re

        long_warn = (
            "Not scanned: Azure Devops (AZURE_DEVOPS_PROJECT not set), Github (STANDUP_GITHUB_REPO not set), "
            "Local Git (no repo path configured), Notion (NOTION_ROOT_PAGE_ID not set) — connect these in Settings"
        )
        width = 220
        panel = _build_standup_screen({"report": StandupReport(warnings=(long_warn,))}, width=width, height=40)
        console = Console(width=width, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        notice = next(ln for ln in cap.get().splitlines() if "notice" in ln)
        vis = re.sub(r"\x1b\[[0-9;]*m", "", notice).rstrip()
        assert vis.endswith("│")  # right border intact
        assert vis.endswith("…   │") or "…" in vis  # truncated, not full text
        assert len(vis[:-1].rstrip()) < 130  # capped teaser, not stretched across 220 cols

    def test_banner_message_wins_over_warnings(self):

        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        panel = _build_standup_screen({"report": rep, "schedule": {}, "message": "Generated."}, width=110, height=40)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Generated." in out
        assert "⚠ 1 notice ·" not in out

    def test_warnings_render_in_notices_detail(self):

        rep = StandupReport(
            date="2026-07-10",
            warnings=(
                "Jira: authentication failed — check token",
                "AI summary unavailable — ANTHROPIC_API_KEY not set",
            ),
        )
        panel = _build_standup_screen(
            {"report": rep, "schedule": {"installed": False}}, width=100, height=60, view="notices"
        )
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Notices" in out
        assert "authentication failed" in out
        assert "ANTHROPIC_API_KEY not set" in out

    def test_notices_section_listed_on_overview(self):

        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=100, height=60)
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Notices" in out
        assert "1 notice" in out

    def test_schedule_detail_shows_standup_time_and_runs_at(self):

        data = {
            "config": {
                "enabled": True,
                "time": "10:00",
                "lead_minutes": 10,
                "weekdays": "1-5",
                "delivery_channels": ["terminal"],
            },
            "schedule": {"installed": True, "platform": "launchd"},
            "report": None,
        }
        panel = _build_standup_screen(data, width=100, height=60, view="schedule")
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Standup time:" in out and "10:00" in out
        assert "Runs at:" in out and "09:50" in out

    def test_overview_shows_my_update_and_collapsed_team_row(self):

        data = {"report": _report(), "schedule": {}, "my_name": "Bob"}
        panel = _build_standup_screen(data, width=110, height=60)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Team Summary" in out
        assert "Sprint & Confidence" not in out  # sprint facts live in the strip now
        assert "My Update" in out
        # Collapsed Team row: count teaser with active/quiet glyphs.
        assert "1 update · 1 active ● 0 quiet ○" in out
        assert "Alice" not in out  # members hidden until the Team row is expanded

    def test_overview_expanded_team_shows_member_subrows(self):

        data = {"report": _report(), "schedule": {}, "my_name": "Bob", "team_expanded": True}
        panel = _build_standup_screen(data, width=110, height=60)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "▾" in out  # expanded chevron on the Team row
        assert "└ ●" in out  # tree guide + active glyph on the (only, hence last) sub-row
        assert "Alice" in out

    def test_member_detail_shows_self_report_and_analysis(self):

        rep = _report()
        rep = StandupReport(
            date=rep.date,
            member_updates=(
                MemberUpdate(
                    name="Bob",
                    summary="Merged the auth PR.",
                    blockers="waiting on review",
                    source="combined",
                    self_report="Paired with Alice.\nStarting on tokens next.",
                ),
            ),
        )
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=100, height=100, view="member:Bob")
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "In their words" in out
        assert "Paired with Alice." in out
        assert "Starting on tokens next." in out  # Alt+Enter paragraph break preserved
        assert "General overview" in out
        assert "Ticketing" in out
        assert "Code" in out
        assert "Documentation" in out
        assert "Merged the auth PR." in out
        assert "Blocker" in out
        assert "waiting on review" in out

    def test_member_detail_uses_dashboard_tiles_and_category_evidence(self):

        rep = StandupReport(
            member_updates=(
                MemberUpdate(
                    name="Ada",
                    summary="Moved authentication rollout forward.",
                    source="inferred",
                    activity_count=7,
                    ticketing_activity_count=3,
                    code_activity_count=2,
                    documentation_activity_count=2,
                    ticketing_summary="Closed PSOT-9.",
                    code_summary="Reviewed the token PR.",
                    documentation_summary="Updated the rollout guide.",
                    ticketing_links=(("PSOT-9", "https://x/browse/PSOT-9"),),
                    code_links=(("PR 42", "https://x/pull/42"),),
                    documentation_links=(("Rollout guide", "https://x/wiki/rollout"),),
                ),
            )
        )
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=120, height=100, view="member:Ada")
        console = Console(width=130, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "ACTIVE" in out and "TRACKED ACTIVITY" in out
        assert "TOTAL" in out and "TICKETING" in out and "CODE" in out and "DOCUMENTATION" in out
        assert "tracked updates" in out and "Jira / Boards" in out and "commits / PRs" in out
        assert "↗ PSOT-9" in out
        assert "↗ PR 42" in out
        assert "↗ Rollout guide" in out

    def test_member_detail_empty_states_and_short_terminal_are_safe(self):

        rep = StandupReport(member_updates=(MemberUpdate(name="Quiet", summary=""),))
        data = {"report": rep, "schedule": {}}
        panel = _build_standup_screen(data, width=56, height=18, view="member:Quiet")
        assert isinstance(panel, Panel)

        # Scroll through the atomic dashboard renderables and verify category
        # empty states remain reachable on a narrow terminal.
        rendered = []
        for offset in range(20):
            meta = {}
            panel = _build_standup_screen(
                data,
                width=56,
                height=32,
                view="member:Quiet",
                scroll_offset=offset,
                scroll_meta=meta,
            )
            console = Console(width=60, file=open("/dev/null", "w"))
            with console.capture() as cap:
                console.print(panel)
            rendered.append(cap.get())
            if offset >= meta.get("max_offset", 0):
                break
        out = "\n".join(rendered)
        assert "QUIET" in out
        assert "No activity" in out and "detected for this member." in out
        assert "No ticketing activity detected" in out
        assert "No code activity detected" in out
        assert "No documentation activity detected" in out

    def test_detail_views_all_build(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order

        rep = StandupReport(date="2026-07-10", member_updates=_report().member_updates, warnings=("w",))
        data = {
            "report": rep,
            "schedule": {},
            "config": {"enabled": False, "time": "10:00"},
            "my_name": "Bob",
            "team_expanded": True,
        }
        for key in standup_card_order(data):
            assert isinstance(_build_standup_screen(data, width=80, height=24, view=key), Panel)

    def test_card_order_no_report(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order

        assert standup_card_order({"report": None}) == ["schedule"]

    def test_card_order_collapsed_and_expanded(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order

        rep = StandupReport(date="2026-07-10", member_updates=_report().member_updates, warnings=("w",))
        data = {"report": rep, "my_name": "Bob"}
        assert standup_card_order(data) == ["summary", "my_update", "team", "activity", "schedule", "notices"]
        data["team_expanded"] = True
        # Sub-rows insert right after "team"; my own card never appears there.
        assert standup_card_order(data) == [
            "summary",
            "my_update",
            "team",
            "member:Alice",
            "activity",
            "schedule",
            "notices",
        ]

    def test_teasers_for_my_update_and_team(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_teaser

        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(name="Bob", summary="auth work", self_report="shipped auth", source="combined"),
                MemberUpdate(name="Alice", summary="login page", source="inferred"),
            ),
        )
        data = {"report": rep, "my_name": "Bob"}
        assert standup_card_teaser("my_update", data) == "auth work · ✍ update"
        # Alice has a real summary (legacy report, activity_count 0) → counted active.
        assert standup_card_teaser("team", data) == "1 update · 1 active ● 0 quiet ○"
        # No member matching my_name → nudge towards Generate (which asks for it).
        data["my_name"] = "Zed"
        assert standup_card_teaser("my_update", data) == "No update yet — Generate asks for it"
        assert standup_card_teaser("team", data) == "2 updates · 2 active ● 0 quiet ○"

    def test_member_teaser_glyphs_and_gist(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_teaser

        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(
                    name="Ada",
                    summary="moved PSOT-9 to review",
                    activity_count=2,
                    links=(("PSOT-9", "https://x/browse/PSOT-9"),),
                ),
                MemberUpdate(name="Quiet Quentin", summary="No activity detected.", activity_count=0),
            ),
        )
        data = {"report": rep, "my_name": "Me"}
        # Active member leads with the first ticket reference.
        assert standup_card_teaser("member:Ada", data) == "PSOT-9 · moved PSOT-9 to review"
        assert standup_card_teaser("member:Quiet Quentin", data) == "no activity detected"
        assert standup_card_teaser("team", data) == "2 updates · 1 active ● 1 quiet ○"

    def test_expanded_member_rows_show_quiet_glyph(self):

        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(name="Ada", summary="shipped auth", activity_count=1),
                MemberUpdate(name="Quentin", summary="No activity detected.", activity_count=0),
            ),
        )
        data = {"report": rep, "schedule": {}, "my_name": "Me", "team_expanded": True}
        panel = _build_standup_screen(data, width=110, height=40)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "├ ●" in out  # active member glyph
        assert "└ ○" in out  # quiet member glyph on the last sub-row
        assert "no activity detected" in out

    def test_summary_teaser_wraps_to_two_rows(self):

        long_summary = (
            "The sprint is in a critical position at day 8, with only 25% confidence. "
            "Auth0 log streaming is complete but GuardDuty and Teleport remain in flight."
        )
        rep = StandupReport(date="2026-07-10", team_summary=long_summary)
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=110, height=40)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        lines = [ln for ln in cap.get().splitlines() if "critical position" in ln or "Auth0" in ln]
        # First chunk on the card row, continuation (ellipsized) on the next row.
        assert len(lines) == 2
        assert "…" in lines[1]

    def test_member_detail_shows_links(self):

        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(
                    name="Bob",
                    summary="moved PSOT-1 to review",
                    links=(("PSOT-1", "https://x.atlassian.net/browse/PSOT-1"),),
                ),
            ),
        )
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=110, height=100, view="member:Bob")
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Evidence" in out
        assert "↗ PSOT-1" in out
        assert "browse/PSOT-1" in out

    def test_my_update_detail_renders_my_member_card(self):

        rep = StandupReport(
            date="2026-07-10",
            member_updates=(MemberUpdate(name="Bob", summary="Merged auth.", self_report="hi", source="combined"),),
        )
        panel = _build_standup_screen(
            {"report": rep, "schedule": {}, "my_name": "Bob"}, width=100, height=60, view="my_update"
        )
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "In their words" in out
        assert "Merged auth." in out

    def test_overview_selection_auto_scrolls(self):
        # Selecting the last of many expanded member sub-rows in a short viewport must not crash.
        members = tuple(MemberUpdate(name=f"Dev {i}", summary="work") for i in range(20))
        rep = StandupReport(date="2026-07-10", member_updates=members)
        data = {"report": rep, "schedule": {}, "team_expanded": True}
        panel = _build_standup_screen(data, width=80, height=14, selected_card=23)
        assert isinstance(panel, Panel)


class TestBuildStandupInputScreen:
    def test_returns_panel(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

        panel = _build_standup_input_screen(
            "Standup time (HH:MM)", "09:5", step="Configure standup  (1/5)", default="09:50", width=80, height=24
        )
        assert isinstance(panel, Panel)

    def test_shows_prompt_value_and_hint(self):

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

        panel = _build_standup_input_screen("Your name", "Ali", step="My update  (1/2)", width=90, height=24)
        console = Console(width=100, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Your name" in out
        assert "Ali" in out
        assert "Esc to cancel" in out

    def test_multirow_box_honours_newlines(self):

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

        panel = _build_standup_input_screen(
            "Your update for today",
            "shipped auth\nnext: tokens",
            step="My update  (2/2)",
            width=90,
            height=30,
            box_rows=6,
        )
        console = Console(width=100, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "shipped auth" in out
        assert "next: tokens" in out  # rendered on its own row, not glued to line 1
        assert "shipped authnext" not in out
        assert "Alt+Enter" in out  # newline hint shown for the large box


class TestSettingsMasksStandupSecrets:
    def test_slack_and_smtp_password_masked(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        data = {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/SECRET123456",
            "STANDUP_SMTP_PASSWORD": "supersecretpw",
            "STANDUP_SMTP_HOST": "smtp.example.com",
            "_config_path": "/tmp/.env",
        }
        panel = _build_settings_screen(data, width=100, height=40, active_tab=2)  # System tab (Standup)
        # Render to text and confirm the raw secret does not appear.

        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "SECRET123456" not in out
        assert "supersecretpw" not in out
        assert "smtp.example.com" in out  # non-secret shown


class TestButtonRowNotClipped:
    def test_scrollbar_has_no_trailing_newline(self):
        from yeaboi.ui.shared._components import build_scrollbar

        for kwargs in ({"always_show": True}, {}):
            sb = build_scrollbar(10, 30, 0, 20, **kwargs)
            assert sb is not None
            assert not sb.plain.endswith("\n")
            assert sb.plain.count("\n") == 9  # exactly viewport_h rows

    def test_button_bottom_border_renders(self):
        # The scrollbar's old trailing newline pushed the buttons' bottom border
        # off the fixed-height panel — the "overlapping buttons" bug.

        data = {"report": _report(), "schedule": {}}
        for height in (24, 30, 40):
            panel = _build_standup_screen(data, width=100, height=height)
            console = Console(width=110, height=height + 2, file=open("/dev/null", "w"))
            with console.capture() as cap:
                console.print(panel)
            out = cap.get()
            assert "╰──" in out.splitlines()[-3]  # button bottom border is on-screen

    def test_no_button_highlighted_when_sections_focused(self):
        # action_sel=-1 (sections focus) must render without error and without
        # crashing on the "no selected button" case.
        data = {"report": _report(), "schedule": {}}
        assert isinstance(_build_standup_screen(data, width=100, height=30, action_sel=-1), Panel)

    def test_overview_has_three_buttons_and_focus_hint(self):

        panel = _build_standup_screen({"report": _report(), "schedule": {}}, width=110, height=40)
        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Open" not in out  # Enter opens sections directly now
        for label in ("Generate", "Identity", "Back"):
            assert label in out
        # The My Update button is gone — Generate collects the user's update itself.
        assert "│ My Update │" not in out
        # The key hint moved into the subtitle line (no standalone hint row).
        assert "↑/↓ sections" in out and "←/→ buttons" in out

    def test_button_bottom_border_renders_with_banner(self):
        # A warning banner adds a header row — the height budget must absorb it
        # or the button bottom border falls off the fixed-height panel.

        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        data = {"report": rep, "schedule": {}}
        for height in (24, 30, 40):
            panel = _build_standup_screen(data, width=100, height=height)
            console = Console(width=110, height=height + 2, file=open("/dev/null", "w"))
            with console.capture() as cap:
                console.print(panel)
            assert "╰──" in cap.get().splitlines()[-3]

    def test_activity_detail_shows_window(self):

        rep = StandupReport(
            date="2026-07-20",
            activity_counts=(("jira", 3),),
            activity_window="Fri 2026-07-17 00:00 → now",
        )
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=100, height=40, view="activity")
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Fri 2026-07-17 00:00" in out


class TestStandupProgressScreen:
    def test_returns_panel_with_steps(self):

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

        panel = _build_standup_progress_screen(
            ["Collecting recent activity", "Writing summaries with AI"],
            width=100,
            height=30,
            elapsed=12.0,
            anim_tick=1.5,
        )
        assert isinstance(panel, Panel)
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Generating standup" in out
        assert "• Collecting recent activity" in out  # activity history, not a false completion
        assert "✓ Collecting recent activity" not in out
        assert "Writing summaries with AI" in out  # current phase
        assert "12s" in out  # elapsed

        rows = [item for item in panel.renderable.renderables if hasattr(item, "plain")]
        history = next(item for item in rows if "Collecting recent activity" in item.plain)
        current = next(item for item in rows if "Writing summaries with AI" in item.plain)
        assert str(history.style) == STANDUP_THEME.accent
        assert str(current.style) == f"bold {STANDUP_THEME.accent_bright}"
        assert str(panel.border_style) == STANDUP_THEME.accent

    def test_empty_progress_and_small_height(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

        assert isinstance(_build_standup_progress_screen([], width=60, height=12), Panel)


class TestBuildScheduleStepScreen:
    """Render tests for the schedule wizard's radio/checkbox step screen."""

    def _render(self, panel, width=100):

        console = Console(width=width, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        return cap.get()

    def _options(self):
        return [("09:00", ""), ("09:30", ""), ("10:00", "current"), ("Custom…", "type any HH:MM")]

    def test_radio_step_marks_cursor_row(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

        panel = _build_standup_schedule_step_screen(
            self._options(), 2, step_index=0, heading="Standup time", width=90, height=24
        )
        assert isinstance(panel, Panel)
        out = self._render(panel)
        assert "Standup time" in out
        assert "‹ ● 10:00 ›" in out  # cursor row is the selection on radio steps
        assert "○ 09:00" in out
        assert "Custom…" in out and "type any HH:MM" in out
        # Radio steps don't offer Space toggling.
        assert "Space toggle" not in out
        assert "Esc back" in out

    def test_progress_dots_show_step_names(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            _SCHEDULE_STEP_NAMES,
            _build_standup_schedule_step_screen,
        )

        panel = _build_standup_schedule_step_screen(self._options(), 0, step_index=1, heading="Lead")
        out = self._render(panel)
        for name in _SCHEDULE_STEP_NAMES:
            assert name in out

    def test_multi_step_checked_glyphs_and_count(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

        days = [(d, "") for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
        panel = _build_standup_schedule_step_screen(
            days, 5, checked={0, 1, 2, 3, 4}, step_index=2, heading="Which days", width=90, height=30
        )
        out = self._render(panel)
        assert "5 of 7 selected" in out
        assert "● Mon" in out and "● Fri" in out
        assert "‹ ○ Sat ›" in out  # cursor on an unchecked row
        assert "Space toggle" in out

    def test_message_row_renders(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

        panel = _build_standup_schedule_step_screen(
            [("terminal", "")], 0, checked=set(), step_index=3, heading="Channels", message="Select at least one"
        )
        assert "Select at least one" in self._render(panel)

    def test_small_terminal_does_not_crash(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

        panel = _build_standup_schedule_step_screen(
            self._options(), 3, step_index=4, heading="Enable", width=60, height=12
        )
        assert isinstance(panel, Panel)
        self._render(panel, width=60)

    def test_custom_step_names_replace_the_schedule_ones(self):
        """The screen is shared with the transcript source picker."""
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

        panel = _build_standup_schedule_step_screen(
            [("Sweep my transcript folders", "3 unreviewed file(s)")],
            0,
            step_index=0,
            heading="Where should I look?",
            step_names=["Source", "Review", "File"],
        )
        out = self._render(panel)
        assert "Source" in out and "Review" in out and "File" in out
        assert "Channels" not in out  # a schedule-only step name


class TestTranscriptSourceStep:
    """The picker that turns "find the folder, copy the file" into one keypress."""

    def _pick(self, monkeypatch, key_script, *, count=0, clip=""):
        """Drive the source step with a scripted key sequence."""
        from yeaboi.ui.mode_select import _standup_review_source_step

        monkeypatch.setattr(
            "yeaboi.ui.mode_select._standup_transcript_counts",
            lambda sid: (count, f"{len(clip):,} characters ready" if clip.strip() else "nothing on the clipboard"),
        )
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_text", lambda: clip)
        keys = iter(key_script)
        live = type("L", (), {"update": lambda self, x: None})()
        console = type("C", (), {"size": (100, 30)})()
        return _standup_review_source_step(console, live, lambda **kw: next(keys), 0.03, True, "sid")

    def test_enter_on_the_first_row_sweeps(self, monkeypatch):
        assert self._pick(monkeypatch, ["enter"], count=3) == ("sweep", "")

    def test_paste_returns_the_clipboard_text(self, monkeypatch):
        # count=1 so the cursor starts on Sweep and "down" lands on Paste.
        kind, value = self._pick(monkeypatch, ["down", "enter"], count=1, clip="Alice: hi\nBob: hey")
        assert kind == "paste"
        assert value == "Alice: hi\nBob: hey"
        assert "\n" in value  # the newlines a text box would have eaten

    def test_an_empty_clipboard_falls_through_to_opening_the_folder(self, monkeypatch):
        """Choosing paste with nothing to paste should help, not error."""
        assert self._pick(monkeypatch, ["down", "enter"], clip="   ")[0] == "open"

    def test_open_row_returns_open(self, monkeypatch):
        assert self._pick(monkeypatch, ["down", "down", "enter"], count=1) == ("open", "")

    def test_esc_backs_out(self, monkeypatch):
        assert self._pick(monkeypatch, ["esc"]) is None

    def test_cursor_starts_on_paste_when_there_is_nothing_to_sweep(self, monkeypatch):
        """Nothing unreviewed but a clipboard full of text — offer the useful row."""
        assert self._pick(monkeypatch, ["enter"], count=0, clip="Alice: hi")[0] == "paste"

    def test_cursor_starts_on_sweep_when_files_are_waiting(self, monkeypatch):
        assert self._pick(monkeypatch, ["enter"], count=2, clip="Alice: hi")[0] == "sweep"


class TestTranscriptCounts:
    """The counts are read once on entry — discover() hashes files and the
    clipboard helper shells out with a 10s timeout, so neither may run per frame."""

    def test_counts_unreviewed_files_and_clipboard_chars(self, monkeypatch):
        from yeaboi.ui.mode_select import _standup_transcript_counts

        monkeypatch.setattr(
            "yeaboi.standup.transcripts.discover", lambda sid, **kw: ([("a.vtt", False), ("b.txt", False)], [])
        )
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_text", lambda: "x" * 1234)
        count, hint = _standup_transcript_counts("sid")
        assert count == 2
        assert "1,234 characters" in hint

    def test_empty_clipboard_says_so(self, monkeypatch):
        from yeaboi.ui.mode_select import _standup_transcript_counts

        monkeypatch.setattr("yeaboi.standup.transcripts.discover", lambda sid, **kw: ([], []))
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_text", lambda: None)
        assert _standup_transcript_counts("sid") == (0, "nothing on the clipboard")

    def test_a_broken_discover_does_not_block_the_picker(self, monkeypatch):
        from yeaboi.ui.mode_select import _standup_transcript_counts

        def _boom(*a, **k):
            raise OSError("disk gone")

        monkeypatch.setattr("yeaboi.standup.transcripts.discover", _boom)
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_text", lambda: "Alice: hi")
        count, hint = _standup_transcript_counts("sid")
        assert count == 0
        assert "characters ready" in hint

    def test_a_broken_clipboard_does_not_block_the_picker(self, monkeypatch):
        from yeaboi.ui.mode_select import _standup_transcript_counts

        def _boom():
            raise OSError("no display")

        monkeypatch.setattr("yeaboi.standup.transcripts.discover", lambda sid, **kw: ([("a.vtt", False)], []))
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_text", _boom)
        assert _standup_transcript_counts("sid") == (1, "nothing on the clipboard")


class TestDayOverDayScreen:
    def test_member_detail_shows_progress_note_and_outlook(self):

        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(
                    name="Bob",
                    summary="Merged the auth PR.",
                    progress_note="Wrapped up yesterday's PSOT-9 work.",
                    outlook="Likely to start on tokens.",
                ),
            ),
        )
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=100, height=100, view="member:Bob")
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Since last standup" in out
        assert "Wrapped up yesterday's PSOT-9 work." in out
        assert "Outlook" in out
        assert "Likely to start on tokens." in out

    def test_member_detail_without_fields_hides_panels(self):

        rep = StandupReport(date="2026-07-10", member_updates=(MemberUpdate(name="Bob", summary="x"),))
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=100, height=100, view="member:Bob")
        console = Console(width=110, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Since last standup" not in out
        assert "Outlook" not in out

    def test_status_strip_shows_trend_arrow(self):

        for trend, delta, marker in (("improving", 6, "▲+6"), ("declining", -8, "▼8")):
            rep = StandupReport(
                date="2026-07-10",
                sprint_name="Sprint 5",
                sprint_day=3,
                sprint_total_days=10,
                confidence_pct=74,
                confidence_label="At risk",
                confidence_delta=delta,
                confidence_trend=trend,
            )
            panel = _build_standup_screen({"report": rep, "schedule": {}}, width=120, height=100, view="overview")
            console = Console(width=130, file=open("/dev/null", "w"))
            with console.capture() as cap:
                console.print(panel)
            assert marker in cap.get()

    def test_status_strip_no_trend_no_arrow(self):

        rep = StandupReport(
            date="2026-07-10",
            sprint_name="Sprint 5",
            confidence_pct=74,
            confidence_label="At risk",
        )
        panel = _build_standup_screen({"report": rep, "schedule": {}}, width=120, height=100, view="overview")
        console = Console(width=130, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "▲" not in out
        assert "▼" not in out


def _render(panel, width: int) -> str:
    """Render a panel to plain text for content assertions."""
    import io

    from rich.console import Console

    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=False).print(panel)
    return buf.getvalue()


def _review(**over):
    from yeaboi.agent.state import StandupGap, TranscriptClaim, TranscriptReview, TranscriptSource

    base = dict(
        standup_date="2026-07-30",
        accuracy_note="Claims checked: 1 confirmed by the evidence.",
        sources=(TranscriptSource(filename="2026-07-30-standup.vtt"),),
        gaps=(
            StandupGap(
                fingerprint="fp1",
                scope="product",
                title="Standup misses Confluence comments",
                root_cause="The Confluence collector reads page edits but not comments.",
                priority="high",
                claims=(TranscriptClaim(member="Alice", quote="I also commented on the design doc"),),
            ),
        ),
        config_suggestions=(
            StandupGap(
                scope="config",
                title="acme/infra is outside your code scope",
                remedy="Add acme/infra via Standup -> Configure -> Code",
            ),
        ),
    )
    base.update(over)
    return TranscriptReview(**base)


def _review_data(**over) -> dict:
    data = {
        "session_name": "demo",
        "report": _report(),
        "schedule": {},
        "my_name": "Alice",
        "review": _review(),
        "gap_issues": [{"fingerprint": "fp1", "issue_number": 42, "occurrences": 3}],
    }
    data.update(over)
    return data


class TestTranscriptReviewCard:
    def test_card_absent_until_a_review_exists(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order

        assert "gaps" not in standup_card_order({"report": _report()})

    def test_card_present_once_reviewed(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order

        assert "gaps" in standup_card_order(_review_data())

    def test_teaser_counts_gaps_suggestions_and_filed(self):
        from yeaboi.ui.mode_select.screens._standup_sections import standup_card_teaser

        teaser = standup_card_teaser("gaps", _review_data())
        assert "1 gap" in teaser
        assert "1 to fix in config" in teaser
        assert "1 filed" in teaser

    def test_overview_shows_the_card(self):
        out = _render(_build_standup_screen(_review_data(), width=100, height=30), 100)
        assert "Transcript Review" in out

    def test_detail_separates_product_gaps_from_config_fixes(self):
        panel = _build_standup_screen(_review_data(), width=100, height=40, view="gaps")
        out = _render(panel, 100)
        assert "Gaps in standup itself" in out
        assert "Standup misses Confluence comments" in out
        assert "Fix in your configuration" in out
        assert "Add acme/infra" in out

    def test_detail_shows_the_quote_and_issue_number(self):
        out = _render(_build_standup_screen(_review_data(), width=100, height=40, view="gaps"), 100)
        assert "I also commented on the design doc" in out
        assert "#42" in out

    def test_detail_empty_state(self):
        data = _review_data(review=_review(gaps=(), config_suggestions=()), gap_issues=[])
        out = _render(_build_standup_screen(data, width=100, height=30, view="gaps"), 100)
        assert "No gaps found" in out

    def test_detail_with_many_gaps_still_renders(self):
        from yeaboi.agent.state import StandupGap

        gaps = tuple(
            StandupGap(fingerprint=f"fp{i}", scope="product", title=f"Gap {i}", priority="medium") for i in range(8)
        )
        data = _review_data(review=_review(gaps=gaps))
        assert isinstance(_build_standup_screen(data, width=80, height=24, view="gaps"), Panel)

    def test_renders_at_80_columns_without_overflow(self):
        out = _render(_build_standup_screen(_review_data(), width=80, height=30, view="gaps"), 80)
        assert not [line for line in out.splitlines() if len(line) > 80]


class TestActionRowWrapping:
    """Six standup actions outgrow an 80-column terminal, so the bar must wrap —
    a clipped button is reachable with the arrow keys and invisible on screen."""

    ACTIONS = ["Generate", "Review", "Team", "Anonymize", "Identity", "Share Online", "Back"]

    def test_every_button_is_drawn_at_80_columns(self):
        out = _render(_build_standup_screen(_review_data(), width=80, height=30, actions=self.ACTIONS), 80)
        for label in self.ACTIONS:
            assert label in out, f"{label} was clipped off the panel"

    def test_no_line_exceeds_the_width(self):
        out = _render(_build_standup_screen(_review_data(), width=80, height=30, actions=self.ACTIONS), 80)
        assert not [line for line in out.splitlines() if len(line) > 80]

    def test_wide_terminal_keeps_one_row(self):
        from yeaboi.ui.shared._components import action_rows_height

        assert action_rows_height(self.ACTIONS, 200) == 4

    def test_narrow_terminal_takes_the_extra_height_from_the_viewport(self):
        from yeaboi.ui.shared._components import action_rows_height

        assert action_rows_height(self.ACTIONS, 80) > 4

    def test_default_actions_include_review(self):
        out = _render(_build_standup_screen(_review_data(), width=100, height=30), 100)
        assert "Review" in out

    def test_selection_across_every_action(self):
        for sel in range(len(self.ACTIONS)):
            panel = _build_standup_screen(_review_data(), width=80, height=30, actions=self.ACTIONS, action_sel=sel)
            assert isinstance(panel, Panel)
