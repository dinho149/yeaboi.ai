"""Tests for src/yeaboi/ui/mode_select/_agents.py — the shared threaded page loop.

The loop runs the real worker thread but with fake console/live/read_key and a
recording build_screen, so each test asserts on the kwargs the screen builder
was handed frame by frame: drained progress, the instant-open stale report,
the refresh banner state, and the non-destructive error path.
"""

import threading
import time
from dataclasses import dataclass

import pytest

from yeaboi.agent.state import AgentUsageReport
from yeaboi.agentwatch import setup as agents_setup
from yeaboi.agentwatch.store import AgentWatchStore
from yeaboi.ui.mode_select import _agents

_MAX_FRAMES = 2000  # safety valve: a test bug must fail, not hang


def _tick() -> str:
    """One polled frame: yield the GIL briefly so the worker thread can run."""
    time.sleep(0.001)
    return "x"


@dataclass(frozen=True)
class _FakeArtifact:
    name: str = "fresh"
    warnings: tuple = ()
    generated_at: str = "2026-08-08T10:00:00+00:00"


class _Screens:
    """Records every build_screen call; returns a placeholder renderable."""

    def __init__(self):
        self.calls = []

    def __call__(self, artifact, **kwargs):
        self.calls.append((artifact, kwargs))
        return "screen"

    @property
    def last(self):
        return self.calls[-1]


class _Live:
    def update(self, renderable):
        pass


class _Console:
    size = (100, 40)


def _run_page(read_key, run_engine, screens, monkeypatch):
    """Drive the shared loop with the usage mode's table row, faked end to end."""
    mode = agents_setup.require("agent-usage")
    monkeypatch.setattr(_agents, "_screen_builder", lambda _mode: screens)
    monkeypatch.setattr(agents_setup, "run", lambda _mode, on_progress: run_engine(on_progress))
    monkeypatch.setattr(agents_setup, "failure_artifact", lambda _mode, exc: _FakeArtifact(name="failure"))
    _agents._run_agent_page(mode, _Console(), _Live(), read_key, 0.0, True)


def _seed_stale(db_path):
    stale = AgentUsageReport(period_start="2026-07-01", period_end="2026-07-31", total_cost_usd=9.99)
    with AgentWatchStore(db_path) as store:
        store.record_report("usage", stale, key_date="2026-07-01")
    return stale


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    return db


@pytest.fixture
def stale_db(empty_db):
    _seed_stale(empty_db)
    return empty_db


class TestDrain:
    def test_a_backlog_folds_into_one_frame(self, empty_db, monkeypatch):
        from yeaboi.analysis.progress import send_component_progress

        emitted = threading.Event()
        release = threading.Event()

        def run_engine(on_progress):
            for i in range(500):
                send_component_progress(
                    on_progress, component_id="scan", label="Scanning", status="running", current=i + 1, total=500
                )
            emitted.set()
            release.wait(5)
            return _FakeArtifact()

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            if frames == 1:
                emitted.wait(5)  # let the whole backlog queue up before the next frame
                return "x"
            release.set()
            return "esc"

        _run_page(read_key, run_engine, screens, monkeypatch)
        artifact, kwargs = screens.last
        assert artifact is None
        # 500 queued events → one latest-per-phase entry, current already at the tail.
        assert len(kwargs["progress"]) == 1
        assert kwargs["progress"][0]["current"] == 500


