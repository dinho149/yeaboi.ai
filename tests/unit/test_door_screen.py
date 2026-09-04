"""Tests for the door — Projects vs Sessions — (_screens_door.py) and its run loop."""

from __future__ import annotations

import pytest
from rich.console import Console

from yeaboi.projects import active
from yeaboi.ui.mode_select.screens._screens_door import (
    _CARD_ROWS,
    _DOOR_CARDS,
    _GUTTER_COLS,
    _HINT_ROWS,
    _build_door_screen,
    _door_bounds,
    door_at_pos,
    door_index,
    world_theme,
)
from yeaboi.ui.shared._components import AGENTS_THEME, SOLO_THEME, TEAM_THEME


def _plain(width=110, height=40, selected=0, **kwargs) -> str:
    console = Console(width=width, height=height, force_terminal=False)
    panel = _build_door_screen(selected, width=width, height=height, **kwargs)
    rows = console.render_lines(panel, console.options.update(height=height), pad=True)
    return "\n".join("".join(seg.text for seg in row) for row in rows)


def _styled(width=110, height=40, selected=0, **kwargs) -> str:
    console = Console(width=width, height=height, force_terminal=True, color_system="truecolor")
    with console.capture() as cap:
        console.print(_build_door_screen(selected, width=width, height=height, **kwargs))
    return cap.get()


class TestCards:
    def test_two_doors_in_order(self):
        assert [c["key"] for c in _DOOR_CARDS] == ["projects", "sessions"]

    def test_index_by_key(self):
        assert door_index("projects") == 0 and door_index("sessions") == 1
        assert door_index("nope") == 0

    def test_world_theme(self):
        assert world_theme("solo") is SOLO_THEME
        assert world_theme("team") is TEAM_THEME
        assert world_theme("agents") is AGENTS_THEME
        assert world_theme("") is TEAM_THEME

    def test_card_height_matches_the_layout_constant(self):
        from yeaboi.ui.mode_select.screens._screens_door import _card

        console = Console(width=50, height=40, force_terminal=False)
        card = _card(_DOOR_CARDS[0], selected=True, theme=TEAM_THEME, shimmer_tick=0.0, intro=1.0)
        rows = console.render_lines(card, console.options.update(width=50), pad=False)
        assert len(rows) == _CARD_ROWS


class TestRender:
    @pytest.mark.parametrize(("width", "height"), [(84, 40), (110, 40)])
    def test_fits_and_names_both_doors(self, width, height):
        out = _plain(width, height)
        assert out.count("\n") + 1 == height
        assert "Everything inside shares context" in out
        assert "One-off run, nothing carried over" in out
        assert "…" not in out  # no verb or detail is ellipsized at the floor width

    def test_heading_rides_the_top_border(self):
        out = _plain(84, 40)
        assert "How do we work today?" in out.splitlines()[0]

    def test_hint_row_names_the_keys(self):
        out = _plain(84, 40)
        assert "enter choose" in out and "esc back" in out and "q quit" in out

    def test_never_carries_the_other_screens_markers(self):
        out = _plain(110, 40)
        for word in ("working with", "changelog", "Tip:", "channel"):
            assert word not in out

    @pytest.mark.parametrize(("world", "theme"), [("solo", SOLO_THEME), ("team", TEAM_THEME), ("agents", AGENTS_THEME)])
    def test_selected_title_wears_the_worlds_accent(self, world, theme):
        out = _styled(84, 40, world=world)
        r, g, b = (int(v) for v in theme.accent_bright[4:-1].split(","))
        assert f"38;2;{r};{g};{b}" in out

    def test_active_project_line_shows_when_given(self):
        assert "Active: Apollo" in _plain(84, 40, active_name="Apollo")
        assert "Active:" not in _plain(84, 40)

    def test_intro_holds_the_wordmarks_back(self):
        early = _plain(84, 40, intro=0.0)
        assert "█" not in early
        assert "Everything inside shares context" in early  # the verb is there from the first frame


