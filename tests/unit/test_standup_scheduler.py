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
