"""Tests for the Analysis board-setup gate (the old "no board configured" dead end)."""

import io

import pytest
from rich.console import Console

import yeaboi.ui.mode_select as ms
from yeaboi.ui.mode_select.screens._screens_secondary import (
    _BOARD_TRACKERS,
    _build_analysis_board_setup_screen,
    board_setup_fields,
    board_setup_ready,
)

_JIRA_FULL = {
    "JIRA_BASE_URL": "https://acme.atlassian.net",
    "JIRA_EMAIL": "dev@acme.com",
    "JIRA_API_TOKEN": "tok_abcdefghijkl",
    "JIRA_PROJECT_KEY": "ACME",
}


def _render(**kwargs) -> str:
    buf = io.StringIO()
    values = kwargs.pop("values", {})
    Console(file=buf, width=110, height=44, legacy_windows=False).print(
        _build_analysis_board_setup_screen(values, width=110, height=44, **kwargs)
    )
    return buf.getvalue()


class TestReady:
    def test_empty_is_not_ready(self):
        assert board_setup_ready(0, {}) is False
        assert board_setup_ready(1, {}) is False

    def test_all_required_present_is_ready(self):
        assert board_setup_ready(0, _JIRA_FULL) is True

    def test_optional_field_is_not_required(self):
        # Azure's Team field is optional — the other three are enough.
        assert board_setup_ready(
            1,
            {
                "AZURE_DEVOPS_ORG_URL": "https://dev.azure.com/acme",
                "AZURE_DEVOPS_PROJECT": "Platform",
                "AZURE_DEVOPS_TOKEN": "pat",
            },
        )

    def test_whitespace_only_does_not_count(self):
        assert board_setup_ready(0, {**_JIRA_FULL, "JIRA_EMAIL": "   "}) is False


class TestScreen:
    def test_empty_lists_what_is_still_needed(self):
        out = _render(values={})
        assert "Connect a board" in out
        assert "Still needed" in out
        for label in ("Jira Base URL", "Jira Email", "Jira API Token", "Project Key"):
            assert label in out

    def test_no_continue_button_until_ready(self):
        assert "Continue" not in _render(values={})
        assert "Continue" in _render(values=_JIRA_FULL)

    def test_ready_says_so(self):
        assert "All set" in _render(values=_JIRA_FULL)

    def test_token_is_masked(self):
        out = _render(values=_JIRA_FULL)
        assert "tok_abcdefghijkl" not in out  # never render a token in full
        assert "•" in out

    def test_unset_required_and_optional_read_differently(self):
        out = _render(values={}, tracker=1)
        assert "required" in out
        assert "optional" in out  # the Team field

    def test_tracker_switch_shows_both_and_brackets_the_active_one(self):
        jira = _render(values={}, tracker=0)
        azdo = _render(values={}, tracker=1)
        for out in (jira, azdo):
            for name in _BOARD_TRACKERS:
                assert name in out
        assert "[ Jira ]" in jira
        assert "[ Azure DevOps ]" in azdo

    def test_switching_tracker_switches_the_fields(self):
        assert "Organization URL" in _render(values={}, tracker=1)
        assert "Organization URL" not in _render(values={}, tracker=0)

    def test_editing_shows_the_buffer_not_the_saved_value(self):
        out = _render(values=_JIRA_FULL, editing=("JIRA_PROJECT_KEY", "NEW", 3))
        assert "NEW" in out

    def test_focused_field_shows_its_hint(self):
        out = _render(values={}, selected=2)  # the API token
        assert "id.atlassian.com" in out

    def test_message_overrides_the_status_line(self):
        out = _render(values=_JIRA_FULL, message="Nope, try again")
        assert "Nope, try again" in out
        assert "All set" not in out

    def test_is_not_a_raw_panel(self):
        # Every page must go through build_page_panel so it paints its own
        # background; a raw Panel would show the user's terminal theme through it.
        panel = _build_analysis_board_setup_screen({}, width=110, height=44)
        assert panel.style and "on " in str(panel.style)


class TestClickMapping:
    def _panel_and_console(self, tracker=0):
        console = Console(file=io.StringIO(), width=110, height=44, legacy_windows=False)
        panel = _build_analysis_board_setup_screen({}, tracker=tracker, width=110, height=44)
        return console, panel

    def test_each_field_row_maps_to_its_index(self):
        console, panel = self._panel_and_console()
        fields = board_setup_fields(0)
        hits = [ms._board_field_click(console, panel, 20, y, fields) for y in range(1, 45)]
        found = [h for h in hits if h is not None]
        assert found == list(range(len(fields)))  # one row each, in order

    def test_click_off_the_rows_hits_nothing(self):
        console, panel = self._panel_and_console()
        assert ms._board_field_click(console, panel, 20, 1, board_setup_fields(0)) is None

    def test_tracker_switch_maps_by_column(self):
        console, panel = self._panel_and_console()
        rows = [(x, y) for y in range(1, 45) for x in range(1, 40)]
        hits = {ms._board_tab_click(console, panel, x, y) for x, y in rows}
        assert 0 in hits and 1 in hits  # both options are clickable


