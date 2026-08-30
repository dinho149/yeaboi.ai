"""Unit tests for the Reporting engine pipeline (mocked LLM + activity)."""

import json

import pytest

from yeaboi.agent.state import DeliveredItem
from yeaboi.reporting import activity as activity_mod
from yeaboi.reporting import engine


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


class TestValidateWindowDates:
    """The window strings arrive verbatim from CLI flags and the MCP tool, and
    everything downstream compares them as strings — the ordering check here, the
    `day >= period_start` filter in context.py, and the period label."""

    def test_canonicalises_a_valid_but_non_canonical_spelling(self):
        from yeaboi.reporting.engine import _validate_window_dates

        assert _validate_window_dates("20260601", "2026-W27-2") == ("2026-06-01", "2026-06-30")

    def test_the_ordering_check_runs_on_the_canonical_form(self):
        """Lexicographically `"2026-09-01" < "20260818"` is True ('-' is 0x2D,
        '0' is 0x30), so a mixed-spelling window would pass a backwards range."""
        from yeaboi.reporting.engine import _validate_window_dates

        with pytest.raises(ValueError, match="before window_start"):
            _validate_window_dates("2026-09-01", "20260818")

    def test_empty_values_pass_through(self):
        from yeaboi.reporting.engine import _validate_window_dates

        assert _validate_window_dates("", "") == ("", "")

    @pytest.mark.parametrize("bad", ["next tuesday", "2026-13-45", "01/06/2026"])
    def test_junk_is_refused_with_the_field_name(self, bad):
        from yeaboi.reporting.engine import _validate_window_dates

        with pytest.raises(ValueError, match="window_start must be an ISO date"):
            _validate_window_dates(bad, "")


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {}


def _patch_llm(monkeypatch, content):
    """Make the engine's single LLM call return ``content`` and report configured."""
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
    monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
    monkeypatch.setattr(
        "yeaboi.agent.llm.get_llm",
        lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(content)})(),
    )


def _patch_activity(monkeypatch, items=(), sprints=(), warnings=()):
    monkeypatch.setattr(
        activity_mod,
        "gather_delivered_work",
        lambda period, **kw: (list(items), list(sprints), list(warnings)),
    )


@pytest.fixture(autouse=True)
def _no_export(monkeypatch):
    # Keep tests off the real ~/.scrum-agent export dir.
    monkeypatch.setattr("yeaboi.reporting.export.export_report", lambda *a, **k: {})


def _items(n=2):
    return [
        DeliveredItem(key=f"P-{i}", title=f"feature {i}", status="Done", source="jira", assignee="Ada")
        for i in range(n)
    ]


class TestRunDeliveryReport:
    def test_happy_path_parses_llm(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=_items(3), sprints=["Sprint 5"])
        _patch_llm(
            monkeypatch,
            json.dumps(
                {
                    "headline": "Big wins this sprint.",
                    "executive_summary": "We shipped a lot.",
                    "themes": [{"title": "Security", "outcomes": ["SSO", "MFA"]}],
                    "highlights": ["SSO live"],
                    "emoji_theme": {"headline": "🔐", "highlights": "⭐"},
                }
            ),
        )
        report = engine.run_delivery_report("last_sprint", session_id="", db_path=db_path)
        assert report.headline == "Big wins this sprint."
        assert report.themes == (("Security", ("SSO", "MFA")),)
        assert report.highlights == ("SSO live",)
        assert ("Items delivered", "3") in report.metrics
        # emoji: LLM slot honoured, missing slots defaulted
        emoji = dict(report.emoji_theme)
        assert emoji["headline"] == "🔐"
        assert emoji["summary"]  # defaulted, not empty
        assert not report.warnings

    def test_llm_failure_falls_back(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=_items(2))
        # is_llm_configured True but the call raises a generic error → fallback
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)

        def _boom(**k):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _boom)
        report = engine.run_delivery_report("last_month", session_id="", db_path=db_path)
        assert report.delivered_items  # evidence preserved
        assert report.themes  # deterministic "Delivered work" theme
        assert any("unavailable" in w.lower() for w in report.warnings)

    def test_auth_error_becomes_warning_not_raised(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=_items(1))
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        monkeypatch.setattr("yeaboi.agent.nodes._is_llm_auth_or_billing_error", lambda e: True)

        def _boom(**k):
            raise RuntimeError("401 invalid api key")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _boom)
        report = engine.run_delivery_report("last_sprint", db_path=db_path)  # must not raise
        assert any("billing" in w.lower() or "invalid" in w.lower() for w in report.warnings)

    def test_no_items_skips_llm(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=[], warnings=["No board configured"])

        # If the LLM were called this would blow up (get_llm not patched to succeed).
        def _fail(**k):
            raise AssertionError("LLM must not be called when there is no delivered work")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _fail)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        report = engine.run_delivery_report("last_month", db_path=db_path)
        assert report.delivered_items == ()
        assert report.warnings == ("No board configured",)
        assert report.metrics == (("Items delivered", "0"),)

    def test_persists_to_store(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=_items(2))
        _patch_llm(monkeypatch, "{}")  # empty parse → fallback, still persists
        engine.run_delivery_report("last_sprint", session_id="s1", db_path=db_path)
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as store:
            assert store.get_latest_report() is not None
            assert len(store.get_history()) == 1


