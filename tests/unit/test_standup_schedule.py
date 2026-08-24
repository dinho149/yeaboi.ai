"""What a finished schedule wizard does: the config write plus the OS jobs."""

import pytest

from yeaboi.standup import schedule
from yeaboi.standup.store import StandupStore

SESSION = "s1"


@pytest.fixture
def jobs(monkeypatch):
    """Stub the OS installers at the seam standup.schedule actually reaches."""
    import yeaboi.ceremonies.scheduler as scheduler

    calls = {"install": [], "reminder": [], "remove": [], "offset": 0}
    monkeypatch.setattr(scheduler, "install_schedule", lambda *a, **k: calls["install"].append(a) or "installed")
    monkeypatch.setattr(
        scheduler, "install_transcript_reminder", lambda *a, **k: calls["reminder"].append(a) or "reminder on"
    )
    monkeypatch.setattr(
        scheduler, "remove_schedule", lambda *a, **k: calls["remove"].append((*a, *sorted(k.items()))) or "removed"
    )
    monkeypatch.setattr(scheduler, "transcript_reminder_offset", lambda sid, t: calls["offset"])
    return calls


def _apply(db, **kw):
    defaults = {
        "enabled": True,
        "time": "09:30",
        "weekdays": "1-5",
        "lead_minutes": 15,
        "delivery_channels": ["terminal"],
    }
    return schedule.apply_schedule(SESSION, db_path=db, **{**defaults, **kw})


class TestApplySchedule:
    def test_saves_the_schedule_fields(self, tmp_path, jobs):
        db = tmp_path / "sessions.db"
        _apply(db)
        with StandupStore(db) as store:
            saved = store.load_config(SESSION)
        assert saved["enabled"] is True
        assert saved["time"] == "09:30"
        assert saved["lead_minutes"] == 15
        assert saved["delivery_channels"] == ["terminal"]

    def test_identity_and_scope_fields_pass_through_untouched(self, tmp_path, jobs):
        db = tmp_path / "sessions.db"
        with StandupStore(db) as store:
            store.save_config(
                SESSION,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                my_aliases="ana,ana.dev",
                tracker_sources=["azdevops"],
                team_members=["Ana", "Bo"],
                roster_configured=True,
                code_sources=["github"],
                github_owners=["acme"],
            )
        _apply(db, time="08:45")
        with StandupStore(db) as store:
            saved = store.load_config(SESSION)
        assert saved["time"] == "08:45"  # the wizard's own field moved
        assert saved["my_aliases"] == "ana,ana.dev"
        assert saved["tracker_sources"] == ["azdevops"]
        assert saved["team_members"] == ["Ana", "Bo"]
        assert saved["roster_configured"] is True
        assert saved["code_sources"] == ["github"]
        assert saved["github_owners"] == ["acme"]

    def test_enabling_installs_the_job_at_the_standup_time(self, tmp_path, jobs):
        # The lead is passed through, not pre-subtracted — the scheduler owns
        # the fire-time arithmetic.
        message = _apply(tmp_path / "sessions.db")
        assert jobs["install"] == [(SESSION, "09:30", "1-5", 15)]
        assert message.startswith("installed")

    def test_a_reminder_offset_installs_the_second_job(self, tmp_path, jobs):
        message = _apply(tmp_path / "sessions.db", remind_after=60)
        assert jobs["reminder"] == [(SESSION, "09:30", "1-5", 60)]
        assert "reminder on" in message

    def test_no_offset_tears_only_the_reminder_down(self, tmp_path, jobs):
        from yeaboi.ceremonies.scheduler import JOB_TRANSCRIPT_REMINDER

        _apply(tmp_path / "sessions.db", remind_after=0)
        assert jobs["remove"] == [(SESSION, ("kind", JOB_TRANSCRIPT_REMINDER))]
        assert jobs["install"]  # the standup job itself stays

    def test_disabling_removes_every_kind_and_installs_nothing(self, tmp_path, jobs):
        # A user who switched their standup off must not keep getting reminders.
        message = _apply(tmp_path / "sessions.db", enabled=False)
        assert jobs["install"] == [] and jobs["reminder"] == []
        assert jobs["remove"] == [(SESSION,)]
        assert message == "removed"


class TestCurrentSchedule:
    def test_defaults_with_nothing_saved(self, tmp_path, jobs):
        view = schedule.current_schedule(SESSION, db_path=tmp_path / "sessions.db")
        assert view["enabled"] is False
        assert view["time"] == "10:00"
        assert view["lead_minutes"] == 10
        assert view["weekdays"] == "1-5"
        assert view["delivery_channels"] == ["terminal"]
        assert "terminal" in view["valid_channels"]

    def test_reads_the_reminder_offset_back_off_the_os(self, tmp_path, jobs):
        # Nothing in the database records it — the installed job IS the setting.
        jobs["offset"] = 120
        assert schedule.current_schedule(SESSION, db_path=tmp_path / "sessions.db")["remind_after"] == 120

    def test_an_unknown_channel_never_survives_the_read(self, tmp_path, jobs):
        db = tmp_path / "sessions.db"
        with StandupStore(db) as store:
            store.save_config(SESSION, enabled=True, time="10:00", weekdays="1-5", delivery_channels=["carrier-duck"])
        assert schedule.current_schedule(SESSION, db_path=db)["delivery_channels"] == ["terminal"]

    def test_round_trips_what_apply_saved(self, tmp_path, jobs):
        db = tmp_path / "sessions.db"
        _apply(db, time="11:00", weekdays="1-3", lead_minutes=5, delivery_channels=["terminal", "desktop"])
        view = schedule.current_schedule(SESSION, db_path=db)
        assert (view["time"], view["weekdays"], view["lead_minutes"]) == ("11:00", "1-3", 5)
        assert view["delivery_channels"] == ["terminal", "desktop"]


class TestReminderPresets:
    def test_a_preset_is_kept(self):
        assert schedule.nearest_reminder_preset(60) == 60

    def test_off_the_grid_snaps_instead_of_vanishing(self):
        # The standup time can move after the reminder was installed. Falling
        # through to "no reminder" would tear the job down for a user who came
        # to change something else entirely.
        assert schedule.nearest_reminder_preset(45) == 30
        assert schedule.nearest_reminder_preset(100) == 120

    def test_zero_stays_off(self):
        assert schedule.nearest_reminder_preset(0) == 0
