"""Unit tests for the OS-native standup scheduler (launchd + crontab)."""

import plistlib
from unittest.mock import MagicMock

import pytest

from yeaboi.standup import scheduler


class TestHelpers:
    def test_parse_time(self):
        assert scheduler.parse_time("09:50") == (9, 50)

    def test_parse_time_invalid(self):
        with pytest.raises(ValueError):
            scheduler.parse_time("9am")
        with pytest.raises(ValueError):
            scheduler.parse_time("25:00")

    def test_run_time_subtracts_lead(self):
        # Standup 10:00, 10 min lead → fire 09:50.
        assert scheduler.run_time("10:00", 10) == (9, 50)
        assert scheduler.run_time_str("10:00", 10) == "09:50"

    def test_run_time_zero_lead(self):
        assert scheduler.run_time("10:00", 0) == (10, 0)

    def test_run_time_wraps_before_midnight(self):
        # 00:05 standup with 10 min lead wraps to 23:55 the prior day.
        assert scheduler.run_time("00:05", 10) == (23, 55)

    def test_weekday_list_range(self):
        assert scheduler.weekday_list("1-5") == [1, 2, 3, 4, 5]

    def test_weekday_list_commas(self):
        assert scheduler.weekday_list("1,3,5") == [1, 3, 5]

    def test_weekday_list_empty_defaults_weekdays(self):
        assert scheduler.weekday_list("") == [1, 2, 3, 4, 5]

    def test_weekday_spec_compresses_runs(self):
        assert scheduler.weekday_spec({1, 2, 3, 4, 5}) == "1-5"
        assert scheduler.weekday_spec({1, 3, 5}) == "1,3,5"
        assert scheduler.weekday_spec({1, 2, 4, 5}) == "1-2,4-5"
        assert scheduler.weekday_spec({7}) == "7"
        assert scheduler.weekday_spec(set()) == "1-5"

    def test_weekday_spec_round_trips_through_weekday_list(self):
        for days in ({1, 2, 3, 4, 5}, {1, 3, 5}, {2, 3, 4, 6, 7}, {7}):
            assert set(scheduler.weekday_list(scheduler.weekday_spec(days))) == days

    def test_weekday_spec_label(self):
        assert scheduler.weekday_spec_label("1-5") == "Mon–Fri"
        assert scheduler.weekday_spec_label("1-7") == "Every day"
        assert scheduler.weekday_spec_label("1,3,5") == "Mon, Wed, Fri"
        assert scheduler.weekday_spec_label("6") == "Sat"

    def test_executable_args_shape(self, monkeypatch):
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/usr/local/bin/scrum-agent")
        args = scheduler._executable_args("sess-1")
        assert args == [
            "/usr/local/bin/scrum-agent",
            "--standup-run",
            "--standup-interactive",
            "--standup-session",
            "sess-1",
        ]

    def test_executable_args_fallback_to_module(self, monkeypatch):
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: None)
        args = scheduler._executable_args("sess-1")
        assert args[1:] == [
            "-m",
            "yeaboi.cli",
            "--standup-run",
            "--standup-interactive",
            "--standup-session",
            "sess-1",
        ]