class TestQuarterReport:
    def test_quarter_uses_window_and_labels(self, monkeypatch, db_path):
        captured = {}

        def _fake_gather(period, **kw):
            captured["period"] = period
            captured["days_override"] = kw.get("days_override")
            return list(_items(2)), [], []

        monkeypatch.setattr(activity_mod, "gather_delivered_work", _fake_gather)
        _patch_llm(monkeypatch, "{}")  # empty parse → fallback, keeps it deterministic

        from datetime import date

        report = engine.run_delivery_report(
            activity_mod.PERIOD_QUARTER,
            db_path=db_path,
            today=date(2026, 7, 13),
            window_start="2026-04-01",
            window_end="2026-06-30",
            sprint_names=("Sprint 8", "Sprint 9"),
            period_label_override="Q2 2026",
        )
        assert report.period_label == "Q2 2026"
        assert report.period_start == "2026-04-01"
        assert report.period_end == "2026-06-30"
        assert report.sprint_names == ("Sprint 8", "Sprint 9")
        assert captured["period"] == activity_mod.PERIOD_QUARTER
        # 2026-04-01 → 2026-07-13 is 103 days
        assert captured["days_override"] == 103
        assert any("truncated" in w.lower() for w in report.warnings)


class TestWindowPeriod:
    def test_window_period_derives_label_and_days(self, monkeypatch, db_path):
        captured = {}

        def _fake_gather(period, **kw):
            captured["days_override"] = kw.get("days_override")
            return list(_items(1)), [], []

        monkeypatch.setattr(activity_mod, "gather_delivered_work", _fake_gather)
        _patch_llm(monkeypatch, "{}")

        from datetime import date

        report = engine.run_delivery_report(
            activity_mod.PERIOD_WINDOW,
            db_path=db_path,
            today=date(2026, 7, 28),
            window_start="2026-07-01",
            window_end="2026-07-15",
        )
        assert report.period_label == "2026-07-01 → 2026-07-15"
        assert report.period_start == "2026-07-01"
        assert report.period_end == "2026-07-15"
        assert captured["days_override"] == 27  # derived from window_start to today
        assert any("truncated" in w.lower() for w in report.warnings)

    def test_non_quarter_period_with_window_start_uses_window(self, monkeypatch, db_path):
        """The window gate generalised from quarter-only to any explicit start date."""
        captured = {}

        def _fake_gather(period, **kw):
            captured["days_override"] = kw.get("days_override")
            return [], [], []

        monkeypatch.setattr(activity_mod, "gather_delivered_work", _fake_gather)
        from datetime import date

        report = engine.run_delivery_report(
            "last_sprint", db_path=db_path, today=date(2026, 7, 28), window_start="2026-07-21"
        )
        assert captured["days_override"] == 7
        assert report.period_start == "2026-07-21"


class TestProgressAndCancel:
    def test_on_progress_emits_stage_messages(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=_items(1))
        _patch_llm(monkeypatch, "{}")
        seen: list[str] = []
        engine.run_delivery_report("last_sprint", db_path=db_path, on_progress=seen.append)
        assert "Loading session state" in seen
        assert any("narrative" in m for m in seen)
        assert any("exporting" in m.lower() for m in seen)

    def test_broken_on_progress_is_swallowed(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=[])

        def _boom(msg):
            raise RuntimeError("bad callback")

        report = engine.run_delivery_report("last_sprint", db_path=db_path, on_progress=_boom)
        assert report is not None

    def test_cancel_before_llm_raises_and_persists_nothing(self, monkeypatch, db_path):
        import threading

        cancel = threading.Event()

        def _gather_then_cancel(period, **kw):
            cancel.set()  # cancelled while gathering — next stage boundary must stop
            return list(_items(2)), [], []

        monkeypatch.setattr(activity_mod, "gather_delivered_work", _gather_then_cancel)

        def _fail(**k):
            raise AssertionError("LLM must not be called after cancellation")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _fail)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        with pytest.raises(engine.ReportCancelledError):
            engine.run_delivery_report("last_sprint", db_path=db_path, cancel_event=cancel)

        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as store:
            assert store.get_latest_report() is None  # nothing persisted

    def test_theme_forwarded_to_export(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=[])
        captured = {}
        monkeypatch.setattr(
            "yeaboi.reporting.export.export_report",
            lambda report, **kw: captured.update(kw) or {},
        )
        engine.run_delivery_report("last_sprint", db_path=db_path, theme="sunset")
        assert captured["theme"] == "sunset"