class TestInstantOpen:
    def test_stale_report_shows_before_the_refresh_lands(self, stale_db, monkeypatch):
        release = threading.Event()

        def run_engine(on_progress):
            release.wait(5)
            return _FakeArtifact(name="fresh")

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            if frames >= _MAX_FRAMES:
                return "esc"
            artifact, kwargs = screens.last
            if frames == 1:
                release.set()
                return _tick()
            if getattr(artifact, "name", "") == "fresh" and not kwargs["refreshing"]:
                return "esc"
            return _tick()

        _run_page(read_key, run_engine, screens, monkeypatch)
        first_artifact, first_kwargs = screens.calls[0]
        # Frame one is the finished screen with the saved report + refresh banner —
        # never the loading screen.
        assert isinstance(first_artifact, AgentUsageReport)
        assert first_artifact.total_cost_usd == 9.99
        assert first_kwargs["refreshing"] is True
        assert first_kwargs["as_of"]
        last_artifact, last_kwargs = screens.last
        assert getattr(last_artifact, "name", "") == "fresh"
        assert last_kwargs["refreshing"] is False
        assert last_kwargs["as_of"] == ""

    def test_failed_refresh_keeps_the_stale_report(self, stale_db, monkeypatch):
        def run_engine(on_progress):
            raise RuntimeError("boom")

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            if frames >= _MAX_FRAMES:
                return "esc"
            _artifact, kwargs = screens.last
            if "Refresh failed" in kwargs.get("notice", ""):
                return "esc"
            return _tick()

        _run_page(read_key, run_engine, screens, monkeypatch)
        artifact, kwargs = screens.last
        assert isinstance(artifact, AgentUsageReport), "the stale report must survive a failed refresh"
        assert artifact.total_cost_usd == 9.99
        assert "Refresh failed" in kwargs["notice"]
        assert kwargs["refreshing"] is False

    def test_rerun_while_refreshing_is_a_notice_not_a_second_worker(self, stale_db, monkeypatch):
        release = threading.Event()
        runs = []

        def run_engine(on_progress):
            runs.append(1)
            release.wait(5)
            return _FakeArtifact(name="fresh")

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            if frames >= _MAX_FRAMES:
                return "esc"
            if frames == 1:
                return "r"  # re-run while the initial refresh is still in flight
            _artifact, kwargs = screens.last
            if kwargs.get("notice") == "Already refreshing…":
                release.set()
                return "esc"
            return _tick()

        _run_page(read_key, run_engine, screens, monkeypatch)
        assert len(runs) == 1
        _artifact, kwargs = screens.last
        assert kwargs["notice"] == "Already refreshing…"


class TestFirstRun:
    def test_no_history_shows_the_loading_screen(self, empty_db, monkeypatch):
        release = threading.Event()

        def run_engine(on_progress):
            release.wait(5)
            return _FakeArtifact(name="fresh")

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            release.set()
            if frames >= _MAX_FRAMES:
                return "esc"
            artifact, _kwargs = screens.last
            return "esc" if artifact is not None else _tick()

        _run_page(read_key, run_engine, screens, monkeypatch)
        first_artifact, first_kwargs = screens.calls[0]
        assert first_artifact is None
        assert first_kwargs["progress"] == []
        assert getattr(screens.last[0], "name", "") == "fresh"

    def test_engine_crash_with_no_history_shows_the_failure_artifact(self, empty_db, monkeypatch):
        def run_engine(on_progress):
            raise RuntimeError("boom")

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            if frames >= _MAX_FRAMES:
                return "esc"
            artifact, _kwargs = screens.last
            return "esc" if artifact is not None else _tick()

        _run_page(read_key, run_engine, screens, monkeypatch)
        assert getattr(screens.last[0], "name", "") == "failure"


class TestHistoryErrors:
    def test_unreadable_store_falls_back_to_the_loading_screen(self, stale_db, monkeypatch):
        def _boom(_path):
            raise RuntimeError("db locked")

        monkeypatch.setattr("yeaboi.agentwatch.store.AgentWatchStore", _boom)
        release = threading.Event()

        def run_engine(on_progress):
            release.wait(5)
            return _FakeArtifact(name="fresh")

        screens = _Screens()
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            release.set()
            if frames >= _MAX_FRAMES:
                return "esc"
            artifact, _kwargs = screens.last
            return "esc" if artifact is not None else _tick()

        _run_page(read_key, run_engine, screens, monkeypatch)
        first_artifact, first_kwargs = screens.calls[0]
        assert first_artifact is None, "a broken history store must cold-start, not crash the page"
        assert "progress" in first_kwargs
        assert getattr(screens.last[0], "name", "") == "fresh"
