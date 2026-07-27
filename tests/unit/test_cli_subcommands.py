"""Tests for the `yeaboi report/standup/perf/analyze` subcommands (cli.py).

The subcommand layer is additive — tests/integration/test_cli.py pins the flat
flags and stays untouched; this file covers the new headless mode runners.
"""

import argparse

import pytest

from yeaboi.agent.state import DeliveryReport, OneOnOnePrep, OneOnOneRecord, SixMonthReview, StandupReport
from yeaboi.cli import _cmd_analyze, _cmd_perf, _cmd_report, _cmd_standup, _run_subcommand, build_parser


def _console(buf=None):
    import io

    from rich.console import Console

    return Console(file=buf or io.StringIO(), width=100)


def test_no_subcommand_flag_abbreviation_collides_with_main_flags():
    """Guard the argparse prefix-matching trap: a subcommand flag that is a
    strict prefix of >=2 top-level flags raises 'ambiguous option' during the
    main parser's pre-scan on Python <3.14 (it bit `retro --export` vs the
    top-level --export-questionnaire/--export-only). CI runs 3.11, so this
    must be caught statically rather than only where 3.14 is lenient."""
    parser = build_parser()
    main_opts = [s for a in parser._actions for s in a.option_strings if s.startswith("--")]
    subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    problems = []
    for name, sp in subs.choices.items():
        groups = [(name, sp)]
        for nested in (a for a in sp._actions if isinstance(a, argparse._SubParsersAction)):
            groups += [(f"{name} {nn}", nsp) for nn, nsp in nested.choices.items()]
        for label, p in groups:
            for opt in (s for a in p._actions for s in a.option_strings if s.startswith("--")):
                clashes = [m for m in main_opts if m != opt and m.startswith(opt)]
                if len(clashes) >= 2:
                    problems.append(f"{label}: {opt} abbreviation-collides with {clashes}")
    assert not problems, "argparse ambiguity (fails on Python <3.14):\n" + "\n".join(problems)


class TestParsing:
    def test_bare_invocation_has_no_command(self):
        args = build_parser().parse_args([])
        assert args.command is None

    def test_flat_flags_unaffected(self):
        args = build_parser().parse_args(["--standup-run", "--standup-session", "abc"])
        assert args.command is None
        assert args.standup_run is True
        assert args.standup_session == "abc"

    def test_report_parses(self):
        args = build_parser().parse_args(["report", "--period", "quarter", "--format", "json"])
        assert args.command == "report"
        assert args.period == "quarter"
        assert args.format == "json"

    def test_report_defaults(self):
        args = build_parser().parse_args(["report"])
        assert args.period == "last_sprint"
        assert args.session == ""
        assert args.format == "text"
        assert args.window_start == ""
        assert args.sprint_names == ""
        assert args.label == ""

    def test_report_window_flags_parse(self):
        args = build_parser().parse_args(
            [
                "report",
                "--period",
                "quarter",
                "--window-start",
                "2026-04-01",
                "--window-end",
                "2026-06-30",
                "--sprint-names",
                "Sprint 7,Sprint 8",
                "--label",
                "Q2 2026",
            ]
        )
        assert args.window_start == "2026-04-01"
        assert args.window_end == "2026-06-30"
        assert args.sprint_names == "Sprint 7,Sprint 8"
        assert args.label == "Q2 2026"

    def test_standup_schedule_parses(self):
        args = build_parser().parse_args(["standup", "--schedule", "status"])
        assert args.schedule == "status"

    def test_standup_schedule_rejects_bad_action(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["standup", "--schedule", "enable"])

    def test_perf_complete_images_recipients_parse(self):
        args = build_parser().parse_args(
            [
                "perf",
                "complete",
                "Sam",
                "--transcript",
                "notes",
                "--images",
                "a.png",
                "b.png",
                "--recipients",
                "lead@x.com",
            ]
        )
        assert args.images == ["a.png", "b.png"]
        assert args.recipients == ["lead@x.com"]

    def test_standup_parses(self):
        args = build_parser().parse_args(["standup", "--deliver", "--channels", "slack", "email", "--days", "3"])
        assert args.command == "standup"
        assert args.deliver is True
        assert args.channels == ["slack", "email"]
        assert args.days == 3

    def test_standup_rejects_bad_channel(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["standup", "--channels", "pager"])

    def test_perf_requires_subcommand(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["perf"])

    def test_perf_prep_parses(self):
        args = build_parser().parse_args(["perf", "prep", "Sam"])
        assert args.command == "perf"
        assert args.perf_command == "prep"
        assert args.engineer == "Sam"

    def test_perf_complete_requires_transcript(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["perf", "complete", "Sam"])

    def test_analyze_parses(self):
        args = build_parser().parse_args(["analyze", "--source", "jira", "--sprints", "4", "--samples"])
        assert args.command == "analyze"
        assert args.source == "jira"
        assert args.sprints == 4
        assert args.samples is True
        assert args.no_insights is False
        assert args.depth == "deep"
        assert args.window_days == 120


