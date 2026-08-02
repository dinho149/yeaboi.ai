"""Tests for the `yeaboi report/standup/perf/analyze` subcommands (cli.py).

The subcommand layer is additive — tests/integration/test_cli.py pins the flat
flags and stays untouched; this file covers the new headless mode runners.
"""

import argparse
import io
import json
import os

import pytest

from yeaboi.agent.state import DeliveryReport, OneOnOnePrep, OneOnOneRecord, SixMonthReview, StandupReport
from yeaboi.beta import BETA_TAG, PERFORMANCE_BETA_NOTICE, PERFORMANCE_BETA_PHRASE
from yeaboi.cli import (
    _cmd_analyze,
    _cmd_perf,
    _cmd_report,
    _cmd_standup,
    _cmd_standup_review,
    _run_subcommand,
    build_parser,
)


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

    def _captured_sources(self, monkeypatch, argv):
        captured: dict = {}

        def fake_report(period, **kw):
            captured.update(kw)
            return DeliveryReport()

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", fake_report)
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        args = build_parser().parse_args(argv)
        assert _cmd_report(args, _console()) == 0
        return captured["sources"]

    def test_no_source_flags_means_auto(self, monkeypatch):
        assert self._captured_sources(monkeypatch, ["report"]) is None

    def test_source_both_expands_to_both_trackers(self, monkeypatch):
        sources = self._captured_sources(monkeypatch, ["report", "--source", "both"])
        assert sources == {"delivery": ["jira", "azdevops"]}

    def test_all_source_flags_assemble_dict(self, monkeypatch):
        sources = self._captured_sources(
            monkeypatch,
            [
                "report",
                "--source",
                "jira",
                "--code-sources",
                "github",
                "--documentation-sources",
                "confluence",
                "notion",
            ],
        )
        assert sources == {"delivery": ["jira"], "code": ["github"], "docs": ["confluence", "notion"]}

    def test_source_flags_parse_choices(self):
        import pytest as _pytest

        with _pytest.raises(SystemExit):
            build_parser().parse_args(["report", "--source", "gitlab"])
        with _pytest.raises(SystemExit):
            build_parser().parse_args(["report", "--code-sources", "svn"])


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
            review_transcripts,
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
                review_transcripts=review_transcripts,
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
            "review_transcripts": True,
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


class TestPerfBetaLabelling:
    """`yeaboi perf` says it's beta in its help and before every run."""

    def _perf_parser(self):
        parser = build_parser()
        subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return subs

    def test_parent_help_carries_the_tag_and_description(self):
        subs = self._perf_parser()
        assert subs.choices["perf"].description == PERFORMANCE_BETA_NOTICE
        help_text = next(a.help for a in subs._choices_actions if a.dest == "perf")
        assert BETA_TAG in help_text

    def test_every_child_help_carries_the_description(self):
        # `yeaboi perf prep --help` is a normal place to land without ever
        # seeing the parent's help.
        perf = self._perf_parser().choices["perf"]
        nested = next(a for a in perf._actions if isinstance(a, argparse._SubParsersAction))
        for name, child in nested.choices.items():
            assert child.description == PERFORMANCE_BETA_NOTICE, name

    def _run_note(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        args = build_parser().parse_args(["perf", "note", "Sam", "--text", "x"])
        return _cmd_perf(args, _console())

    def test_notice_goes_to_stderr_not_stdout(self, monkeypatch, tmp_path, capsys):
        # The artifact is routinely piped; a caveat inside the file is worse
        # than no caveat at all.
        monkeypatch.delenv("BETA_NOTICES_ENABLED", raising=False)
        assert self._run_note(monkeypatch, tmp_path) == 0

        captured = capsys.readouterr()
        assert PERFORMANCE_BETA_PHRASE in captured.err
        assert PERFORMANCE_BETA_PHRASE not in captured.out

    def test_notice_suppressed_by_env(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("BETA_NOTICES_ENABLED", "false")
        assert self._run_note(monkeypatch, tmp_path) == 0

        assert PERFORMANCE_BETA_PHRASE not in capsys.readouterr().err

    @pytest.mark.parametrize("subcommand", ["roster", "prep", "complete", "review", "note"])
    def test_notice_prints_for_every_subcommand(self, monkeypatch, tmp_path, capsys, subcommand):
        # Guards against the call being pushed down into one branch later.
        monkeypatch.delenv("BETA_NOTICES_ENABLED", raising=False)
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda **kw: [])
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_one_on_one_prep",
            lambda engineer, **kw: OneOnOnePrep(engineer=engineer),
        )
        monkeypatch.setattr(
            "yeaboi.performance.engine.complete_one_on_one",
            lambda engineer, transcript, **kw: OneOnOneRecord(engineer=engineer),
        )
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_six_month_review",
            lambda engineer, **kw: SixMonthReview(engineer=engineer),
        )
        argv = {
            "roster": ["perf", "roster"],
            "prep": ["perf", "prep", "Sam"],
            "complete": ["perf", "complete", "Sam", "--transcript", "notes"],
            "review": ["perf", "review", "Sam"],
            "note": ["perf", "note", "Sam", "--text", "x"],
        }[subcommand]

        _cmd_perf(build_parser().parse_args(argv), _console())

        assert PERFORMANCE_BETA_PHRASE in capsys.readouterr().err


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


