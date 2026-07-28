"""Render tests for the Reporting TUI screen builder."""

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.agent.state import DeliveredItem, DeliveryReport
from yeaboi.ui.mode_select.screens._screens_secondary import _build_reporting_screen


def _render(panel: Panel, width: int = 100) -> str:
    console = Console(file=io.StringIO(), width=width)
    console.print(panel)
    return console.file.getvalue()


def _report(n_items: int = 2) -> DeliveryReport:
    items = tuple(
        DeliveredItem(key=f"PROJ-{i}", title=f"Shipped thing {i}", status="Done", source="jira", assignee="Sam")
        for i in range(n_items)
    )
    return DeliveryReport(
        period_label="Last sprint",
        period_start="2026-07-01",
        period_end="2026-07-14",
        project_name="Demo",
        sprint_names=("Sprint 9",),
        headline="A strong sprint for the platform.",
        executive_summary="We shipped SSO and hardened the pipeline.",
        themes=(("Security", ("SSO shipped", "Audit log added")),),
        highlights=("SSO live",),
        metrics=(("Items delivered", str(n_items)), ("Contributors", "1")),
        delivered_items=items,
        emoji_theme=(("headline", "🚀"), ("metrics", "📊")),
        warnings=("Sample warning.",),
        generated_at="2026-07-14",
    )


_PALETTES = {
    "midnight": {
        "bg1": "#0d1117",
        "bg2": "#161b2e",
        "fg": "#e6edf3",
        "muted": "#9aa4b2",
        "accent": "#8c78e6",
        "accent2": "#b8a6ff",
    },
    "aurora": {
        "bg1": "#04121a",
        "bg2": "#0a2a2a",
        "fg": "#e8fff6",
        "muted": "#8fc9be",
        "accent": "#28c2a0",
        "accent2": "#6ff0d0",
    },
    "sunset": {
        "bg1": "#1a0d16",
        "bg2": "#3a1424",
        "fg": "#fff1e8",
        "muted": "#d9a08f",
        "accent": "#f0784e",
        "accent2": "#ffb27a",
    },
    "mono": {
        "bg1": "#0b0b0c",
        "bg2": "#1c1c1f",
        "fg": "#f4f4f5",
        "muted": "#a1a1aa",
        "accent": "#d4d4d8",
        "accent2": "#ffffff",
    },
    "corporate": {
        "bg1": "#101418",
        "bg2": "#1c2733",
        "fg": "#eef3f8",
        "muted": "#93a3b4",
        "accent": "#2f81f7",
        "accent2": "#79b8ff",
    },
}