class TestReportCommand:
    def test_text_output(self, monkeypatch, capsys):
        captured: dict = {}

        def fake_report(period, *, session_id="", **kw):
            captured.update(period=period, session_id=session_id)
            return DeliveryReport(period_label="Last sprint", executive_summary="Shipped.", warnings=("no tracker",))

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", fake_report)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "new-abc-2026-07-20")
        args = build_parser().parse_args(["report", "--period", "last_month"])
        assert _cmd_report(args, _console()) == 0
        assert captured == {"period": "last_month", "session_id": "new-abc-2026-07-20"}
        assert "no tracker" in capsys.readouterr().err

    def test_json_output_is_clean(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "yeaboi.reporting.engine.run_delivery_report",
            lambda period, *, session_id="", **kw: DeliveryReport(executive_summary="Shipped."),
        )
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "x")
        args = build_parser().parse_args(["report", "--format", "json"])
        assert _cmd_report(args, _console()) == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["executive_summary"] == "Shipped."

    def test_window_flags_reach_the_engine(self, monkeypatch):
        captured: dict = {}

        def fake_report(period, **kw):
            captured.update(period=period, **kw)
            return DeliveryReport()

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", fake_report)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(
            [
                "report",
                "--period",
                "quarter",
                "--window-start",
                "2026-04-01",
                "--window-end",
                "2026-06-30",
                "--sprint-names",
                "Sprint 7, Sprint 8",
                "--label",
                "Q2 2026",
            ]
        )
        assert _cmd_report(args, _console()) == 0
        assert captured["window_start"] == "2026-04-01"
        assert captured["window_end"] == "2026-06-30"
        assert captured["sprint_names"] == ("Sprint 7", "Sprint 8")
        assert captured["period_label_override"] == "Q2 2026"


class TestStandupCommand:
    def test_no_session_exits_2(self, monkeypatch):
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: None)
        args = build_parser().parse_args(["standup"])
        assert _cmd_standup(args, _console()) == 2

    def test_runs_engine_with_overrides(self, monkeypatch):
        captured: dict = {}

        def fake_run(
            session_id,
            *,
            deliver,
            days,
            channels,
            tracker_sources,
            team_members,
            code_sources,
            github_repositories,
            azdo_projects,
            azdo_repositories,
            documentation_sources,
        ):
            captured.update(
                session_id=session_id,
                deliver=deliver,
                days=days,
                channels=channels,
                tracker_sources=tracker_sources,
                team_members=team_members,
                code_sources=code_sources,
                github_repositories=github_repositories,
                azdo_projects=azdo_projects,
                azdo_repositories=azdo_repositories,
                documentation_sources=documentation_sources,
            )
            return StandupReport(team_summary="fine")

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["standup", "--deliver", "--channels", "slack", "--days", "2"])
        assert _cmd_standup(args, _console()) == 0
        assert captured == {
            "session_id": "sid",
            "deliver": True,
            "days": 2,
            "channels": ["slack"],
            "tracker_sources": None,
            "team_members": None,
            "code_sources": None,
            "github_repositories": None,
            "azdo_projects": None,
            "azdo_repositories": None,
            "documentation_sources": None,
        }