class _Live:
    def __init__(self):
        self.panels = []

    def update(self, panel, *a, **k):
        self.panels.append(panel)


def _drive(keys, monkeypatch, *, env=None, jira_ok=False, azdo_ok=False):
    """Run the gate headlessly with a scripted key sequence.

    Returns (result, saved) where ``saved`` is the list of (env_var, value)
    writes the loop made through apply_config_value.
    """
    import yeaboi.azdevops_sync as azdevops_sync
    import yeaboi.config as config
    import yeaboi.jira_sync as jira_sync

    for var in (f["env_var"] for t in (0, 1) for f in board_setup_fields(t)):
        monkeypatch.delenv(var, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)

    saved: list[tuple[str, str]] = []

    def _apply(key, value):
        saved.append((key, value))
        monkeypatch.setenv(key, value)
        return None

    monkeypatch.setattr(config, "apply_config_value", _apply)
    monkeypatch.setattr(jira_sync, "is_jira_configured", lambda: jira_ok)
    monkeypatch.setattr(azdevops_sync, "is_azdevops_board_configured", lambda: azdo_ok)

    seq = iter(keys)
    console = Console(file=io.StringIO(), width=110, height=44, legacy_windows=False)
    result = ms._run_analysis_board_setup(_Live(), console, lambda **k: next(seq, "esc"), 0.0, True)
    return result, saved


class TestLoop:
    def test_esc_cancels(self, monkeypatch):
        result, saved = _drive(["esc"], monkeypatch)
        assert result == "cancel"
        assert saved == []

    def test_back_button_cancels(self, monkeypatch):
        # Nothing configured → the only button is Back, so down then Enter.
        result, _ = _drive(["down", "down", "down", "down", "enter"], monkeypatch)
        assert result == "cancel"

    def test_typing_a_value_saves_it(self, monkeypatch):
        result, saved = _drive(["enter", "A", "C", "M", "E", "enter", "esc"], monkeypatch)
        assert result == "cancel"
        assert saved == [("JIRA_BASE_URL", "ACME")]

    def test_esc_during_an_edit_discards_it_without_leaving(self, monkeypatch):
        result, saved = _drive(["enter", "X", "esc", "esc"], monkeypatch)
        assert result == "cancel"
        assert saved == []  # the edit was thrown away, not written

    def test_continue_returns_connected_once_the_board_check_passes(self, monkeypatch):
        result, _ = _drive(
            ["down", "down", "down", "down", "enter"],
            monkeypatch,
            env=_JIRA_FULL,
            jira_ok=True,
        )
        assert result == "connected"

    def test_continue_refuses_when_the_board_check_still_fails(self, monkeypatch):
        # Fields look filled but the real predicate disagrees — the loop must not
        # hand a broken config to the analysis flow, and must say why.
        import yeaboi.azdevops_sync as azdevops_sync
        import yeaboi.config as config
        import yeaboi.jira_sync as jira_sync

        for var in (f["env_var"] for t in (0, 1) for f in board_setup_fields(t)):
            monkeypatch.delenv(var, raising=False)
        for k, v in _JIRA_FULL.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(config, "apply_config_value", lambda *a: None)
        monkeypatch.setattr(jira_sync, "is_jira_configured", lambda: False)
        monkeypatch.setattr(azdevops_sync, "is_azdevops_board_configured", lambda: False)

        live = _Live()
        seq = iter(["down", "down", "down", "down", "enter", "esc"])
        console = Console(file=io.StringIO(), width=110, height=44, legacy_windows=False)
        result = ms._run_analysis_board_setup(live, console, lambda **k: next(seq, "esc"), 0.0, True)

        assert result == "cancel"
        buf = io.StringIO()
        Console(file=buf, width=110, height=44, legacy_windows=False).print(live.panels[-1])
        assert "doesn't look complete" in buf.getvalue()

    def test_tab_switches_tracker_and_resets_the_cursor(self, monkeypatch):
        live = _Live()
        seq = iter(["tab", "esc"])
        console = Console(file=io.StringIO(), width=110, height=44, legacy_windows=False)
        for var in (f["env_var"] for t in (0, 1) for f in board_setup_fields(t)):
            monkeypatch.delenv(var, raising=False)
        ms._run_analysis_board_setup(live, console, lambda **k: next(seq, "esc"), 0.0, True)
        buf = io.StringIO()
        Console(file=buf, width=110, height=44, legacy_windows=False).print(live.panels[-1])
        assert "Organization URL" in buf.getvalue()  # switched to Azure DevOps

    def test_masked_field_keeps_its_value_when_committed_empty(self, monkeypatch):
        # Enter on the token opens a blank buffer; committing it empty must not
        # wipe the existing token.
        result, saved = _drive(
            ["down", "down", "enter", "enter", "esc"],
            monkeypatch,
            env=_JIRA_FULL,
        )
        assert result == "cancel"
        assert saved == []


@pytest.mark.parametrize("tracker", [0, 1])
def test_every_tracker_renders_at_a_small_size(tracker):
    # The gate is the first thing Analysis shows, so it must not blow up on a
    # terminal that's merely small rather than unusable.
    out = _render(values={}, tracker=tracker)
    assert "Connect a board" in out