class TestBuildReportingScreen:
    def test_picker_view_renders_periods_and_actions(self):
        data = {
            "session_name": "Demo",
            "view": "picker",
            "periods": [
                ("last_week", "Last week", "the last 7 days"),
                ("last_sprint", "Last sprint", "recent sprint"),
                ("last_month", "Last month (~2 sprints)", "last ~4 weeks"),
                ("quarter", "Whole quarter (Q3 2026)", "pick the sprints"),
                ("window", "Custom range", "explicit start and end dates"),
            ],
            "selected_idx": 1,
            "theme": "aurora",
            "palettes": _PALETTES,
            "actions": ["Generate Report", "Theme", "Back"],
        }
        panel = _build_reporting_screen(data, width=100, height=36, action_sel=0)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "REPORTING SETUP" in out  # analysis-style wizard breadcrumb
        assert "Last week" in out
        assert "Last sprint" in out
        assert "Last month" in out
        assert "Custom range" in out
        assert "aurora" in out  # current deck theme shown
        assert "Generate" in out  # action button present

    def test_detail_view_renders_report_sections(self):
        data = {
            "view": "detail",
            "detail_title": "Delivery Report — Last sprint",
            "report": _report(),
            "theme": "midnight",
            "actions": ["Export", "Theme", "Back"],
        }
        panel = _build_reporting_screen(data, width=100, height=44, action_sel=0)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "A strong sprint" in out  # headline
        assert "By the numbers" in out  # metrics section
        assert "Items delivered" in out
        assert "Executive summary" in out
        assert "Security" in out  # theme section title
        assert "Highlights" in out
        assert "PROJ-0" in out  # delivered item key
        assert "Notices" in out  # warnings section
        assert "Export" in out

    def test_detail_view_empty_data(self):
        data = {"view": "detail", "report": None, "detail_title": "X", "actions": ["Export", "Theme", "Back"]}
        panel = _build_reporting_screen(data, width=100, height=30)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "nothing to show" in out.lower()

    def test_no_permanent_scrollbar_track(self):
        """Regression: the old always_show=True painted a track next to short content,
        which read as a doubled scrollbar. The builder must let build_scrollbar return
        None when content fits (the track glyph is the panel-border glyph, so this is
        asserted at the source level like the repo's other AST guards)."""
        import inspect

        from yeaboi.ui.mode_select.screens import _screens_secondary as mod

        assert "always_show" not in inspect.getsource(mod._build_reporting_screen)
        assert "always_show" not in inspect.getsource(mod._build_reporting_theme_screen)

    def test_sprint_select_view_renders_toggle_rows(self):
        from yeaboi.reporting.sprints import SprintRef

        sprints = [
            SprintRef("Sprint 5", "2026-06-01", "2026-06-14", "jira", in_quarter=False),
            SprintRef("Sprint 6", "2026-07-01", "2026-07-14", "jira", in_quarter=True),
        ]
        data = {
            "view": "sprint_select",
            "quarter_label": "Q3 2026",
            "sprints": sprints,
            "sprint_cursor": 1,
            "sprint_checked": {1},
            "actions": ["Generate Report", "Back"],
        }
        panel = _build_reporting_screen(data, width=100, height=32, action_sel=0)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "REPORTING SETUP" in out
        assert "Q3 2026" in out
        assert "Sprint 6" in out
        assert "●" in out and "○" in out  # one checked, one not (toggle-row dots)
        assert "in quarter" in out
        assert "toggles" in out  # the hint line

    def test_sprint_select_empty(self):
        data = {
            "view": "sprint_select",
            "quarter_label": "Q3 2026",
            "sprints": [],
            "sprint_cursor": 0,
            "sprint_checked": set(),
            "actions": ["Generate Report", "Back"],
        }
        panel = _build_reporting_screen(data, width=100, height=30)
        assert isinstance(panel, Panel)
        assert "No sprints found" in _render(panel)

    def test_theme_select_renders_swatches_and_custom_tag(self):
        data = {
            "view": "theme_select",
            "theme": "midnight",
            "theme_names": list(_PALETTES),
            "palettes": _PALETTES,
            "theme_cursor": 4,
            "actions": ["Select", "Back"],
        }
        panel = _build_reporting_screen(data, width=100, height=36, action_sel=0)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "REPORTING SETUP" in out and "THEME" in out
        assert "midnight" in out and "corporate" in out
        assert "custom" in out  # the fifth palette is tagged as user-defined
        assert "reporting_themes.json" in out  # where to add your own

    def test_scrollable_long_detail(self):
        data = {
            "view": "detail",
            "report": _report(n_items=60),
            "detail_title": "X",
            "actions": ["Export", "Theme", "Back"],
        }
        panel = _build_reporting_screen(data, width=100, height=24, scroll_offset=5)
        assert isinstance(panel, Panel)
        out = _render(panel)
        # Long content: the scrollbar track appears, and the overflow cap note renders
        # somewhere in the scroll range (build must stay bounded and not raise).
        assert "┃" in out or "│" in out

    def test_picker_shows_sources_summary(self):
        data = {
            "view": "picker",
            "periods": [("last_week", "Last week", "hint")],
            "selected_idx": 0,
            "theme": "midnight",
            "palettes": _PALETTES,
            "sources_summary": "Sources: Jira + Azure DevOps  ·  Code: GitHub  ·  Docs: —",
            "actions": ["Generate Report", "Sources", "Theme", "Back"],
        }
        out = _render(_build_reporting_screen(data, width=110, height=36), width=110)
        assert "Sources: Jira + Azure DevOps" in out
        assert "Code: GitHub" in out


class TestExportBanner:
    """Regression for the invisible export feedback: the status message must render
    as a PINNED banner outside the scroll viewport, so it is visible even when the
    reader has scrolled deep into the report."""

    def test_message_visible_when_scrolled_down(self):
        data = {
            "view": "detail",
            "report": _report(n_items=60),
            "detail_title": "X",
            "message": "Exported PowerPoint to ~/.yeaboi/exports/reporting/demo.pptx",
            "actions": ["Export", "Theme", "Back"],
        }
        out = _render(_build_reporting_screen(data, width=100, height=24, scroll_offset=40))
        assert "Exported PowerPoint" in out

    def test_fixed_height_with_and_without_banner(self):
        """The banner must not push the button border off the fixed-height panel."""
        for message in ("", "Exported to ~/exports  (Markdown + HTML + slides)"):
            data = {
                "view": "detail",
                "report": _report(n_items=30),
                "detail_title": "X",
                "message": message,
                "actions": ["Export", "Theme", "Back"],
            }
            out = _render(_build_reporting_screen(data, width=100, height=28))
            lines = [ln for ln in out.splitlines() if ln.strip()]
            assert len(lines) == 28, f"panel height drifted with message={message!r}"
            assert lines[-1].strip().startswith("╰"), "bottom border must stay on the panel"

    def test_short_terminal_drops_banner_keeps_height(self):
        """When the viewport would collapse below 3 rows the banner is dropped so it
        can't push the buttons further off — and the panel height stays exact."""
        data = {
            "view": "detail",
            "report": _report(),
            "detail_title": "X",
            "message": "Exported PowerPoint to deck.pptx",
            "actions": ["Export", "Theme", "Back"],
        }
        out = _render(_build_reporting_screen(data, width=100, height=15))
        assert "Exported PowerPoint" not in out  # banner dropped, not squeezed in
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 15
        assert lines[-1].strip().startswith("╰")