class TestStandupSchedule:
    def test_status(self, monkeypatch, capsys):
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr(
            "yeaboi.standup.scheduler.get_schedule_status",
            lambda sid: {"platform": "macos", "installed": True, "path": "/tmp/plist"},
        )
        args = build_parser().parse_args(["standup", "--schedule", "status", "--format", "json"])
        assert _cmd_standup(args, _console()) == 0
        import json

        assert json.loads(capsys.readouterr().out)["installed"] is True

    def test_install_uses_saved_config(self, monkeypatch, tmp_path):
        captured: dict = {}
        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")

        def fake_install(session_id, standup_time, weekdays, lead_minutes):
            captured.update(session_id=session_id, time=standup_time, weekdays=weekdays, lead=lead_minutes)
            return "Installed."

        monkeypatch.setattr("yeaboi.standup.scheduler.install_schedule", fake_install)
        from yeaboi.standup.store import StandupStore

        with StandupStore(db) as store:
            store.save_config(
                "sid", enabled=True, time="09:30", weekdays="1,3,5", delivery_channels=["terminal"], lead_minutes=5
            )
        args = build_parser().parse_args(["standup", "--schedule", "install"])
        assert _cmd_standup(args, _console()) == 0
        assert captured == {"session_id": "sid", "time": "09:30", "weekdays": "1,3,5", "lead": 5}

    def test_install_without_config_uses_defaults(self, monkeypatch, tmp_path):
        captured: dict = {}
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr(
            "yeaboi.standup.scheduler.install_schedule",
            lambda sid, t, w, lm: captured.update(time=t, weekdays=w, lead=lm) or "Installed.",
        )
        args = build_parser().parse_args(["standup", "--schedule", "install"])
        assert _cmd_standup(args, _console()) == 0
        assert captured == {"time": "10:00", "weekdays": "1-5", "lead": 10}

    def test_remove(self, monkeypatch):
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr("yeaboi.standup.scheduler.remove_schedule", lambda sid: "Removed.")
        args = build_parser().parse_args(["standup", "--schedule", "remove"])
        assert _cmd_standup(args, _console()) == 0


class TestPerfCommand:
    def test_roster_empty_exits_2(self, monkeypatch):
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda **kw: [])
        args = build_parser().parse_args(["perf", "roster"])
        assert _cmd_perf(args, _console()) == 2

    def test_prep(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_one_on_one_prep",
            lambda engineer, **kw: captured.update(engineer=engineer, **kw) or OneOnOnePrep(engineer=engineer),
        )
        args = build_parser().parse_args(["perf", "prep", "Sam", "--jira-project", "PROJ"])
        assert _cmd_perf(args, _console()) == 0
        assert captured["session_id"] == "sid"
        assert captured["jira_project"] == "PROJ"

    def test_complete_reads_transcript_file(self, monkeypatch, tmp_path):
        captured: dict = {}
        transcript_file = tmp_path / "notes.txt"
        transcript_file.write_text("we discussed growth\n")

        def fake_complete(engineer, transcript, *, deliver, **kw):
            captured.update(engineer=engineer, transcript=transcript, deliver=deliver, **kw)
            return OneOnOneRecord(engineer=engineer)

        monkeypatch.setattr("yeaboi.performance.engine.complete_one_on_one", fake_complete)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(
            ["perf", "complete", "Sam", "--transcript", f"@{transcript_file}", "--images", "board.png"]
        )
        assert _cmd_perf(args, _console()) == 0
        assert captured["engineer"] == "Sam"
        assert captured["transcript"] == "we discussed growth"
        assert captured["deliver"] is False
        assert captured["images"] == ("board.png",)
        assert captured["recipients"] is None

    def test_complete_missing_file_errors(self, tmp_path):
        args = build_parser().parse_args(["perf", "complete", "Sam", "--transcript", f"@{tmp_path}/nope.txt"])
        assert _cmd_perf(args, _console()) == 1

    def test_review_months_passthrough(self, monkeypatch):
        captured: dict = {}

        def fake_review(engineer, *, period_months, **kw):
            captured.update(engineer=engineer, period_months=period_months)
            return SixMonthReview(engineer=engineer)

        monkeypatch.setattr("yeaboi.performance.engine.run_six_month_review", fake_review)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["perf", "review", "Sam", "--months", "12"])
        assert _cmd_perf(args, _console()) == 0
        assert captured == {"engineer": "Sam", "period_months": 12}

    def test_note_persists(self, monkeypatch, tmp_path):
        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        args = build_parser().parse_args(["perf", "note", "Sam", "--text", "shipped the migration solo"])
        assert _cmd_perf(args, _console()) == 0

        from yeaboi.performance.store import PerformanceStore

        with PerformanceStore(db) as store:
            assert store.get_notes("Sam")[0]["note_text"] == "shipped the migration solo"