class TestTwoJobKinds:
    """Two OS jobs per session. Every install/remove/status path takes a kind,
    and the teardown matrix is the whole safety property: a reminder still
    firing after the user disabled their standup is the worst failure here."""

    @pytest.fixture
    def macos(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: False)
        monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path)
        monkeypatch.setattr(scheduler, "_launcher_dir", lambda: tmp_path / "launchers")
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/bin/yeaboi")
        monkeypatch.setattr(scheduler.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stderr="")))
        return tmp_path

    def test_a_negative_lead_fires_after_the_standup(self):
        """The reminder needs no new time maths — run_time already wraps."""
        assert scheduler.run_time("10:00", -45) == (10, 45)
        assert scheduler.run_time("23:30", -45) == (0, 15)  # wraps past midnight

    def test_the_offset_is_readable_back_off_the_installed_job(self, macos):
        """The job IS the setting, so the wizard must be able to read it back.

        Recovering only "is one installed?" and defaulting the offset to 30
        would silently downgrade a user's "2 hours after" the next time they
        opened the wizard to change something else entirely.
        """
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 120)
        assert scheduler.transcript_reminder_offset("s1", "10:00") == 120

    def test_no_installed_job_means_no_offset(self, macos):
        assert scheduler.transcript_reminder_offset("s1", "10:00") == 0

    def test_an_offset_past_midnight_reads_back(self, macos):
        scheduler.install_transcript_reminder("s1", "23:30", "1-5", 60)
        assert scheduler.transcript_reminder_offset("s1", "23:30") == 60

    def test_a_malformed_standup_time_is_not_fatal(self, macos):
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 60)
        assert scheduler.transcript_reminder_offset("s1", "not a time") == 0

    def test_the_two_kinds_use_distinct_labels(self, macos):
        scheduler.install_schedule("s1", "10:00", "1-5")
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        assert (macos / "com.yeaboi.standup.s1.plist").exists()
        assert (macos / "com.yeaboi.standup-transcript.s1.plist").exists()

    def test_the_reminder_runs_the_cli_directly_with_no_terminal(self, macos):
        """The wrapper/osascript stack exists only to make the standup run
        interactive; a notification is passive and must not open a window."""
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        with (macos / "com.yeaboi.standup-transcript.s1.plist").open("rb") as fh:
            data = plistlib.load(fh)
        argv = data["ProgramArguments"]
        assert "--standup-remind-transcript" in argv
        assert not any("osascript" in str(a) or "yeaboi-standup" == str(a).split("/")[-1] for a in argv)
        assert data["StartCalendarInterval"][0]["Hour"] == 10
        assert data["StartCalendarInterval"][0]["Minute"] == 45

    def test_removing_one_kind_leaves_the_other(self, macos):
        scheduler.install_schedule("s1", "10:00", "1-5")
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        scheduler.remove_schedule("s1", kind=scheduler.JOB_TRANSCRIPT_REMINDER)
        assert (macos / "com.yeaboi.standup.s1.plist").exists()
        assert not (macos / "com.yeaboi.standup-transcript.s1.plist").exists()

    def test_remove_with_no_kind_tears_down_everything(self, macos):
        scheduler.install_schedule("s1", "10:00", "1-5")
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        scheduler.remove_schedule("s1")
        assert not list(macos.glob("*.plist"))

    def test_status_is_per_kind(self, macos):
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        assert scheduler.get_schedule_status("s1")["installed"] is False
        assert scheduler.get_schedule_status("s1", scheduler.JOB_TRANSCRIPT_REMINDER)["installed"] is True

    def test_removing_the_standup_does_not_touch_another_session(self, macos):
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        scheduler.install_transcript_reminder("s2", "10:00", "1-5", 45)
        scheduler.remove_schedule("s1")
        assert (macos / "com.yeaboi.standup-transcript.s2.plist").exists()