class TestSupportingSignalsRows:
    def test_signals_section_renders(self):
        from yeaboi.agent.state import SupportingSignal

        report = _report()
        report = type(report)(
            **{
                **{f: getattr(report, f) for f in report.__dataclass_fields__},
                "supporting_signals": (
                    SupportingSignal(kind="pull_requests", source="github", count=12, samples=("Fix auth (#41)",)),
                    SupportingSignal(kind="doc_updates", source="confluence", count=3, samples=("Runbook",)),
                ),
            }
        )
        data = {"view": "detail", "report": report, "detail_title": "X", "actions": ["Export", "Back"]}
        out = _render(_build_reporting_screen(data, width=100, height=60))
        assert "Supporting signals" in out
        assert "Pull requests · GitHub" in out
        assert "12" in out
        assert "Fix auth (#41)" in out
        assert "Doc updates · Confluence" in out

    def test_no_signals_no_section(self):
        data = {"view": "detail", "report": _report(), "detail_title": "X", "actions": ["Export", "Back"]}
        assert "Supporting signals" not in _render(_build_reporting_screen(data, width=100, height=60))


class TestStyleScreen:
    """Render tests for the deck-style options view ("style_select")."""

    def _data(self, style=None, cursor: int = 0) -> dict:
        from yeaboi.reporting.style import DeckStyle

        return {
            "view": "style_select",
            "style": style or DeckStyle(),
            "style_cursor": cursor,
            "theme": "midnight",
            "palettes": _PALETTES,
            "actions": ["Save", "Reset", "Back"],
        }

    def test_all_option_labels_render(self):
        from yeaboi.reporting.style import STYLE_FIELDS

        out = _render(_build_reporting_screen(self._data(), width=100, height=48))
        for _field, label, _kind in STYLE_FIELDS:
            assert label in out
        assert "REPORTING SETUP" in out
        assert "reporting_prefs.json" in out  # footer names the persistence file
        # Buttons in order: Save (persist) first, then Reset, then Back.
        assert out.index("Save") < out.index("Reset") < out.rindex("Back")

    def test_default_values_shown(self):
        out = _render(_build_reporting_screen(self._data(), width=100, height=48))
        assert "theme default" in out  # color rows
        assert "Modern" in out  # font preset pretty label
        assert "detailed" in out
        assert "ask at export" in out  # content_fit pretty label, not raw "ask"
        assert "(none)" in out  # empty footer text
        assert "○ off" in out and "● on" in out  # bools both ways by default

    def test_content_fit_tight_renders_fixed_label(self):
        from yeaboi.reporting.style import DeckStyle

        out = _render(_build_reporting_screen(self._data(style=DeckStyle(content_fit="tight")), width=100, height=48))
        assert "fixed" in out
        assert "ask at export" not in out

    def test_focus_markers_follow_cursor(self):
        out = _render(_build_reporting_screen(self._data(cursor=2), width=100, height=48))
        lines = out.splitlines()
        focused = [ln for ln in lines if "‹" in ln]
        assert len(focused) == 1
        assert "Font" in focused[0]

    def test_custom_values_shown(self):
        from yeaboi.reporting.style import DeckStyle

        style = DeckStyle(title_color="#ff0000", font_family="classic", layout="compact", footer_text="ACME")
        out = _render(_build_reporting_screen(self._data(style=style), width=100, height=48))
        assert "#ff0000" in out
        assert "Classic serif" in out
        assert "compact" in out
        assert "ACME" in out

    def test_picker_shows_style_summary_line(self):
        data = {
            "view": "picker",
            "periods": [("last_week", "Last week", "the last 7 days")],
            "selected_idx": 0,
            "theme": "midnight",
            "palettes": _PALETTES,
            "actions": ["Generate Report", "Sources", "Theme", "Style", "Back"],
            "style_summary": "classic · compact layout",
        }
        out = _render(_build_reporting_screen(data, width=110, height=36))
        assert "Style: classic · compact layout" in out
