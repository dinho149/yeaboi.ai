"""Tests for `yeaboi ceremonies` (cli.py::_cmd_ceremonies).

This handler is on the unattended path — ``ceremonies run --scheduled`` is what
the installed OS job invokes — so the properties that matter are its exit codes
and its refusal to prompt. The other half is ordering: the store validates, and
only then does a job get installed, because a job installed for a ceremony the
store refused is one nothing can describe or remove.
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from yeaboi.agent.state import CeremonyRun
from yeaboi.ceremonies.store import CeremonyStore
from yeaboi.cli import _cmd_ceremonies, build_parser


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway db, a resolved session, and a scheduler that only records."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    monkeypatch.setattr("yeaboi.mcp.tools_sessions.resolve_session_id", lambda sid="": sid or "s1")
    installed: dict[str, str] = {}
    monkeypatch.setattr(
        "yeaboi.ceremonies.scheduler.install_ceremony",
        lambda sid, name, at, weekdays: installed.setdefault(name, f"{at} {weekdays}") and "installed" or "installed",
    )
    monkeypatch.setattr(
        "yeaboi.ceremonies.scheduler.remove_ceremony",
        lambda sid, name: (installed.pop(name, None), "removed")[1],
    )
    monkeypatch.setattr("yeaboi.ceremonies.scheduler.installed_ceremonies", lambda sid: sorted(installed))
    return {"db": db, "installed": installed}


def _run(*argv) -> int:
    args = build_parser().parse_args(["ceremonies", *argv])
    # Mirrors cli.py's dispatcher: in JSON mode the human-facing console is
    # bound to stderr so stdout stays machine-clean.
    return _cmd_ceremonies(args, Console(stderr=getattr(args, "format", "text") == "json"))


def _add(*extra) -> int:
    return _run("add", "morning-standup", "--mode", "standup", "--at", "09:00", *extra)


class TestAdd:
    def test_declares_and_installs(self, env):
        assert _add() == 0
        with CeremonyStore(env["db"]) as store:
            ceremony = store.get("s1", "morning-standup")
        assert ceremony.mode == "standup"
        assert env["installed"] == {"morning-standup": "09:00 1-5"}

    def test_args_and_channels_are_parsed(self, env):
        assert _add("--arg", "days=3", "--channels", "slack,email") == 0
        with CeremonyStore(env["db"]) as store:
            ceremony = store.get("s1", "morning-standup")
        assert ceremony.args == (("days", "3"),)
        assert ceremony.channels == ("slack", "email")

    def test_the_modes_own_cadence_is_the_default(self, env):
        # The weekly report should not silently become a daily one.
        assert _run("add", "weekly-report", "--mode", "report", "--at", "08:00") == 0
        with CeremonyStore(env["db"]) as store:
            assert store.get("s1", "weekly-report").weekdays == "1"

    def test_a_refused_mode_exits_2_and_installs_nothing(self, env, capsys):
        assert _run("add", "friday-retro", "--mode", "retro", "--at", "16:00") == 2
        assert "live board" in capsys.readouterr().err
        assert env["installed"] == {}

    def test_a_malformed_arg_exits_2(self, env, capsys):
        assert _add("--arg", "days") == 2
        assert "KEY=VALUE" in capsys.readouterr().err

    def test_a_name_the_store_refuses_installs_no_job(self, env):
        # Ordering: the store validates first. A job installed for a ceremony
        # nothing can describe is a job nothing can remove.
        with pytest.raises(ValueError, match="scheduled-job label"):
            _run("add", "Morning Standup", "--mode", "standup", "--at", "09:00")
        assert env["installed"] == {}


class TestPauseResumeRemove:
    def test_pause_removes_the_job_but_keeps_the_declaration(self, env):
        _add()
        assert _run("pause", "morning-standup") == 0
        assert env["installed"] == {}
        with CeremonyStore(env["db"]) as store:
            assert store.get("s1", "morning-standup").enabled is False

    def test_resume_puts_it_back(self, env):
        _add()
        _run("pause", "morning-standup")
        assert _run("resume", "morning-standup") == 0
        assert "morning-standup" in env["installed"]

    def test_pausing_something_that_does_not_exist_exits_1(self, env, capsys):
        assert _run("pause", "nope") == 1
        assert "no ceremony" in capsys.readouterr().err

    def test_remove_takes_the_job_with_it(self, env):
        _add()
        assert _run("remove", "morning-standup") == 0
        assert env["installed"] == {}

    def test_removing_something_that_does_not_exist_exits_1(self, env):
        assert _run("remove", "nope") == 1


class TestListAndDrift:
    def test_reports_a_job_with_no_declaration(self, env, capsys):
        env["installed"]["ghost"] = "09:00 1-5"
        _add()
        _run("list")
        assert "installed for 'ghost'" in capsys.readouterr().out

    def test_reports_a_paused_ceremony_whose_job_survived(self, env, capsys):
        _add()
        with CeremonyStore(env["db"]) as store:
            store.set_enabled("s1", "morning-standup", False)  # job left behind on purpose
        _run("list")
        assert "paused but its job is still installed" in capsys.readouterr().out

    def test_reports_a_declaration_with_no_job(self, env, capsys):
        _add()
        env["installed"].clear()
        _run("list")
        assert "has no scheduled job" in capsys.readouterr().out

    def test_a_clean_setup_reports_no_drift(self, env, capsys):
        _add()
        _run("list")
        assert "!" not in capsys.readouterr().out

    def test_json_is_machine_clean(self, env, capsys):
        _add()
        capsys.readouterr()  # drop the add's human output
        _run("list", "--format", "json")
        captured = capsys.readouterr()
        assert json.loads(captured.out)["ceremonies"][0]["name"] == "morning-standup"

    def test_json_carries_the_drift_the_text_path_reports(self, env, capsys):
        # A scripted caller is exactly who cannot notice a morning going quiet,
        # so the one gap the feature exists to surface must not be text-only.
        env["installed"]["ghost"] = "09:00 1-5"
        _add()
        capsys.readouterr()
        _run("list", "--format", "json")
        payload = json.loads(capsys.readouterr().out)
        assert "ghost" in payload["installed_jobs"]
        assert any("ghost" in line for line in payload["drift"])


class TestRunExitCodes:
    def test_a_failed_run_exits_1(self, env, monkeypatch):
        _add()
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda *a, **k: CeremonyRun(ceremony="morning-standup", outcome="failed", error="boom"),
        )
        assert _run("run", "morning-standup") == 1

    @pytest.mark.parametrize("outcome", ["ok", "skipped_stale", "skipped_over_cap", "skipped_paused"])
    def test_a_declined_run_is_not_a_failure(self, env, monkeypatch, outcome):
        # The guards doing their job must not read as a crash, or a scheduled
        # wrapper fills the logs with alarms about working behaviour.
        _add()
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda *a, **k: CeremonyRun(ceremony="morning-standup", outcome=outcome),
        )
        assert _run("run", "morning-standup") == 0

    def test_an_unknown_ceremony_exits_1(self, env, capsys):
        assert _run("run", "nope") == 1
        assert "no ceremony" in capsys.readouterr().err

    def test_the_scheduled_flag_reaches_the_engine(self, env, monkeypatch):
        _add()
        seen = {}

        def _fake(name, **kwargs):
            seen.update(kwargs)
            return CeremonyRun(ceremony=name, outcome="ok")

        monkeypatch.setattr("yeaboi.ceremonies.engine.run_ceremony", _fake)
        _run("run", "morning-standup", "--scheduled")
        assert seen["scheduled"] is True

    def test_a_scheduled_run_gets_no_progress_callback_in_json_mode(self, env, monkeypatch):
        _add()
        seen = {}
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda name, **kwargs: (seen.update(kwargs), CeremonyRun(ceremony=name, outcome="ok"))[1],
        )
        _run("run", "morning-standup", "--format", "json")
        assert seen["on_progress"] is None


class TestModes:
    def test_lists_what_can_and_cannot_be_scheduled(self, capsys):
        assert _run("modes") == 0
        out = capsys.readouterr().out
        assert "standup" in out
        assert "retro" in out  # the refusals are as useful as the offers

    def test_json_carries_both_halves(self, capsys):
        capsys.readouterr()
        _run("modes", "--format", "json")
        payload = json.loads(capsys.readouterr().out)
        assert {m["key"] for m in payload["schedulable"]} >= {"standup", "report"}
        assert "retro" in payload["refused"]
