"""Unit tests for the interactive scheduled standup run."""

from datetime import date

from yeaboi.agent.state import StandupReport
from yeaboi.standup import interactive


class TestHeadlessFallback:
    def test_non_tty_runs_headless(self, monkeypatch):
        monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(interactive.sys.stdout, "isatty", lambda: False)
        calls = {}

        def fake_run(session_id, channels=None, deliver=True, solo=False, db_path=None, today=None):
            calls["ran"] = session_id
            return StandupReport(session_id=session_id)

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run)
        rc = interactive.run_interactive_standup("s1")
        assert rc == 0
        assert calls["ran"] == "s1"


class TestInteractiveFlow:
    def _tty(self, monkeypatch):
        monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(interactive.sys.stdout, "isatty", lambda: True)

    def test_saves_update_then_generates(self, monkeypatch, tmp_path):
        self._tty(monkeypatch)
        db = tmp_path / "sessions.db"

        # First timed prompt returns an update; confirm returns "" (default yes);
        # the trailing _hold prompt returns None (timeout).
        prompts = iter(["I shipped the login flow", "", None])
        monkeypatch.setattr(interactive, "_timed_input", lambda prompt, timeout: next(prompts, None))
        monkeypatch.setattr("yeaboi.config.get_standup_user_name", lambda: "Omar")

        saved = {}

        class FakeStore:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def save_my_update(self, sid, d, member, text):
                saved.update(session=sid, member=member, text=text)

        monkeypatch.setattr("yeaboi.standup.store.StandupStore", FakeStore)

        report = StandupReport(session_id="s1", warnings=("Jira: authentication failed",))
        monkeypatch.setattr("yeaboi.standup.engine.run_standup", lambda *a, **k: report)

        rc = interactive.run_interactive_standup("s1", db_path=db, today=date(2026, 7, 10))
        assert rc == 0
        assert saved == {"session": "s1", "member": "Omar", "text": "I shipped the login flow"}

    def test_confirm_no_cancels(self, monkeypatch):
        self._tty(monkeypatch)
        # Skip update (None), then answer "n" to confirm → cancel, engine not called.
        prompts = iter([None, "n"])
        monkeypatch.setattr(interactive, "_timed_input", lambda prompt, timeout: next(prompts, None))

        def _should_not_run(*a, **k):
            raise AssertionError("run_standup must not be called when cancelled")

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", _should_not_run)
        rc = interactive.run_interactive_standup("s1")
        assert rc == 0

    def test_timeout_auto_proceeds(self, monkeypatch):
        self._tty(monkeypatch)
        # Both update and confirm time out (None) → proceed with generate.
        prompts = iter([None, None, None])
        monkeypatch.setattr(interactive, "_timed_input", lambda prompt, timeout: next(prompts, None))
        ran = {}

        def fake_run(*a, **k):
            ran["ok"] = True
            return StandupReport(session_id="s1")

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run)
        rc = interactive.run_interactive_standup("s1")
        assert rc == 0
        assert ran["ok"] is True
