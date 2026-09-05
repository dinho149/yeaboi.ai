"""Tests for scheduler.reap_dead_jobs — uninstalling jobs that can never run again."""

import plistlib

import pytest

from yeaboi.ceremonies import scheduler


@pytest.fixture
def launch_agents(tmp_path, monkeypatch):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: agents)
    monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv, **_kw):
        calls.append(list(argv))

        class _Proc:
            returncode = 0
            stderr = ""

        return _Proc()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    return agents, calls


def _plist(agents, label, program):
    path = agents / f"{label}.plist"
    with path.open("wb") as fh:
        plistlib.dump({"Label": label, "ProgramArguments": [program]}, fh)
    return path


class TestReap:
    def test_a_standup_wrapper_whose_venv_is_gone_is_removed(self, launch_agents, tmp_path):
        agents, calls = launch_agents
        support = tmp_path / "Application Support" / "yeaboi" / "standup-s1"
        support.mkdir(parents=True)
        (support / "run.sh").write_text("#!/bin/sh\n# yeaboi\n/gone/.venv/bin/yeaboi --standup-run\n")
        wrapper = support / "yeaboi-standup"
        wrapper.write_text("#!/bin/sh\nexec osascript\n")
        path = _plist(agents, "com.yeaboi.standup.s1", str(wrapper))
        removed = scheduler.reap_dead_jobs()
        assert removed and "venv missing" in removed[0]
        assert not path.exists() and not support.exists()
        assert calls[0][:2] == ["launchctl", "unload"]

    def test_a_job_whose_program_exists_is_kept(self, launch_agents, tmp_path):
        agents, _calls = launch_agents
        program = tmp_path / "yeaboi"
        program.write_text("#!/bin/sh\n")
        path = _plist(agents, "com.yeaboi.ceremony.weekly.s1", str(program))
        assert scheduler.reap_dead_jobs() == []
        assert path.exists()

    def test_a_missing_program_is_removed(self, launch_agents, tmp_path):
        agents, _calls = launch_agents
        path = _plist(agents, "com.yeaboi.ceremony.weekly.s1", str(tmp_path / "nowhere"))
        removed = scheduler.reap_dead_jobs()
        assert removed and "program missing" in removed[0] and not path.exists()

    def test_a_ceremony_whose_mode_was_withdrawn_is_removed(self, launch_agents, tmp_path, monkeypatch):
        from yeaboi.agent.state import Ceremony

        agents, _calls = launch_agents
        program = tmp_path / "yeaboi"
        program.write_text("#!/bin/sh\n")
        path = _plist(agents, "com.yeaboi.ceremony.agent-digest.s1", str(program))

        class _Store:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def list(self):
                return [Ceremony(name="agent-digest", mode="agents-standup", session_id="s1")]

        monkeypatch.setattr("yeaboi.ceremonies.store.CeremonyStore", lambda: _Store())
        removed = scheduler.reap_dead_jobs()
        assert removed and "mode withdrawn: agents-standup" in removed[0] and not path.exists()

    def test_not_macos_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        assert scheduler.reap_dead_jobs() == []
