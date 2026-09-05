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
    monkeypatch.setattr(
        agents_setup, "run", lambda _mode, on_progress, project_path="", options=None: run_engine(on_progress)
    )
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
    # Every saved report is freshly recorded by these tests, so the page would
    # treat it as fresh and never start the worker: force the old behaviour.
    monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "0")
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


class TestProjectScope:
    """A scoped page skips the instant open and hands the repo to the engine."""

    def test_scoped_page_runs_fresh_with_the_repo(self, empty_db, monkeypatch):
        mode = agents_setup.require("agent-usage")
        screens = _Screens()
        seen: dict = {}
        monkeypatch.setattr(_agents, "_screen_builder", lambda _mode: screens)
        # A saved report exists; a scoped page must never open on it.
        monkeypatch.setattr(agents_setup, "latest_artifact", lambda kind: (_FakeArtifact(name="stale"), "2026-07-01"))

        def run(_mode, on_progress, **kw):
            seen.update(kw)
            return _FakeArtifact()

        monkeypatch.setattr(agents_setup, "run", run)
        frames = 0

        def read_key(timeout=None):
            nonlocal frames
            frames += 1
            assert frames < _MAX_FRAMES
            return "q" if screens.calls and screens.calls[-1][0] is not None else _tick()

        _agents._run_agent_page(mode, _Console(), _Live(), read_key, 0.0, True, project_path="/srv/app")
        assert seen == {"project_path": "/srv/app", "options": {"window_days": 30}}
        # No instant open: the stale machine-wide report never reaches a frame.
        assert all(getattr(artifact, "name", "") != "stale" for artifact, _k in screens.calls)
        assert all(kwargs.get("as_of", "") != "2026-07-01" for _a, kwargs in screens.calls)
        assert all(kwargs["scope"] == "/srv/app" for _a, kwargs in screens.calls)
        assert screens.last[0].name == "fresh"

    def test_the_repo_shows_in_the_subtitle(self):
        import io

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_usage_screen

        console = Console(file=io.StringIO(), width=100, height=40)
        console.print(_build_agent_usage_screen(None, width=100, height=40, scope="/srv/app"))
        assert "What your agents cost · /srv/app" in console.file.getvalue()

    def test_a_deep_repo_shows_its_tail_on_one_row_at_the_floor(self):
        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_usage_screen, _repo_tail

        assert _repo_tail("/srv/app") == "/srv/app"
        assert _repo_tail("/Users/dev/Documents/yeaboi/yeaboi.ai") == "…/yeaboi/yeaboi.ai"
        console = Console(width=84, height=40, force_terminal=False)
        for scope in ("/Users/dev/Documents/yeaboi/yeaboi.ai", "/x/" + "a" * 120):
            panel = _build_agent_usage_screen(None, width=84, height=40, scope=scope)
            rows = console.render_lines(panel, console.options.update(height=40), pad=True)
            text = ["".join(seg.text for seg in row) for row in rows]
            hits = [i for i, line in enumerate(text) if "What your agents cost" in line]
            assert len(hits) == 1
            assert text[hits[0] + 1].strip("│ ") == ""  # the blank under it is still blank: no second row
        assert "What your agents cost · …/yeaboi/yeaboi.ai" in "\n".join(
            "".join(seg.text for seg in row)
            for row in console.render_lines(
                _build_agent_usage_screen(None, width=84, height=40, scope="/Users/dev/Documents/yeaboi/yeaboi.ai"),
                console.options.update(height=40),
            )
        )

    def test_route_hands_the_repo_to_scoped_modes_only(self, monkeypatch):
        seen: list = []
        monkeypatch.setattr(_agents, "show_beta_notice", lambda *a, **k: True)
        monkeypatch.setattr(_agents, "_run_agent_page", lambda mode, *a, project_path="": seen.append(project_path))
        for key in ("agent-usage", "agent-security"):
            _agents.route_agent_mode(
                key,
                console=_Console(),
                live=_Live(),
                read_key=_tick,
                frame_time=0.0,
                supports_timeout=True,
                project_path="/srv/app",
            )
        assert seen == ["/srv/app", ""]


class TestSecurityIssueFlow:
    """Enter opens an issue, x marks it test data, esc comes back — no scan in between."""

    def _report(self):
        from yeaboi.agent.state import AgentSecurityReport, SecurityFinding, SecurityFix, SecurityIssue

        fix = SecurityFix(id="mark-test-data", kind="dismiss", label="Mark as test data")
        finding = SecurityFinding(
            category="risky_tool",
            pattern="curl-pipe-shell",
            severity="high",
            location="/t/gone.jsonl",
            line_no=5,
            key="risky_tool:curl-pipe-shell:/t/gone.jsonl:command",
            verdict="needs-decision",
            context="command",
            fixes=(fix,),
        )
        issue = SecurityIssue(
            id="risky_tool:curl-pipe-shell",
            category="risky_tool",
            pattern="curl-pipe-shell",
            title="An agent piped a download into a shell",
            verdict="needs-decision",
            severity="high",
            signals=1,
            sessions=1,
            files=1,
            finding_keys=(finding.key,),
            fixes=(fix,),
        )
        return AgentSecurityReport(
            scan_date="2026-08-08",
            posture="needs-attention",
            findings=(finding,),
            issues=(issue,),
            verdict_counts=(("needs-decision", 1),),
            verdict_line="One thing needs a decision.",
            generated_at="2026-08-08T10:00:00+00:00",
        )

    def test_enter_opens_the_issue_and_x_marks_it_test_data(self, empty_db, tmp_path, monkeypatch):
        from yeaboi.agentwatch import dismissals, security_checks
        from yeaboi.ui.mode_select.screens import _screens_agents

        monkeypatch.setattr(dismissals, "default_path", lambda: tmp_path / "allow.json")
        monkeypatch.setattr(security_checks, "_config_roots", lambda: (tmp_path / "dot-claude", tmp_path / "c.json"))
        screens = _Screens()
        issue_screens = _Screens()
        monkeypatch.setattr(_agents, "_screen_builder", lambda _mode: screens)
        monkeypatch.setattr(
            _screens_agents,
            "_build_agent_security_issue_screen",
            lambda report, issue, **kwargs: issue_screens((report, issue), **kwargs),
        )
        report = self._report()
        monkeypatch.setattr(agents_setup, "run", lambda _mode, on_progress, project_path="", options=None: report)
        keys = iter(["enter", "x", "esc", "esc"])

        def read_key(timeout=None):
            time.sleep(0.001)
            if not screens.calls or screens.calls[-1][0] is None:
                return None  # still loading
            return next(keys, "esc")

        mode = agents_setup.require("agent-security")
        _agents._run_agent_page(mode, _Console(), _Live(), read_key, 0.0, True)
        assert issue_screens.calls, "enter should have opened the issue screen"
        (opened, kwargs) = issue_screens.calls[0]
        assert opened[1].id == "risky_tool:curl-pipe-shell"
        assert "No replay" in kwargs["replay_status"]
        assert [d.key for d in dismissals.load()] == ["risky_tool:curl-pipe-shell:/t/gone.jsonl:command"]
        assert dismissals.load()[0].reason.startswith("test data")