class TestAllowPathFlag:
    """--allow-path grants session-scoped sandbox access (never persisted)."""

    def test_flag_is_repeatable(self):
        args = build_parser().parse_args(["--allow-path", "/a", "--allow-path", "/b"])
        assert args.allow_path == ["/a", "/b"]

    def test_defaults_to_empty(self):
        args = build_parser().parse_args([])
        assert args.allow_path == []


class TestSeedAllowedPaths:
    """One-time grandfathering of pre-sandbox standup repo paths."""

    def _seed(self):
        from yeaboi.cli import _seed_allowed_paths_from_standup

        return _seed_allowed_paths_from_standup()

    def test_noop_when_whitelist_already_set(self, monkeypatch):
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "/already")
        self._seed()
        assert os.environ["YEABOI_ALLOWED_PATHS"] == "/already"

    def test_seeds_from_standup_config(self, monkeypatch, tmp_path):
        import sqlite3

        from yeaboi import paths as paths_mod

        db = tmp_path / "sessions.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("CREATE TABLE standup_config (session_id TEXT, repo_path TEXT)")
            conn.execute("INSERT INTO standup_config VALUES ('s1', '/team/repo')")
            conn.execute("INSERT INTO standup_config VALUES ('s2', '')")
        monkeypatch.setattr(paths_mod, "DB_PATH", db)
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: tmp_path / ".env")
        monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
        self._seed()
        assert os.environ.get("YEABOI_ALLOWED_PATHS") == "/team/repo"

    def test_noop_without_db(self, monkeypatch, tmp_path):
        from yeaboi import paths as paths_mod

        monkeypatch.setattr(paths_mod, "DB_PATH", tmp_path / "missing.db")
        monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
        self._seed()
        assert "YEABOI_ALLOWED_PATHS" not in os.environ


class TestStandupReviewCommand:
    def _args(self, *argv):
        return build_parser().parse_args(["standup-review", *argv])

    def _patch(self, monkeypatch, review, filing=None):
        from yeaboi.agent.state import IssueFilingResult

        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr("yeaboi.standup.engine.run_transcript_review", lambda *a, **k: review)
        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.standup.engine.file_transcript_issues",
            lambda rid, **kw: seen.update(review_id=rid) or (filing or IssueFilingResult(filed=1)),
        )
        return seen

    def _review(self, **over):
        from yeaboi.agent.state import StandupGap, TranscriptReview

        base = dict(
            review_id=3,
            standup_date="2026-07-30",
            accuracy_note="Claims checked: 1 confirmed by the evidence.",
            gaps=(
                StandupGap(
                    fingerprint="fp1",
                    scope="product",
                    title="Standup misses Confluence comments",
                    root_cause="the collector reads pages but not comments",
                ),
            ),
        )
        base.update(over)
        return TranscriptReview(**base)

    def test_registered_as_a_subcommand(self):
        assert self._args().command == "standup-review"

    def test_text_output_lists_gaps(self, monkeypatch, capsys):
        self._patch(monkeypatch, self._review())
        buf = io.StringIO()
        assert _cmd_standup_review(self._args(), _console(buf)) == 0
        out = buf.getvalue()
        assert "Standup misses Confluence comments" in out
        assert "--file-issues" in out  # tells you how to actually file it

    def test_json_output(self, monkeypatch, capsys):
        self._patch(monkeypatch, self._review())
        assert _cmd_standup_review(self._args("--format", "json"), _console()) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["gaps"][0]["title"] == "Standup misses Confluence comments"

    def test_does_not_file_by_default(self, monkeypatch):
        seen = self._patch(monkeypatch, self._review())
        _cmd_standup_review(self._args(), _console())
        assert seen == {}

    def test_file_issues_reaches_the_filing_entry_point(self, monkeypatch):
        seen = self._patch(monkeypatch, self._review())
        buf = io.StringIO()
        _cmd_standup_review(self._args("--file-issues"), _console(buf))
        assert seen == {"review_id": 3}
        assert "Filed 1" in buf.getvalue()

    def test_file_issues_with_no_gaps_says_so(self, monkeypatch, capsys):
        seen = self._patch(monkeypatch, self._review(gaps=()))
        _cmd_standup_review(self._args("--file-issues"), _console())
        assert seen == {}
        assert "Nothing to file" in capsys.readouterr().err

    def test_config_suggestions_are_shown_as_never_filed(self, monkeypatch):
        from yeaboi.agent.state import StandupGap

        self._patch(
            monkeypatch,
            self._review(
                gaps=(),
                config_suggestions=(
                    StandupGap(scope="config", title="acme/infra is outside your scope", remedy="Add it."),
                ),
            ),
        )
        buf = io.StringIO()
        _cmd_standup_review(self._args(), _console(buf))
        out = buf.getvalue()
        assert "never filed" in out
        assert "Add it." in out

    def test_strict_exits_3_on_warnings(self, monkeypatch):
        self._patch(monkeypatch, self._review(warnings=("AI unavailable",)))
        assert _cmd_standup_review(self._args("--strict"), _console()) == 3

    def test_warnings_go_to_stderr(self, monkeypatch, capsys):
        self._patch(monkeypatch, self._review(warnings=("AI unavailable",)))
        _cmd_standup_review(self._args(), _console())
        assert "AI unavailable" in capsys.readouterr().err

    def test_no_session_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "")
        assert _cmd_standup_review(self._args(), _console()) == 2
        assert "no session found" in capsys.readouterr().err

    def test_list_gaps_reads_the_ledger(self, monkeypatch, tmp_path):
        from yeaboi.paths import get_db_path
        from yeaboi.standup.store import StandupStore

        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        with StandupStore(get_db_path()) as store:
            store.upsert_gap_issue("fp1", category="c", title="A tracked gap", issue_number=7, state="filed")
        buf = io.StringIO()
        assert _cmd_standup_review(self._args("--list-gaps"), _console(buf)) == 0
        assert "A tracked gap" in buf.getvalue()
        assert "#7" in buf.getvalue()