class TestMetrics:
    def test_counts_sources_and_contributors(self):
        items = [
            DeliveredItem(key="J-1", status="Done", source="jira", assignee="Ada"),
            DeliveredItem(key="J-2", status="Done", source="jira", assignee="Bo"),
            DeliveredItem(key="#3", status="Closed", source="azuredevops", assignee="Ada"),
        ]
        metrics = dict(engine._compute_metrics(items))
        assert metrics["Items delivered"] == "3"
        assert metrics["Contributors"] == "2"
        assert metrics["From Jira"] == "2"
        assert metrics["From Azure DevOps"] == "1"


class TestPeriodDays:
    def test_last_week_is_always_seven_days(self):
        assert activity_mod.period_days("last_week", sprint_length_weeks=2) == 7
        assert activity_mod.period_days("last_week", sprint_length_weeks=3) == 7

    def test_last_sprint_is_one_sprint(self):
        assert activity_mod.period_days("last_sprint", sprint_length_weeks=2) == 14
        assert activity_mod.period_days("last_sprint", sprint_length_weeks=1) == 7

    def test_last_month_is_at_least_28(self):
        assert activity_mod.period_days("last_month", sprint_length_weeks=1) == 28
        assert activity_mod.period_days("last_month", sprint_length_weeks=2) == 28
        assert activity_mod.period_days("last_month", sprint_length_weeks=3) == 42


