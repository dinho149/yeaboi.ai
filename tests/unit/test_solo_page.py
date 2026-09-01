"""Tests for the Weekly Review page loop (ui/mode_select/_solo.py).

Drive the loop with a scripted ``read_key``, a fake Live and a fake engine —
no terminal, no tracker, no LLM. The worker thread is replaced by one that
runs its target inline, so the progress screen never consumes script keys.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import ReviewAction, WeeklyReview
from yeaboi.ui.mode_select import _solo
from yeaboi.ui.shared import _music_bar


class _Live:
    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (100, 40)


class _InlineThread:
    """Runs the worker synchronously so the poll loop sees it already finished."""

    def __init__(self, target, *, name: str):
        self._target = target

    def start(self):
        self._target()

    def is_alive(self):
        return False

    def join(self):
        pass


def _keys(*sequence):
    remaining = list(sequence)

    def _read(timeout=None):
        return remaining.pop(0) if remaining else "esc"

    return _read


_CARRIED = [
    ReviewAction(id="aaaaaaaaaaaa", text="Ask ops for the staging keys", week_label="2026-W35"),
    ReviewAction(id="bbbbbbbbbbbb", text="Write the OAuth PRD", week_label="2026-W35"),
]


@pytest.fixture()
def page(monkeypatch, tmp_path):
    """The page with its stores, duck and worker thread faked out.

    Returns a dict the tests read: every screen ``data`` rendered (``views``),
    and the kwargs the engine was called with (``engine``).
    """
    from yeaboi.ui import mode_select

    seen: dict = {"views": [], "engine": []}
    monkeypatch.setattr(mode_select, "_ana_dbp", tmp_path / "sessions.db")
    monkeypatch.setattr(mode_select, "_duck_react", lambda *a, **k: None)
    monkeypatch.setattr(_solo, "_load_carried", lambda _db: list(_CARRIED))
    monkeypatch.setattr(_music_bar, "duck_working_thread", _InlineThread)

    def _fake_screen(data, **_kw):
        seen["views"].append(dict(data))
        return "panel"

    monkeypatch.setattr(_solo, "_build_solo_review_screen", _fake_screen)
    return seen


def _fake_engine(seen, *, review=None, error=None):
    def _run(**kwargs):
        seen["engine"].append(kwargs)
        if error is not None:
            raise error
        return review or WeeklyReview(week_label="2026-W36", summary="A steady week.")

    return _run


class TestGenerate:
    def test_generate_then_back_walks_carried_to_detail(self, page, monkeypatch):
        import yeaboi.solo.engine as engine

        monkeypatch.setattr(engine, "run_weekly_review", _fake_engine(page))

        _solo.run_solo_review_page(_Console(), _Live(), _keys("enter", "esc"), 0.001, True)

        views = [v["view"] for v in page["views"]]
        assert views[0] == "carried"
        assert "detail" in views
        assert len(page["engine"]) == 1
        assert page["views"][-1]["review"].week_label == "2026-W36"

    def test_marks_reach_the_engine_as_carried_statuses(self, page, monkeypatch):
        import yeaboi.solo.engine as engine

        monkeypatch.setattr(engine, "run_weekly_review", _fake_engine(page))

        # Space on the first action → done; down, Space, Space on the second → dropped.
        _solo.run_solo_review_page(_Console(), _Live(), _keys(" ", "down", " ", " ", "enter", "esc"), 0.001, True)

        (call,) = page["engine"]
        assert call["carried_statuses"] == {"aaaaaaaaaaaa": "done", "bbbbbbbbbbbb": "dropped"}
        assert call["on_progress"] is not None

    def test_marks_render_on_the_carried_view_before_the_run(self, page, monkeypatch):
        import yeaboi.solo.engine as engine

        monkeypatch.setattr(engine, "run_weekly_review", _fake_engine(page))

        _solo.run_solo_review_page(_Console(), _Live(), _keys(" ", "esc"), 0.001, True)

        last_carried = [v for v in page["views"] if v["view"] == "carried"][-1]
        assert [a.status for a in last_carried["carried"]] == ["done", "pending"]

    def test_engine_failure_stays_on_the_carried_view_with_a_message(self, page, monkeypatch):
        import yeaboi.solo.engine as engine

        monkeypatch.setattr(engine, "run_weekly_review", _fake_engine(page, error=RuntimeError("no tracker")))

        _solo.run_solo_review_page(_Console(), _Live(), _keys("enter", "esc"), 0.001, True)

        views = [v["view"] for v in page["views"]]
        assert "detail" not in views
        assert page["views"][-1]["message"] == "Review failed: no tracker"

    def test_back_button_leaves_without_running(self, page, monkeypatch):
        import yeaboi.solo.engine as engine

        monkeypatch.setattr(engine, "run_weekly_review", _fake_engine(page))

        _solo.run_solo_review_page(_Console(), _Live(), _keys("right", "enter"), 0.001, True)

        assert page["engine"] == []

    def test_unreadable_carried_store_still_opens_the_page(self, page, monkeypatch):
        def _boom(_db):
            raise OSError("locked")

        monkeypatch.setattr(_solo, "_load_carried", _boom)

        _solo.run_solo_review_page(_Console(), _Live(), _keys("esc"), 0.001, True)

        assert page["views"][0]["carried"] == []


class TestDetailLoop:
    def test_scroll_keys_and_back(self, page, monkeypatch):
        review = WeeklyReview(week_label="2026-W36", summary="A steady week.")

        _solo._detail_loop(_Console(), _Live(), _keys("down", "left", "enter"), 0.001, True, review)

        views = [v["view"] for v in page["views"]]
        assert views and set(views) == {"detail"}
        # "left" wraps the focus from Export to Back, and Enter leaves.
        assert len(views) == 3