class TestRetroCommand:
    def test_no_session_exits_2(self, monkeypatch):
        from yeaboi.cli import _cmd_retro

        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: None)
        args = build_parser().parse_args(["retro"])
        assert _cmd_retro(args, _console()) == 2

    def test_history_json(self, monkeypatch, tmp_path, capsys):
        import json

        from yeaboi.agent.state import RetroReport
        from yeaboi.cli import _cmd_retro
        from yeaboi.retro.store import RetroStore

        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        with RetroStore(db) as store:
            store.record_run(RetroReport(date="2026-07-18", session_id="sid", project_name="P"))
        args = build_parser().parse_args(["retro", "--format", "json"])
        assert _cmd_retro(args, _console()) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["history"][0]["retro_date"] == "2026-07-18"
        assert payload["carried_action_items"] == []  # none on this report

    def test_carried_summary_in_json_and_text(self, monkeypatch, tmp_path, capsys):
        import json

        from yeaboi.agent.state import RetroCard, RetroReport
        from yeaboi.cli import _cmd_retro
        from yeaboi.retro.store import RetroStore

        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        report = RetroReport(
            date="2026-07-18",
            session_id="sid",
            project_name="P",
            carried_action_items=(
                RetroCard(grid="action_items", text="a", status="done"),
                RetroCard(grid="action_items", text="b", status="carried_over"),
            ),
        )
        with RetroStore(db) as store:
            store.record_run(report)
        # JSON surfaces the carried items with statuses.
        assert _cmd_retro(build_parser().parse_args(["retro", "--format", "json"]), _console()) == 0
        payload = json.loads(capsys.readouterr().out)
        statuses = {c["status"] for c in payload["carried_action_items"]}
        assert statuses == {"done", "carried_over"}
        # Text output shows a one-line summary.
        console = _console()
        assert _cmd_retro(build_parser().parse_args(["retro"]), console) == 0

    def test_export_writes_files(self, monkeypatch, tmp_path):
        from yeaboi.agent.state import RetroReport
        from yeaboi.cli import _cmd_retro
        from yeaboi.retro.store import RetroStore

        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.paths.get_retro_export_dir", lambda key: tmp_path / "out")
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        (tmp_path / "out").mkdir()
        with RetroStore(db) as store:
            store.record_run(RetroReport(date="2026-07-18", session_id="sid", project_name="P"))
        args = build_parser().parse_args(["retro", "--export-latest"])
        assert _cmd_retro(args, _console()) == 0
        assert (tmp_path / "out" / "retro-2026-07-18.md").exists()

    def test_export_without_retro_exits_2(self, monkeypatch, tmp_path):
        from yeaboi.cli import _cmd_retro

        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["retro", "--export-latest"])
        assert _cmd_retro(args, _console()) == 2


def _poker_report(session_id: str = "sid", date: str = "2026-07-25"):
    from yeaboi.agent.state import PokerReport, PokerTicketResult

    return PokerReport(
        date=date,
        session_id=session_id,
        source="jira",
        scope_label="Sprint 42",
        tickets=(
            PokerTicketResult(key="PROJ-1", summary="S", final_points=5.0, estimated=True),
            PokerTicketResult(key="PROJ-2", summary="T"),
        ),
    )


class TestPokerCommand:
    def test_poker_parses(self):
        args = build_parser().parse_args(["poker", "--session", "sid", "--limit", "5", "--export-latest"])
        assert args.command == "poker"
        assert args.session == "sid"
        assert args.limit == 5
        assert args.export is True

    def test_empty_history_ok(self, monkeypatch, tmp_path, capsys):
        from yeaboi.cli import _cmd_poker

        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        assert _cmd_poker(build_parser().parse_args(["poker"]), _console()) == 0

    def test_history_json_and_session_filter(self, monkeypatch, tmp_path, capsys):
        import json

        from yeaboi.cli import _cmd_poker
        from yeaboi.poker.store import PokerStore

        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        with PokerStore(db) as store:
            store.record_run(_poker_report("sid-a"))
            store.record_run(_poker_report("sid-b"))
        assert _cmd_poker(build_parser().parse_args(["poker", "--format", "json"]), _console()) == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["history"]) == 2
        assert payload["history"][0]["estimated_count"] == 1
        # --session narrows to one recorded session.
        assert (
            _cmd_poker(build_parser().parse_args(["poker", "--session", "sid-a", "--format", "json"]), _console()) == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert {r["session_id"] for r in payload["history"]} == {"sid-a"}

    def test_export_writes_files(self, monkeypatch, tmp_path):
        from yeaboi.cli import _cmd_poker
        from yeaboi.poker.store import PokerStore

        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: tmp_path / "out")
        (tmp_path / "out").mkdir()
        with PokerStore(db) as store:
            store.record_run(_poker_report())
        assert _cmd_poker(build_parser().parse_args(["poker", "--export-latest"]), _console()) == 0
        assert (tmp_path / "out" / "poker-2026-07-25.md").exists()

    def test_export_without_session_exits_2(self, monkeypatch, tmp_path):
        from yeaboi.cli import _cmd_poker

        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        assert _cmd_poker(build_parser().parse_args(["poker", "--export-latest"]), _console()) == 2


