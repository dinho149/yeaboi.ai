"""The inbound poll's OS job: an interval kind, not a ceremony.

Two things are load-bearing and neither is obvious. **Teardown is surgical** —
switching the Slack inbox off must not take the Monday delivery report with it,
and the reverse. And **the interval must divide 60**, because cron's `*/N` is
not a frequency: `*/7` fires at 0,7,…,56 and then leaves a four-minute gap, on
the platform least able to tell anybody it did.
"""

from __future__ import annotations

import plistlib

import pytest

from yeaboi.ceremonies import scheduler
from yeaboi.ceremonies.scheduler import (
    DEFAULT_POLL_MINUTES,
    JOB_SLACK_POLL,
    JOB_STANDUP,
    POLL_INTERVALS,
    _executable_args,
    _identity,
    _session_launcher_dir,
    ceremony_kind,
    install_slack_poll,
    remove_slack_poll,
    slack_poll_status,
)


@pytest.fixture(autouse=True)
def _mac(monkeypatch, tmp_path):
    """Pretend to be macOS with a throwaway LaunchAgents directory."""
    monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
    monkeypatch.setattr(scheduler, "_is_linux", lambda: False)
    monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setattr(
        scheduler, "_plist_path", lambda sid, kind=JOB_STANDUP: tmp_path / f"{_identity(kind)[0]}.plist"
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")


class TestIdentity:
    def test_the_poll_has_its_own_namespace(self):
        # com.yeaboi.standup-slack would be a lie, and the ceremony prefix is
        # glob-scanned by installed_ceremonies().
        label, marker = _identity(JOB_SLACK_POLL)
        assert label == "com.yeaboi.slack"
        assert marker == "# yeaboi-slack"

    def test_its_marker_cannot_be_confused_with_any_other(self):
        # Removal matches on a marker substring, so an overlap would let one
        # teardown take another kind's job with it.
        poll = _identity(JOB_SLACK_POLL)[1]
        others = [_identity(JOB_STANDUP)[1], _identity(ceremony_kind("morning"))[1]]
        assert all(poll not in other and other not in poll for other in others)

    def test_it_gets_an_explicit_launcher_directory(self):
        # Without a branch this falls through to the ceremony path and yields
        # "ceremony--<session>". Nothing writes there, but teardown rmtree's it.
        assert _session_launcher_dir("s1", JOB_SLACK_POLL).name == "slack-poll-s1"
        assert "ceremony-" not in _session_launcher_dir("s1", JOB_SLACK_POLL).name

    def test_it_invokes_the_poll_and_arms_its_guards(self):
        argv = _executable_args("", JOB_SLACK_POLL)
        assert argv[-3:] == ["slack", "poll", "--scheduled"]

    def test_it_is_not_session_scoped(self):
        # The token and channel are machine-wide; each event finds its session
        # through the anchor it answers.
        assert _executable_args("s1", JOB_SLACK_POLL) == _executable_args("s2", JOB_SLACK_POLL)


class TestInstall:
    def test_it_writes_an_interval_not_a_calendar_slot(self, _mac):
        install_slack_poll(minutes=10)
        with (_mac / "com.yeaboi.slack.plist").open("rb") as fh:
            plist = plistlib.load(fh)
        assert plist["StartInterval"] == 600
        assert "StartCalendarInterval" not in plist
        assert plist["RunAtLoad"] is False

    def test_it_carries_a_usable_path(self, _mac):
        # launchd hands a job /usr/bin:/bin, where git does not exist.
        install_slack_poll()
        with (_mac / "com.yeaboi.slack.plist").open("rb") as fh:
            assert plistlib.load(fh)["EnvironmentVariables"]["PATH"]

    @pytest.mark.parametrize("minutes", [7, 8, 9, 11, 45, 0, -5])
    def test_an_interval_cron_cannot_express_is_refused(self, minutes, _mac):
        message = install_slack_poll(minutes=minutes)
        assert "not a usable interval" in message
        assert not (_mac / "com.yeaboi.slack.plist").exists()

    @pytest.mark.parametrize("minutes", POLL_INTERVALS)
    def test_every_offered_interval_divides_an_hour(self, minutes):
        assert 60 % minutes == 0

    def test_no_token_means_no_job_at_all(self, monkeypatch, _mac):
        # A job that can only ever decline is noise 144 times a day.
        monkeypatch.setenv("SLACK_BOT_TOKEN", "")
        message = install_slack_poll()
        assert "Not installing" in message
        assert not (_mac / "com.yeaboi.slack.plist").exists()

    def test_a_token_without_a_channel_is_the_same(self, monkeypatch, _mac):
        monkeypatch.setenv("SLACK_CHANNEL_ID", "")
        assert "Not installing" in install_slack_poll()

    def test_the_default_is_one_of_the_offered_intervals(self):
        assert DEFAULT_POLL_MINUTES in POLL_INTERVALS


class TestStatusAndRemoval:
    def test_the_interval_is_read_back_off_the_installed_job(self, _mac):
        # Not from config: a stored copy is a second source of truth that can
        # disagree with what will actually fire.
        install_slack_poll(minutes=15)
        status = slack_poll_status()
        assert status["installed"] is True
        assert status["interval_min"] == 15

    def test_nothing_installed_reports_zero_rather_than_guessing(self, _mac):
        status = slack_poll_status()
        assert status["installed"] is False
        assert status["interval_min"] == 0

    def test_removing_the_poll_leaves_a_ceremony_alone(self, _mac, monkeypatch):
        removed: list[str] = []
        monkeypatch.setattr(scheduler, "_remove_launchd", lambda sid, kind: removed.append(kind) or "Removed")
        remove_slack_poll("s1")
        assert removed == [JOB_SLACK_POLL]

    def test_removing_the_standup_family_leaves_the_poll_alone(self, _mac, monkeypatch):
        removed: list[str] = []
        monkeypatch.setattr(scheduler, "_remove_launchd", lambda sid, kind: removed.append(kind) or "Removed")
        scheduler.remove_schedule("s1")
        assert JOB_SLACK_POLL not in removed