class TestHitTest:
    def test_left_half_is_projects_right_half_is_sessions(self):
        assert door_at_pos(100, 40, row=20, col=10) == 0
        assert door_at_pos(100, 40, row=20, col=90) == 1

    def test_region_boundaries_match_the_shared_bounds(self):
        bounds = _door_bounds(100)
        (s0, e0), (s1, e1) = bounds
        assert door_at_pos(100, 40, row=20, col=e0) == 0
        assert door_at_pos(100, 40, row=20, col=s1) == 1
        assert s1 - e0 - 1 == _GUTTER_COLS

    def test_the_gutter_is_dead(self):
        (_s0, e0), (s1, _e1) = _door_bounds(100)
        for col in range(e0 + 1, s1):
            assert door_at_pos(100, 40, row=20, col=col) is None

    def test_the_frame_padding_counts_for_the_nearest_card(self):
        assert door_at_pos(100, 40, row=20, col=1) == 0
        assert door_at_pos(100, 40, row=20, col=100) == 1

    def test_border_and_hint_rows_are_dead(self):
        assert door_at_pos(100, 40, row=1, col=50) is None
        assert door_at_pos(100, 40, row=2, col=50) is None
        assert door_at_pos(100, 40, row=40, col=10) is None
        for row in range(40 - _HINT_ROWS, 41):
            assert door_at_pos(100, 40, row=row, col=10) is None


class TestChromeStamps:
    def test_no_corner_duck_and_no_music_but_a_back_tab(self):
        panel = _build_door_screen(0, width=84, height=40)
        assert getattr(panel, "_no_companion_duck", False) is True
        assert getattr(panel, "_no_music", False) is True
        assert getattr(panel, "_no_back_hint", False) is False


# ---------------------------------------------------------------------------
# The run loop (Phase 0b of select_mode)
# ---------------------------------------------------------------------------


class _Live:
    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (84, 40)


def _keys(*sequence):
    remaining = list(sequence)

    def _read(timeout=None):
        return remaining.pop(0) if remaining else "q"

    return _read


def _run(*keys, preselected="sessions", world="team"):
    import yeaboi.ui.mode_select as ms

    live = _Live()
    result = ms._run_door_screen(_Console(), live, _keys(*keys), True, world=world, preselected=preselected)
    return result, live


@pytest.fixture(autouse=True)
def _no_project(tmp_path, monkeypatch):
    import yeaboi.ui.mode_select as ms

    monkeypatch.setattr(ms, "_ana_dbp", tmp_path / "sessions.db")
    active.set_active_project("")
    yield
    active.set_active_project("")