class TestTwoJobKindsCron:
    @pytest.fixture
    def cron(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: True)
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/bin/yeaboi")
        lines: list[str] = []
        monkeypatch.setattr(scheduler, "_read_crontab", lambda: list(lines))
        monkeypatch.setattr(scheduler, "_write_crontab", lambda new: (lines.clear(), lines.extend(new)))
        return lines

    def test_markers_cannot_collide(self):
        """'# yeaboi-standup s1' must not be a substring of the transcript one,
        or removing the standup would silently take the reminder with it."""
        standup = scheduler._cron_marker("s1")
        reminder = scheduler._cron_marker("s1", scheduler.JOB_TRANSCRIPT_REMINDER)
        assert standup not in reminder
        assert reminder not in standup

    def test_the_offset_reads_back_off_the_crontab_entry(self, cron):
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 90)
        assert scheduler.transcript_reminder_offset("s1", "10:00") == 90

    def test_removing_one_kind_leaves_the_other(self, cron):
        scheduler.install_schedule("s1", "10:00", "1-5")
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        assert len(cron) == 2
        scheduler.remove_schedule("s1", kind=scheduler.JOB_TRANSCRIPT_REMINDER)
        assert len(cron) == 1
        assert "--standup-run" in cron[0]

    def test_remove_with_no_kind_tears_down_everything(self, cron):
        scheduler.install_schedule("s1", "10:00", "1-5")
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        scheduler.remove_schedule("s1")
        assert cron == []

    def test_the_reminder_entry_fires_after_the_standup(self, cron):
        scheduler.install_schedule("s1", "10:00", "1-5", 10)
        scheduler.install_transcript_reminder("s1", "10:00", "1-5", 45)
        standup = next(ln for ln in cron if "--standup-run" in ln)
        reminder = next(ln for ln in cron if "--standup-remind-transcript" in ln)
        assert standup.startswith("50 9 ")
        assert reminder.startswith("45 10 ")


class TestLaunchd:
    def test_install_writes_plist_and_loads(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: False)
        monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path)
        monkeypatch.setattr(scheduler, "_launcher_dir", lambda: tmp_path / "launchers")
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/bin/scrum-agent")
        run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
        monkeypatch.setattr(scheduler.subprocess, "run", run)

        msg = scheduler.install_schedule("sess-1", "10:00", "1-5")
        plist_file = tmp_path / "com.yeaboi.standup.sess-1.plist"
        assert plist_file.exists()
        with plist_file.open("rb") as fh:
            data = plistlib.load(fh)
        # launchd executes the wrapper directly so macOS Background Task
        # Management shows "yeaboi-standup" (not "osascript") as the item name.
        assert data["ProgramArguments"] == [str(tmp_path / "launchers" / "standup-sess-1" / "yeaboi-standup")]
        assert len(data["StartCalendarInterval"]) == 5
        assert data["StartCalendarInterval"][0]["Hour"] == 9
        assert data["StartCalendarInterval"][0]["Minute"] == 50
        # The wrapper opens Terminal via osascript; run.sh holds the CLI command.
        wrapper = tmp_path / "launchers" / "standup-sess-1" / "yeaboi-standup"
        run_script = tmp_path / "launchers" / "standup-sess-1" / "run.sh"
        assert wrapper.exists() and run_script.exists()
        assert wrapper.stat().st_mode & 0o111 and run_script.stat().st_mode & 0o111
        assert "osascript" in wrapper.read_text() and "Terminal" in wrapper.read_text()
        script = run_script.read_text()
        assert "--standup-run" in script and "--standup-interactive" in script
        assert "launchd" in msg

    def test_wrapper_quotes_paths_with_spaces(self, monkeypatch, tmp_path):
        # Regression: the real launcher dir lives under "Application Support";
        # the unquoted path used to split at the space and every fire failed.
        monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
        monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path)
        monkeypatch.setattr(scheduler, "_launcher_dir", lambda: tmp_path / "Application Support" / "yeaboi")
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/bin/scrum-agent")
        monkeypatch.setattr(scheduler.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stderr="")))

        scheduler.install_schedule("sess-1", "10:00", "1-5")
        wrapper_text = (tmp_path / "Application Support" / "yeaboi" / "standup-sess-1" / "yeaboi-standup").read_text()
        run_path = str(tmp_path / "Application Support" / "yeaboi" / "standup-sess-1" / "run.sh")
        # The AppleScript hands Terminal a shell-quoted path, so the space survives.
        assert f"'{run_path}'" in wrapper_text
        assert f" {run_path}" not in wrapper_text.replace(f"'{run_path}'", "")

    def test_install_removes_legacy_launcher(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
        monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path)
        monkeypatch.setattr(scheduler, "_launcher_dir", lambda: tmp_path / "launchers")
        monkeypatch.setattr(scheduler.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stderr="")))
        legacy = tmp_path / "launchers" / "standup-sess-1.sh"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("#!/bin/sh\nold\n")

        scheduler.install_schedule("sess-1", "10:00")
        assert not legacy.exists()

    def test_sunday_maps_to_zero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
        monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path)
        monkeypatch.setattr(scheduler, "_launcher_dir", lambda: tmp_path / "launchers")
        monkeypatch.setattr(scheduler.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stderr="")))
        scheduler.install_schedule("s", "10:00", "7")
        with (tmp_path / "com.yeaboi.standup.s.plist").open("rb") as fh:
            data = plistlib.load(fh)
        assert data["StartCalendarInterval"][0]["Weekday"] == 0

    def test_status_and_remove(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: True)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: False)
        monkeypatch.setattr(scheduler, "_launch_agents_dir", lambda: tmp_path)
        monkeypatch.setattr(scheduler, "_launcher_dir", lambda: tmp_path / "launchers")
        monkeypatch.setattr(scheduler.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stderr="")))
        scheduler.install_schedule("sess-1", "10:00")
        assert scheduler.get_schedule_status("sess-1")["installed"] is True
        scheduler.remove_schedule("sess-1")
        assert scheduler.get_schedule_status("sess-1")["installed"] is False
        # Wrapper + run.sh directory also cleaned up.
        assert not (tmp_path / "launchers" / "standup-sess-1").exists()


