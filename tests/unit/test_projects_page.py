"""Tests for the Projects TUI page loop (ui/mode_select/_projects.py).

Driven with a scripted ``read_key`` and a fake Live, the same shape the other
page-loop tests use — no terminal, no threads of our own.
"""

from __future__ import annotations

import pytest

from yeaboi.projects import active
from yeaboi.projects.engine import create_project
from yeaboi.ui.mode_select import _projects


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
        if timeout == 0.0:
            return ""
        return remaining.pop(0) if remaining else "esc"

    return _read


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    active.set_active_project("")
    yield {"db": db}
    active.set_active_project("")


def _run(keys) -> _Live:
    live = _Live()
    _projects.run_projects_page(_Console(), live, keys, 0.05, True)
    return live


def _render(panel) -> str:
    import io

    from rich.console import Console

    console = Console(file=io.StringIO(), width=84, height=40)
    console.print(panel)
    return console.file.getvalue()


class TestClose:
    def test_esc_closes_the_page(self, env):
        live = _run(_keys("esc"))
        assert live.frames

    def test_the_back_button_closes_it(self, env):
        create_project("Apollo", db_path=env["db"])
        # Set active → Archive → Back, then Enter.
        live = _run(_keys("right", "right", "enter"))
        assert "Apollo" in _render(live.frames[-1])


class TestSetActive:
    def test_enter_on_set_active_marks_the_project(self, env):
        create_project("Apollo", db_path=env["db"])
        _run(_keys("enter", "esc"))
        assert active.get_active_project().startswith("proj-")

    def test_enter_again_clears_it(self, env):
        create_project("Apollo", db_path=env["db"])
        _run(_keys("enter", "enter", "esc"))
        assert active.get_active_project() == ""

    def test_empty_page_offers_the_command_instead(self, env):
        live = _run(_keys("enter", "esc"))
        assert "yeaboi project create" in _render(live.frames[-1])
        assert active.get_active_project() == ""


class TestArchive:
    def test_archive_hides_the_row_and_clears_active(self, env):
        create_project("Apollo", db_path=env["db"])
        _run(_keys("enter", "right", "enter", "esc"))  # set active, then archive it
        assert active.get_active_project() == ""