class TestDoorLoop:
    def test_enter_picks_the_preselected_door(self):
        assert _run("enter")[0] == "sessions"
        assert _run("enter", preselected="projects")[0] == "projects"

    @pytest.mark.parametrize("key", ["left", "right", "up", "down", "tab"])
    def test_arrows_and_tab_cycle_between_the_two(self, key):
        assert _run(key, "enter")[0] == "projects"

    def test_esc_goes_back_to_the_split(self):
        result, live = _run("esc")
        assert result is None and live.frames

    def test_q_quits(self):
        assert _run("q")[0] == "quit"

    def test_a_click_selects_then_activates(self):
        (_s0, e0), (s1, _e1) = _door_bounds(84)
        assert _run(f"click:{s1 + 2}:20", "enter")[0] == "sessions"
        assert _run(f"click:{e0 - 2}:20", f"click:{e0 - 2}:20")[0] == "projects"

    def test_a_dead_click_changes_nothing(self):
        assert _run("click:40:1", "enter")[0] == "sessions"

    def test_n_opens_niko_and_stays(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        opened = []
        monkeypatch.setattr(ms, "_open_niko", lambda *a, **k: opened.append(True))
        assert _run("n", "enter")[0] == "sessions"
        assert opened == [True]

    def test_names_the_active_project(self, tmp_path):
        from yeaboi.projects.engine import create_project

        pid = create_project("Apollo", db_path=tmp_path / "sessions.db")["project_id"]
        active.set_active_project(pid)
        _result, live = _run("esc")
        console = Console(width=84, height=40, force_terminal=False)
        rows = console.render_lines(live.frames[-1], console.options.update(height=40), pad=True)
        assert "Active: Apollo" in "\n".join("".join(seg.text for seg in row) for row in rows)


# ---------------------------------------------------------------------------
# The scope line the menu carries on its top border
# ---------------------------------------------------------------------------


class TestScopeLine:
    def test_sessions_door_is_a_one_off(self):
        import yeaboi.ui.mode_select as ms

        assert ms._scope_line("sessions", "team") == "Session · one-off, unscoped"

    def test_projects_door_without_a_project_is_a_one_off(self):
        import yeaboi.ui.mode_select as ms

        assert ms._scope_line("projects", "team") == "Session · one-off, unscoped"

    def test_projects_door_names_the_project(self, tmp_path):
        import yeaboi.ui.mode_select as ms
        from yeaboi.projects.engine import create_project, set_project_defaults

        db = tmp_path / "sessions.db"
        pid = create_project("Apollo", db_path=db)["project_id"]
        active.set_active_project(pid)
        assert ms._scope_line("projects", "team") == "Apollo · every run here shares context"
        assert ms._scope_line("projects", "agents") == (
            "Apollo · no repo path yet — yeaboi project set-defaults --repo <path>"
        )
        set_project_defaults(pid, {"repo_path": "/srv/apollo"}, db_path=db)
        assert ms._scope_line("projects", "agents") == "Apollo · agents in /srv/apollo"

    @pytest.fixture(autouse=True)
    def _still_chrome(self, monkeypatch):
        # The menu's tabs ease in and out per render from module state another
        # test may have left mid-glide; rest them so two renders draw the same.
        import yeaboi.ui.shared._music_bar as mb

        for name in ("_back_presence", "_controls_presence", "_controls_tab_presence"):
            monkeypatch.setattr(mb, name, 0.0)
        monkeypatch.setattr(mb, "_back_retracting", False)
        monkeypatch.setattr(mb, "_controls_open", False)
        quiet = {"update_available": False, "current": "0.0.0", "latest": "", "upgrade_command": "", "is_dev": False}
        monkeypatch.setattr("yeaboi.update_check.get_update_status", lambda: quiet)
        monkeypatch.setattr("yeaboi.update_check.is_fresh_restart", lambda: False)

    def test_the_menu_draws_it_on_the_top_border_without_moving_a_row(self):
        from yeaboi.ui.mode_select.screens._screens import _build_mode_screen, mode_at_row, selected_title_offset

        console = Console(width=110, height=40, force_terminal=False)
        panel = _build_mode_screen(0, width=110, height=40, scope="Apollo · every run here shares context")
        rows = console.render_lines(panel, console.options.update(height=40), pad=True)
        text = "\n".join("".join(seg.text for seg in row) for row in rows)
        assert "Apollo · every run here shares context" in text.splitlines()[0]
        assert len(rows) == 40
        # Zero rows: the body under the border is byte-identical with and without it.
        bare = console.render_lines(_build_mode_screen(0, width=110, height=40), console.options.update(height=40))
        assert [[seg.text for seg in row] for row in rows[1:]] == [[seg.text for seg in row] for row in bare[1:]]
        # And the click map + lift offset never learn about it (they take no scope).
        import inspect

        assert "scope" not in inspect.signature(mode_at_row).parameters
        assert "scope" not in inspect.signature(selected_title_offset).parameters

    def test_every_scope_line_fits_the_top_border_at_the_floor(self):
        from yeaboi.ui.mode_select.screens._screens import _build_mode_screen

        console = Console(width=84, height=40, force_terminal=False)
        for scope in (
            "Session · one-off, unscoped",
            "Apollo · every run here shares context",
            "Apollo · no repo path yet — yeaboi project set-defaults --repo <path>",
        ):
            rows = console.render_lines(
                _build_mode_screen(0, width=84, height=40, scope=scope), console.options.update(height=40)
            )
            assert scope in "".join(seg.text for seg in rows[0])

    def test_no_scope_means_no_title(self):
        from yeaboi.ui.mode_select.screens._screens import _build_mode_screen

        console = Console(width=110, height=40, force_terminal=False)
        rows = console.render_lines(_build_mode_screen(0, width=110, height=40), console.options.update(height=40))
        assert "·" not in "".join(seg.text for seg in rows[0])