class TestCron:
    def test_install_appends_entry(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: True)
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/bin/scrum-agent")
        monkeypatch.setattr(scheduler, "_read_crontab", lambda: ["# existing"])
        written = {}
        monkeypatch.setattr(scheduler, "_write_crontab", lambda lines: written.setdefault("lines", lines))

        msg = scheduler.install_schedule("sess-1", "10:00", "1-5")
        entry = written["lines"][-1]
        assert entry.startswith("50 9 * * 1,2,3,4,5 ")
        assert "--standup-run" in entry
        assert "# yeaboi-standup sess-1" in entry
        assert "crontab" in msg

    def test_install_replaces_existing_for_session(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: True)
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/bin/scrum-agent")
        monkeypatch.setattr(
            scheduler,
            "_read_crontab",
            lambda: ["0 8 * * 1 old # yeaboi-standup sess-1", "# unrelated"],
        )
        written = {}
        monkeypatch.setattr(scheduler, "_write_crontab", lambda lines: written.setdefault("lines", lines))
        scheduler.install_schedule("sess-1", "10:00")
        # old sess-1 entry removed, unrelated kept, one new entry added.
        lines = written["lines"]
        assert "# unrelated" in lines
        assert sum(1 for ln in lines if "sess-1" in ln) == 1
        assert lines[-1].startswith("50 9")

    def test_remove_filters_marker(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: True)
        monkeypatch.setattr(
            scheduler,
            "_read_crontab",
            lambda: ["50 9 * * 1 cmd # yeaboi-standup sess-1", "# keep"],
        )
        written = {}
        monkeypatch.setattr(scheduler, "_write_crontab", lambda lines: written.setdefault("lines", lines))
        msg = scheduler.remove_schedule("sess-1")
        assert written["lines"] == ["# keep"]
        assert "Removed" in msg

    def test_remove_missing_returns_message(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: True)
        monkeypatch.setattr(scheduler, "_read_crontab", lambda: ["# nothing here"])
        assert "No crontab schedule" in scheduler.remove_schedule("sess-1")


class TestUnsupportedPlatform:
    def test_install_unsupported(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: False)
        msg = scheduler.install_schedule("sess-1", "10:00")
        assert "not supported" in msg

    def test_status_unsupported(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_is_macos", lambda: False)
        monkeypatch.setattr(scheduler, "_is_linux", lambda: False)
        assert scheduler.get_schedule_status("sess-1") == {"platform": "unsupported", "installed": False, "path": ""}
