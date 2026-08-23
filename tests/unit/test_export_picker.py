"""Tests for the shared export destination picker (ui/shared/_export_picker)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.panel import Panel

# The menu's *rules* moved to yeaboi.exporting so every surface reads one
# answer; the picker here is the terminal's rendering of them.
from yeaboi.exporting import available_destinations, destination_description
from yeaboi.ui.shared._export_picker import _build_export_picker_screen, pick_export_destination


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "NOTION_TOKEN",
        "NOTION_ROOT_PAGE_ID",
        "NOTION_EXPORT_PARENT_PAGE_ID",
        "YEABOI_HOME",
        "CONFLUENCE_SPACE_KEY",
        "CONFLUENCE_EXPORT_PARENT_PAGE_ID",
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_EMAIL",
        "CONFLUENCE_API_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


class TestAvailableDestinations:
    def test_files_and_copy_by_default(self):
        # Files + Copy are always available (no config needed).
        assert available_destinations() == ["files", "copy"]

    def test_notion_when_token_set(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")
        assert available_destinations() == ["files", "copy", "notion"]

    def test_confluence_via_own_creds(self, monkeypatch):
        monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://x.atlassian.net")
        monkeypatch.setenv("CONFLUENCE_EMAIL", "a@b.c")
        monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
        assert available_destinations() == ["files", "copy", "confluence"]

    def test_confluence_via_jira_fallback(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@b.c")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        assert available_destinations() == ["files", "copy", "confluence"]

    def test_partial_confluence_creds_not_offered(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
        assert available_destinations() == ["files", "copy"]

    def test_copy_is_always_present(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")
        assert "copy" in available_destinations()


class TestPickerScreen:
    def test_returns_panel(self):
        result = _build_export_picker_screen(mode="standup", labels=["Files", "Back"], selected=0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_warning_popup_rendered(self):
        from io import StringIO

        from rich.console import Console

        panel = _build_export_picker_screen(
            mode="planning",
            labels=["Files", "Notion", "Back"],
            selected=1,
            warning="Add a Notion page in Setup",
            width=80,
            height=24,
        )
        buf = StringIO()
        Console(file=buf, width=80, legacy_windows=False).print(panel)
        assert "Add a Notion page in Setup" in buf.getvalue()

    def test_unknown_mode_falls_back(self):
        result = _build_export_picker_screen(mode="???", labels=["Files"], selected=0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_warning_actions_replace_buttons(self):
        from io import StringIO

        from rich.console import Console

        panel = _build_export_picker_screen(
            mode="planning",
            labels=["Files", "Notion", "Back"],
            selected=1,
            warning="Add a Notion page in Setup",
            warning_actions=["Open Setup", "Back"],
            warning_sel=0,
            width=100,
            height=24,
        )
        buf = StringIO()
        Console(file=buf, width=100, legacy_windows=False).print(panel)
        out = buf.getvalue()
        assert "Open Setup" in out
        assert "Notion" not in out.replace("Add a Notion page in Setup", "")


class TestDestDescription:
    def test_files_shows_export_dir(self, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "EXPORTS_DIR", Path("/data/exports"))
        assert (
            destination_description("files", mode="standup", label="Files") == "Markdown + HTML → /data/exports/standup"
        )

    def test_files_abbreviates_home(self, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "EXPORTS_DIR", Path.home() / ".yeaboi" / "exports")
        assert (
            destination_description("files", mode="analysis", label="Files")
            == "Markdown + HTML → ~/.yeaboi/exports/analysis"
        )

    def test_notion_unconfigured_points_at_setup(self):
        assert "set it up" in destination_description("notion", mode="retro", label="Notion")

    def test_notion_exports_page_configured(self, monkeypatch):
        monkeypatch.setenv("NOTION_EXPORT_PARENT_PAGE_ID", "pg1")
        assert (
            destination_description("notion", mode="retro", label="Notion")
            == "Publish a page under your Notion exports page"
        )

    def test_notion_root_page_fallback_names_yeaboi_container(self, monkeypatch):
        monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root1")
        assert (
            destination_description("notion", mode="retro", label="Notion")
            == "Publish under the 🤙 yeaboi page in Notion"
        )

    def test_confluence_fallback_names_yeaboi_container(self, monkeypatch):
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "TEAM")
        assert (
            destination_description("confluence", mode="planning", label="Confluence")
            == "Publish under the 🤙 yeaboi page in space TEAM"
        )

    def test_confluence_exports_page_configured(self, monkeypatch):
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "TEAM")
        monkeypatch.setenv("CONFLUENCE_EXPORT_PARENT_PAGE_ID", "999")
        assert (
            destination_description("confluence", mode="planning", label="Confluence")
            == "Publish under your Confluence exports page in TEAM"
        )

    def test_confluence_unconfigured_points_at_setup(self):
        assert "set it up" in destination_description("confluence", mode="planning", label="Confluence")

    def test_back_and_extras(self):
        assert destination_description("back", mode="planning", label="Back") == "Return without exporting"
        assert destination_description("jira", mode="planning", label="Jira") == "Send to Jira"

    def test_subtitle_rendered_on_screen(self):
        from io import StringIO

        from rich.console import Console

        panel = _build_export_picker_screen(
            mode="analysis",
            labels=["Files", "Back"],
            selected=0,
            subtitle="Markdown + HTML → ~/.yeaboi/exports/analysis",
            width=100,
            height=24,
        )
        buf = StringIO()
        Console(file=buf, width=100, legacy_windows=False).print(panel)
        assert "Markdown + HTML → ~/.yeaboi/exports/analysis" in buf.getvalue()


class _FakeConsole:
    size = (100, 30)


class _FakeLive:
    def __init__(self):
        self.frames = 0

    def update(self, _panel):
        self.frames += 1


def _run(keys, *, mode="standup", extra_options=None, open_setup=None):
    it = iter(keys)

    def _read_key(timeout=None):
        return next(it)

    live = _FakeLive()
    result = pick_export_destination(
        live,
        _FakeConsole(),
        _read_key,
        0.05,
        True,
        mode=mode,
        extra_options=extra_options,
        open_setup=open_setup,
    )
    return result, live


class TestPickerLoop:
    def test_enter_on_files(self):
        result, live = _run(["enter"])
        assert result == "files"
        assert live.frames >= 1

    def test_back_returns_none(self):
        # Destinations are [Files, Copy] → sel 2 is Back.
        result, _ = _run(["right", "right", "enter"])
        assert result is None

    def test_esc_returns_none(self):
        result, _ = _run(["esc"])
        assert result is None

    def test_notion_selectable_with_exports_page(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")
        monkeypatch.setenv("NOTION_EXPORT_PARENT_PAGE_ID", "pg1")
        # [Files, Copy, Notion] → Notion is sel 2.
        result, _ = _run(["right", "right", "enter"])
        assert result == "notion"

    def test_notion_selectable_with_root_page_only(self, monkeypatch):
        # No dedicated exports page — the root page from setup is enough.
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")
        monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root1")
        result, _ = _run(["right", "right", "enter"])
        assert result == "notion"

    def test_notion_without_any_page_warns_and_stays(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")  # integrated, but no page at all
        # Enter on Notion → warning; any key clears it; back to Files; Enter → files.
        result, _ = _run(["right", "right", "enter", "x", "left", "left", "enter"])
        assert result == "files"

    def test_warning_survives_idle_timeout_ticks(self, monkeypatch):
        """read_key returns "" on every timeout tick — those must NOT dismiss the warning.

        Regression: the warning popup was cleared by the first idle tick (~one
        frame later), so blocked destinations looked like "nothing happened".
        The second "enter" below must be the acknowledgment (not a re-select of
        the blocked Notion row, which is what happens when the "" tick already
        ate the warning).
        """
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")  # integrated, but no page at all
        result, _ = _run(["right", "right", "enter", "", "", "enter", "left", "left", "enter"])
        assert result == "files"

    def test_idle_ticks_do_not_move_selection(self):
        # "" ticks before Enter must leave the selection on Files.
        result, _ = _run(["", "", "enter"])
        assert result == "files"

    def test_confluence_without_space_warns(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@b.c")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        result, _ = _run(["right", "right", "enter", "x", "esc"])
        assert result is None

    def test_confluence_selectable_with_space(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@b.c")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "TEAM")
        result, _ = _run(["right", "right", "enter"])
        assert result == "confluence"

    def test_extra_options_returned_lowercased(self):
        # [Files, Copy, Jira] → Jira is sel 2.
        result, _ = _run(["right", "right", "enter"], extra_options=["Jira"])
        assert result == "jira"

    def test_azure_devops_maps_to_azdevops(self):
        # [Files, Copy, Jira, Azure DevOps] → Azure DevOps is sel 3.
        result, _ = _run(["right", "right", "right", "enter"], extra_options=["Jira", "Azure DevOps"])
        assert result == "azdevops"

    def test_open_setup_configures_and_export_proceeds(self, monkeypatch):
        """Open Setup on the warning → wizard configures a page → export continues."""
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")  # integrated, but no page at all
        calls = []

        def _open_setup():
            calls.append(True)
            monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root1")

        # Enter on Notion (sel 2) → warning with buttons; Enter on "Open Setup" → configured → "notion".
        result, _ = _run(["right", "right", "enter", "enter"], open_setup=_open_setup)
        assert result == "notion"
        assert calls == [True]

    def test_open_setup_still_unconfigured_stays_in_picker(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")
        # Open Setup but don't configure → back to the picker; Esc exits.
        result, _ = _run(["right", "right", "enter", "enter", "esc"], open_setup=lambda: None)
        assert result is None

    def test_warning_back_button_skips_setup(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")
        calls = []
        # Warning up (Notion=sel 2) → right selects Back → Enter dismisses without
        # opening Setup; left,left back to Files → Enter exports files.
        result, _ = _run(
            ["right", "right", "enter", "right", "enter", "left", "left", "enter"],
            open_setup=lambda: calls.append(True),
        )
        assert result == "files"
        assert calls == []

    def test_warning_idle_ticks_keep_buttons(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "ntn_x")

        def _open_setup():
            monkeypatch.setenv("NOTION_EXPORT_PARENT_PAGE_ID", "pg1")

        # Idle "" ticks while the warning is up must not press or move anything.
        result, _ = _run(["right", "right", "enter", "", "", "enter"], open_setup=_open_setup)
        assert result == "notion"

    def test_timeout_typeerror_fallback(self):
        # A read_key that rejects the timeout kwarg (session-phase _key style).
        keys = iter(["enter"])

        def _read_key():
            return next(keys)

        live = _FakeLive()
        result = pick_export_destination(live, _FakeConsole(), _read_key, 0.05, True, mode="planning")
        assert result == "files"