class TestWindowValidation:
    """Bad window dates fail fast with a friendly message — the strings arrive
    verbatim from the CLI flags and the MCP tool (validation runs before any
    DB or tracker access)."""

    def test_bad_window_start_raises(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            engine.run_delivery_report("quarter", window_start="July 1st")

    def test_bad_window_end_raises(self):
        with pytest.raises(ValueError, match="window_end"):
            engine.run_delivery_report("quarter", window_start="2026-04-01", window_end="30/06/2026")

    def test_inverted_window_raises(self):
        with pytest.raises(ValueError, match="before window_start"):
            engine.run_delivery_report("quarter", window_start="2026-06-30", window_end="2026-04-01")


@pytest.fixture(autouse=True)
def _no_configured_sources(monkeypatch):
    """Keep the signals stage hermetic: tests opt into code/docs sources explicitly."""
    monkeypatch.setattr(activity_mod, "available_report_sources", lambda: {"delivery": [], "code": [], "docs": []})


class TestSources:
    def test_delivery_selection_reaches_gather(self, monkeypatch, db_path):
        captured = {}

        def _gather(period, **kw):
            captured.update(kw)
            return [], [], []

        monkeypatch.setattr(activity_mod, "gather_delivered_work", _gather)
        engine.run_delivery_report("last_sprint", db_path=db_path, sources={"delivery": ["azdevops"]})
        assert captured["delivery_sources"] == {"azuredevops"}  # alias canonicalized

    def test_none_sources_is_auto(self, monkeypatch, db_path):
        captured = {}

        def _gather(period, **kw):
            captured.update(kw)
            return [], [], []

        monkeypatch.setattr(activity_mod, "gather_delivered_work", _gather)
        engine.run_delivery_report("last_sprint", db_path=db_path)
        assert captured["delivery_sources"] is None

    def test_signals_gathered_and_attached_with_llm(self, monkeypatch, db_path):
        from yeaboi.agent.state import SupportingSignal

        _patch_activity(monkeypatch, items=_items(2))
        _patch_llm(monkeypatch, json.dumps({"headline": "H", "executive_summary": "S"}))
        monkeypatch.setattr(
            activity_mod,
            "available_report_sources",
            lambda: {"delivery": ["jira"], "code": ["github"], "docs": []},
        )
        sig = SupportingSignal(kind="pull_requests", source="github", count=7, samples=("Fix (#1)",))
        sig_kwargs = {}

        def _signals(**kw):
            sig_kwargs.update(kw)
            return (sig,), ["github partially unavailable"]

        monkeypatch.setattr("yeaboi.reporting.context.gather_supporting_signals", _signals)
        prompt_kwargs = {}
        import yeaboi.prompts.reporting as prompts_mod

        real_prompt = prompts_mod.get_delivery_report_prompt

        def _prompt(**kw):
            prompt_kwargs.update(kw)
            return real_prompt(**kw)

        monkeypatch.setattr(prompts_mod, "get_delivery_report_prompt", _prompt)
        report = engine.run_delivery_report("last_sprint", db_path=db_path)
        assert report.supporting_signals == (sig,)
        assert "github partially unavailable" in report.warnings
        assert sig_kwargs["code_sources"] == ["github"]
        assert prompt_kwargs["supporting_signals"] == [
            {"kind": "pull_requests", "source": "github", "count": 7, "samples": ("Fix (#1)",)}
        ]

    def test_signals_attached_to_zero_item_fallback(self, monkeypatch, db_path):
        from yeaboi.agent.state import SupportingSignal

        _patch_activity(monkeypatch, items=[])
        monkeypatch.setattr(
            activity_mod,
            "available_report_sources",
            lambda: {"delivery": [], "code": ["github"], "docs": []},
        )
        sig = SupportingSignal(kind="commits", source="github", count=3)
        monkeypatch.setattr("yeaboi.reporting.context.gather_supporting_signals", lambda **kw: ((sig,), []))
        # No LLM patched: zero items must skip the LLM entirely and still carry signals.
        report = engine.run_delivery_report("last_sprint", db_path=db_path)
        assert report.supporting_signals == (sig,)
        assert report.delivered_items == ()

    def test_unconfigured_sources_skip_signal_gather(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=[])

        def _boom(**kw):
            raise AssertionError("signal gather must be skipped when nothing is configured")

        monkeypatch.setattr("yeaboi.reporting.context.gather_supporting_signals", _boom)
        report = engine.run_delivery_report("last_sprint", db_path=db_path)  # autouse: nothing configured
        assert report.supporting_signals == ()

    def test_explicitly_deselected_code_docs_skip_gather(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=[])
        monkeypatch.setattr(
            activity_mod,
            "available_report_sources",
            lambda: {"delivery": ["jira"], "code": ["github"], "docs": ["notion"]},
        )

        def _boom(**kw):
            raise AssertionError("explicit empty code/docs selection must skip the gather")

        monkeypatch.setattr("yeaboi.reporting.context.gather_supporting_signals", _boom)
        report = engine.run_delivery_report("last_sprint", db_path=db_path, sources={"code": [], "docs": []})
        assert report.supporting_signals == ()

    def test_cancel_during_signals_stage_persists_nothing(self, monkeypatch, db_path):
        import threading

        _patch_activity(monkeypatch, items=_items(1))
        monkeypatch.setattr(
            activity_mod,
            "available_report_sources",
            lambda: {"delivery": [], "code": ["github"], "docs": []},
        )
        cancel = threading.Event()

        def _signals(**kw):
            cancel.set()  # cancel lands while the signal fetch is in flight
            return ((), [])

        monkeypatch.setattr("yeaboi.reporting.context.gather_supporting_signals", _signals)
        with pytest.raises(engine.ReportCancelledError):
            engine.run_delivery_report("last_sprint", db_path=db_path, cancel_event=cancel)
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as store:
            assert store.get_latest_report("") is None


class TestDeckStylePlumbing:
    def test_auto_export_passes_the_saved_deck_style(self, monkeypatch, db_path):
        """engine._export is the one seam every auto-export flows through — it must
        resolve the persisted prefs so CLI/MCP report_delivery honor them."""
        from yeaboi.reporting.style import DeckStyle

        _patch_activity(monkeypatch, items=_items(1))
        sentinel = DeckStyle(layout="compact", footer_text="saved prefs")
        monkeypatch.setattr("yeaboi.reporting.style.load_deck_style", lambda: sentinel)
        seen = {}

        def _capture(report, **kw):
            seen.update(kw)
            return {}

        monkeypatch.setattr("yeaboi.reporting.export.export_report", _capture)
        engine.run_delivery_report("last_sprint", db_path=db_path)
        assert seen["style"] == sentinel


class TestProjectScopedReport:
    """The planning→reporting edge: sprint framing from the project's plan."""

    def test_scoped_report_frames_with_the_projects_plan(self, monkeypatch, db_path):
        from yeaboi.agent.state import Sprint
        from yeaboi.projects.store import ProjectStore
        from yeaboi.sessions import SessionStore

        with ProjectStore(db_path) as projects:
            pid = projects.create("Apollo")["project_id"]
        with SessionStore(db_path) as s:
            s.create_session("plan-1", "Apollo", mode="planning", project_id=pid)
            s.save_state(
                "plan-1",
                {
                    "project_name": "Apollo",
                    "sprints": [Sprint(id="SP-1", name="Sprint 1", goal="", capacity_points=10, story_ids=())],
                },
            )
        _patch_activity(monkeypatch, items=_items(1))
        _patch_llm(monkeypatch, json.dumps({"headline": "h", "executive_summary": "s"}))
        report = engine.run_delivery_report("last_sprint", project_id=pid, db_path=db_path)
        assert report.project_name == "Apollo"

    def test_unscoped_report_is_unchanged(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, items=_items(1))
        _patch_llm(monkeypatch, json.dumps({"headline": "h", "executive_summary": "s"}))
        report = engine.run_delivery_report("last_sprint", session_id="", db_path=db_path)
        assert report.project_name == ""