class TestStandupReviewInputs:
    """The positional form, stdin, and paths dragged out of a file manager."""

    def _args(self, *argv):
        return build_parser().parse_args(["standup-review", *argv])

    def _capture(self, monkeypatch) -> dict:
        from yeaboi.agent.state import TranscriptReview

        seen: dict = {}
        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr(
            "yeaboi.standup.engine.run_transcript_review",
            lambda sid, **kw: seen.update(kw) or TranscriptReview(),
        )
        return seen

    def test_positional_paths_reach_the_engine(self, monkeypatch):
        seen = self._capture(monkeypatch)
        _cmd_standup_review(self._args("/tmp/a.vtt", "/tmp/b.vtt"), _console())
        assert seen["transcript_paths"] == ["/tmp/a.vtt", "/tmp/b.vtt"]

    def test_positional_and_flag_forms_combine(self, monkeypatch):
        seen = self._capture(monkeypatch)
        _cmd_standup_review(self._args("/tmp/b.vtt", "--transcript", "/tmp/a.vtt"), _console())
        assert set(seen["transcript_paths"]) == {"/tmp/a.vtt", "/tmp/b.vtt"}

    def test_transcript_text_flag_reaches_the_engine(self, monkeypatch):
        seen = self._capture(monkeypatch)
        _cmd_standup_review(self._args("--transcript-text", "Alice: hi"), _console())
        assert seen["transcript_text"] == "Alice: hi"
        assert seen["transcript_paths"] is None

    def test_dash_reads_stdin(self, monkeypatch):
        seen = self._capture(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO("Alice: shipped auth\nBob: reviewed"))
        _cmd_standup_review(self._args("-"), _console())
        assert seen["transcript_text"] == "Alice: shipped auth\nBob: reviewed"
        # "-" must never reach the engine as a filename: sweep_and_review does a
        # bare Path() on everything it is handed.
        assert seen["transcript_paths"] is None

    def test_dash_does_not_override_an_explicit_text_flag(self, monkeypatch):
        seen = self._capture(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
        _cmd_standup_review(self._args("-", "--transcript-text", "explicit"), _console())
        assert seen["transcript_text"] == "explicit"

    def test_a_terminal_dragged_path_is_unquoted(self, monkeypatch):
        seen = self._capture(monkeypatch)
        _cmd_standup_review(self._args("'/tmp/My Meetings/a.vtt'"), _console())
        assert seen["transcript_paths"] == ["/tmp/My Meetings/a.vtt"]

    def test_an_iterm_dragged_path_is_unescaped(self, monkeypatch):
        seen = self._capture(monkeypatch)
        _cmd_standup_review(self._args("/tmp/My\\ Meetings/a.vtt"), _console())
        assert seen["transcript_paths"] == ["/tmp/My Meetings/a.vtt"]

    def test_no_inputs_leaves_the_sweep_alone(self, monkeypatch):
        seen = self._capture(monkeypatch)
        _cmd_standup_review(self._args(), _console())
        assert seen["transcript_paths"] is None
        assert seen["transcript_text"] == ""

    def test_import_is_named_so_the_user_can_see_which_day_it_hit(self, monkeypatch, capsys, tmp_path):
        from yeaboi.agent.state import TranscriptReview, TranscriptSource

        monkeypatch.setattr("yeaboi.cli._resolve_cli_session", lambda s: "sid")
        monkeypatch.setattr(
            "yeaboi.standup.engine.run_transcript_review",
            lambda sid, **kw: TranscriptReview(
                sources=(
                    TranscriptSource(
                        filename="2026-07-30-pasted.txt", covered_date="2026-07-30", attribution="labelled"
                    ),
                )
            ),
        )
        _cmd_standup_review(self._args("--transcript-text", "Alice: hi"), _console())
        out = capsys.readouterr().out
        assert "2026-07-30-pasted.txt" in out
        assert "2026-07-30" in out


class TestTranscriptReminderCommand:
    """The second scheduled job: passive, tiny, and silent when it has nothing
    to say — a job that only speaks when it matters is one you leave installed."""

    @pytest.fixture(autouse=True)
    def _no_real_logging(self, monkeypatch):
        # The handler is a scheduled-run concern, not what these tests are about,
        # and configure_logging() latches globally — running it for real here
        # silently no-ops the later test_logging_setup assertions.
        monkeypatch.setattr("yeaboi.logging_setup.configure_logging", lambda *a, **k: None)
        monkeypatch.setattr("yeaboi.logging_setup.attach_mode_handler", lambda *a, **k: None)

    def _args(self, session="s1"):
        from yeaboi.cli import build_parser

        return build_parser().parse_args(["--standup-remind-transcript", "--standup-session", session])

    def _run(self, monkeypatch, nudge, *, session="s1"):
        from yeaboi.cli import _run_transcript_reminder

        sent: list[tuple[str, str]] = []
        monkeypatch.setattr("yeaboi.sessions.SessionStore.get_latest_session_id", lambda self: session)
        monkeypatch.setattr("yeaboi.standup.engine.transcript_nudge", lambda sid, **kw: nudge)
        monkeypatch.setattr("yeaboi.standup.delivery.notify_desktop", lambda t, b: sent.append((t, b)) or True)
        return _run_transcript_reminder(self._args(session)), sent

    def _nudge(self, **over):
        from yeaboi.agent.state import TranscriptNudge

        base = dict(missed_dates=("2026-07-30",), streak=5, level="reminder", message="5 standups unchecked")
        base.update(over)
        return TranscriptNudge(**base)

    def test_flag_is_registered(self):
        assert self._args().standup_remind_transcript is True

    def test_notifies_when_standups_went_unchecked(self, monkeypatch, capsys):
        code, sent = self._run(monkeypatch, self._nudge())
        assert code == 0
        assert sent == [("Standup transcript", "5 standups unchecked")]
        assert "5 standups unchecked" in capsys.readouterr().out

    def test_silent_when_there_is_nothing_to_say(self, monkeypatch):
        from yeaboi.agent.state import TranscriptNudge

        code, sent = self._run(monkeypatch, TranscriptNudge())
        assert code == 0
        assert sent == []

    def test_no_session_exits_2(self, monkeypatch, capsys):
        code, _sent = self._run(monkeypatch, self._nudge(), session="")
        assert code == 2
        assert "no session found" in capsys.readouterr().err

    def test_a_failure_never_escapes(self, monkeypatch, capsys):
        from yeaboi.cli import _run_transcript_reminder

        def _boom(sid, **kw):
            raise RuntimeError("db gone")

        monkeypatch.setattr("yeaboi.sessions.SessionStore.get_latest_session_id", lambda self: "s1")
        monkeypatch.setattr("yeaboi.standup.engine.transcript_nudge", _boom)
        assert _run_transcript_reminder(self._args()) == 1
        assert "transcript reminder failed" in capsys.readouterr().err


class TestStandupTranscriptFlag:
    def test_review_is_on_by_default(self):
        assert build_parser().parse_args(["standup"]).review_transcripts is True

    def test_no_transcript_review_turns_it_off(self):
        assert build_parser().parse_args(["standup", "--no-transcript-review"]).review_transcripts is False