def _delivery_sub(src, key):
    from yeaboi.team_profile import TeamProfile

    return {
        "profile": TeamProfile(team_id=f"{src}:{key}", source=src, project_key=key, velocity_avg=23.0),
        "insights": {"start": [{"title": "Pairing"}], "stop": [], "keep": [], "try": []},
    }


class TestAnalyzeCommand:
    def test_passthrough_and_summary(self, monkeypatch):
        captured: dict = {}

        def fake_analysis(**kwargs):
            captured.update(kwargs)
            return {"delivery": {"jira": _delivery_sub("jira", "P")}, "code": None, "docs": None, "warnings": []}

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        args = build_parser().parse_args(["analyze", "--source", "jira", "--sprints", "4", "--no-insights"])
        assert _cmd_analyze(args, _console()) == 0
        assert captured["source"] == "jira"
        assert captured["sprint_count"] == 4
        assert captured["include_insights"] is False
        assert captured["analysis_depth"] == "deep"
        assert captured["analysis_window_days"] == 120

    def test_depth_deep_passthrough(self, monkeypatch):
        captured: dict = {}

        monkeypatch.setattr(
            "yeaboi.analysis.run_team_analysis",
            lambda **kwargs: captured.update(kwargs) or {"delivery": {}, "code": None, "docs": None, "warnings": []},
        )
        args = build_parser().parse_args(["analyze", "--depth", "deep", "--delivery", "jira", "--features", "delivery"])
        assert _cmd_analyze(args, _console()) == 0
        assert captured["analysis_depth"] == "deep"
        assert captured["analysis_features"] == ["delivery"]

    def test_delivery_banners_and_comparison(self, monkeypatch):
        import io

        def fake_analysis(**kwargs):
            return {
                "delivery": {"jira": _delivery_sub("jira", "P"), "azdevops": _delivery_sub("azdevops", "Web")},
                "code": None,
                "docs": None,
                "comparison": [("Avg velocity", "23", "15")],
                "warnings": [],
            }

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        args = build_parser().parse_args(["analyze", "--source", "both"])
        buf = io.StringIO()
        assert _cmd_analyze(args, _console(buf)) == 0
        out = buf.getvalue()
        assert "From Jira" in out and "From Azure DevOps" in out
        assert "23" in out and "15" in out  # side by side, never blended

    def test_per_component_flags_and_members(self, monkeypatch):
        captured: dict = {}

        def fake_analysis(**kwargs):
            captured.update(kwargs)
            return {"delivery": {"jira": _delivery_sub("jira", "P")}, "code": None, "docs": None, "warnings": []}

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        args = build_parser().parse_args(
            [
                "analyze",
                "--delivery",
                "jira",
                "--code",
                "github",
                "azdo",
                "--docs",
                "confluence",
                "--members",
                "Alice",
                "Bob",
            ]
        )
        assert _cmd_analyze(args, _console()) == 0
        assert captured["components"] == {"delivery": ["jira"], "code": ["github", "azdo"], "docs": ["confluence"]}
        assert captured["members"] == {"jira": ["Alice", "Bob"], "azdevops": ["Alice", "Bob"]}

    def test_source_ignored_without_delivery_warns(self, monkeypatch, capsys):
        def fake_analysis(**kwargs):
            return {"delivery": {}, "code": {"signal": None}, "docs": None, "warnings": []}

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        args = build_parser().parse_args(["analyze", "--source", "jira", "--code", "github"])
        _cmd_analyze(args, _console())
        assert "--source jira ignored" in capsys.readouterr().err

    def test_global_code_and_docs_printed(self, monkeypatch):
        import io

        from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal

        def fake_analysis(**kwargs):
            return {
                "delivery": {},
                "code": {"signal": AiAdoptionSignal(scanned_commits=40, ai_commits=18, footprint_pct=45.0)},
                "docs": {"signal": DocQualitySignal(pages_scanned=6, avg_clarity=72.0)},
                "warnings": [],
            }

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        args = build_parser().parse_args(["analyze", "--code", "github", "--docs", "confluence"])
        buf = io.StringIO()
        assert _cmd_analyze(args, _console(buf)) == 0
        out = buf.getvalue()
        assert "45%" in out  # global code footprint
        assert "72/100" in out  # global docs clarity


class TestDispatch:
    def test_unhandled_error_returns_1(self, monkeypatch, capsys):
        def boom(period, **kw):
            raise ValueError("tracker exploded")

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", boom)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "x")
        args = build_parser().parse_args(["report"])
        assert _run_subcommand(args) == 1
        assert "tracker exploded" in capsys.readouterr().err

    def test_main_routes_commands(self, monkeypatch):
        from yeaboi import cli

        # Keep global state untouched: configure_logging() is idempotent (would
        # starve later logging tests) and load_user_config() would leak the real
        # ~/.yeaboi/.env credentials into os.environ for the rest of the run.
        monkeypatch.setattr("yeaboi.logging_setup.configure_logging", lambda: None)
        monkeypatch.setattr(cli, "load_user_config", lambda: None)
        monkeypatch.setattr(cli.paths, "migrate_root_dir", lambda: None)
        monkeypatch.setattr(cli, "_run_subcommand", lambda args: 0)
        with pytest.raises(SystemExit) as exc:
            cli.main(["report"])
        assert exc.value.code == 0

    def test_resolve_cli_session_validates_explicit(self, monkeypatch, tmp_path):
        from yeaboi.cli import _resolve_cli_session
        from yeaboi.sessions import SessionStore

        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        with SessionStore(db) as store:
            store.create_session("new-1234-2026-01-01")

        assert _resolve_cli_session("new-1234-2026-01-01") == "new-1234-2026-01-01"
        with pytest.raises(ValueError, match="available: new-1234-2026-01-01"):
            _resolve_cli_session("new-typo-2026-01-01")

    def test_resolve_cli_session_empty_db(self, monkeypatch, tmp_path):
        from yeaboi.cli import _resolve_cli_session

        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        assert _resolve_cli_session("") is None
        with pytest.raises(ValueError, match="none saved yet"):
            _resolve_cli_session("new-nope-2026-01-01")


class TestStrictExit:
    def test_report_warnings_exit_3(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "yeaboi.reporting.engine.run_delivery_report",
            lambda period, **kw: DeliveryReport(warnings=("no tracker configured",)),
        )
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["report", "--strict"])
        assert _cmd_report(args, _console()) == 3
        assert "exit 3" in capsys.readouterr().err

    def test_report_empty_result_exit_3(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.reporting.engine.run_delivery_report", lambda period, **kw: DeliveryReport(delivered_items=())
        )
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["report", "--strict"])
        assert _cmd_report(args, _console()) == 3

    def test_default_keeps_exit_0_on_warnings(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.reporting.engine.run_delivery_report",
            lambda period, **kw: DeliveryReport(warnings=("no tracker configured",)),
        )
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["report"])
        assert _cmd_report(args, _console()) == 0

    def test_standup_strict(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.standup.engine.run_standup",
            lambda session_id, **kw: StandupReport(team_summary="x", warnings=("Jira 401",)),
        )
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["standup", "--strict"])
        assert _cmd_standup(args, _console()) == 3

    def test_analyze_strict(self, monkeypatch):
        from yeaboi.team_profile import TeamProfile

        monkeypatch.setattr(
            "yeaboi.analysis.run_team_analysis",
            lambda **kw: {
                "profile": TeamProfile(team_id="jira:P", source="jira", project_key="P"),
                "insights": {},
                "warnings": ["insights failed"],
            },
        )
        args = build_parser().parse_args(["analyze", "--strict", "--format", "json"])
        assert _cmd_analyze(args, _console()) == 3

    def test_perf_review_strict(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_six_month_review",
            lambda engineer, **kw: SixMonthReview(engineer=engineer, warnings=("LLM fallback",)),
        )
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(["perf", "review", "Sam", "--strict"])
        assert _cmd_perf(args, _console()) == 3


def test_namespace_type_sanity():
    # The subparsers must not shadow existing flat-flag dests.
    args = build_parser().parse_args(["--quick"])
    assert isinstance(args, argparse.Namespace)
    assert args.quick is True
    assert args.command is None
