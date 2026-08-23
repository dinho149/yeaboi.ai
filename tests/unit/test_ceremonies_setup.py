"""The surface-neutral half of a Ceremonies page (ceremonies/setup.py).

The drift lines and the write order carry the weight: the store says what is
declared and the OS says what will fire, and a pause that leaves the job
installed is the bug users actually report.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies import setup
from yeaboi.ceremonies.store import CeremonyStore


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway store and a scheduler that only records what it was asked."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    installed: set[str] = set()
    monkeypatch.setattr(
        setup.scheduler, "install_ceremony", lambda sid, name, at, wd: (installed.add(name), "installed")[1]
    )
    monkeypatch.setattr(setup.scheduler, "remove_ceremony", lambda sid, name: (installed.discard(name), "removed")[1])
    monkeypatch.setattr(setup.scheduler, "installed_ceremonies", lambda sid: sorted(installed))
    return {"db": db, "installed": installed}


def _save(db, **overrides) -> Ceremony:
    base = {"session_id": "s1", "name": "morning", "mode": "standup", "at": "09:00", "channels": ("terminal",)}
    with CeremonyStore(db) as store:
        return store.save(Ceremony(**{**base, **overrides}))


class TestDrift:
    def test_a_matching_pair_has_no_drift(self):
        declared = [Ceremony(name="a", enabled=True)]
        assert setup.drift_lines(declared, {"a"}) == []

    def test_a_job_with_no_declaration(self):
        assert "not declared here" in setup.drift_lines([], {"ghost"})[0]

    def test_a_paused_ceremony_whose_job_survived(self):
        lines = setup.drift_lines([Ceremony(name="a", enabled=False)], {"a"})
        assert "still installed" in lines[0]

    def test_a_declaration_with_no_job(self):
        lines = setup.drift_lines([Ceremony(name="a", enabled=True)], set())
        assert "re-add it" in lines[0]


class TestLoadPage:
    def test_no_session_reads_nothing(self, env):
        assert setup.load_page("") == ([], {}, {}, [])

    def test_the_last_run_and_the_month_spend_come_back(self, env):
        _save(env["db"])
        with CeremonyStore(env["db"]) as store:
            store.record_run(CeremonyRun(ceremony="morning", session_id="s1", outcome="ok", cost_usd=0.25))
        ceremonies, last, spend, drift = setup.load_page("s1", db_path=env["db"])
        assert [c.name for c in ceremonies] == ["morning"]
        assert last["morning"].outcome == "ok"
        assert spend["morning"] == pytest.approx(0.25)
        # Declared with no job installed by the fake scheduler.
        assert drift and "re-add it" in drift[0]


class TestDeclare:
    def test_it_saves_then_installs(self, env):
        stored, message = setup.declare(
            "s1", name="morning", mode="standup", at="08:30", channels=("terminal",), db_path=env["db"]
        )
        assert stored.at == "08:30"
        assert message == "installed"
        assert env["installed"] == {"morning"}

    def test_mode_defaults_fill_the_blanks(self, env):
        stored, _ = setup.declare("s1", name="morning", mode="standup", channels=("terminal",), db_path=env["db"])
        assert stored.at and stored.weekdays

    def test_an_unknown_mode_is_refused_before_anything_is_written(self, env):
        with pytest.raises(ValueError, match="unknown ceremony mode"):
            setup.declare("s1", name="morning", mode="nonsense", db_path=env["db"])
        assert env["installed"] == set()

    def test_a_bad_name_never_reaches_the_scheduler(self, env):
        with pytest.raises(ValueError, match="lowercase"):
            setup.declare("s1", name="Morning Standup!", mode="standup", db_path=env["db"])
        assert env["installed"] == set()

    def test_no_session_is_refused(self, env):
        with pytest.raises(ValueError, match="No saved session"):
            setup.declare("", name="morning", mode="standup", db_path=env["db"])


class TestSetEnabled:
    def test_pause_takes_the_job_down_and_keeps_the_declaration(self, env):
        setup.declare("s1", name="morning", mode="standup", channels=("terminal",), db_path=env["db"])
        ceremony, message = setup.set_enabled("s1", "morning", False, db_path=env["db"])
        assert ceremony is not None and ceremony.enabled is False
        assert message == "removed"
        assert env["installed"] == set()
        with CeremonyStore(env["db"]) as store:
            assert store.get("s1", "morning") is not None

    def test_resume_puts_the_job_back(self, env):
        setup.declare("s1", name="morning", mode="standup", channels=("terminal",), db_path=env["db"])
        setup.set_enabled("s1", "morning", False, db_path=env["db"])
        _ceremony, message = setup.set_enabled("s1", "morning", True, db_path=env["db"])
        assert message == "installed"
        assert env["installed"] == {"morning"}

    def test_an_unknown_name_touches_no_job(self, env):
        ceremony, message = setup.set_enabled("s1", "nope", False, db_path=env["db"])
        assert ceremony is None
        assert "no ceremony" in message


class TestSlackStatus:
    def test_write_only_slack_reports_why(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (False, "no SLACK_BOT_TOKEN"))
        status = setup.slack_status("s1")
        assert status == {
            "two_way": False,
            "why": "no SLACK_BOT_TOKEN",
            "identities": [],
            "linked": 0,
            "interval_min": 0,
        }

    def test_a_live_lane_carries_the_installed_interval(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.slack.identity.listing", lambda sid, db_path=None: [{"slack_user": "U1"}])
        monkeypatch.setattr(setup.scheduler, "slack_poll_status", lambda session_id="": {"interval_min": 15})
        status = setup.slack_status("s1")
        assert status["two_way"] is True
        assert status["linked"] == 1
        assert status["interval_min"] == 15

    def test_an_unreadable_mapping_does_not_take_the_page_down(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))

        def _boom(sid, db_path=None):
            raise RuntimeError("db locked")

        monkeypatch.setattr("yeaboi.slack.identity.listing", _boom)
        monkeypatch.setattr(setup.scheduler, "slack_poll_status", lambda session_id="": {"interval_min": 0})
        assert setup.slack_status("s1")["linked"] == 0


class TestRunSummary:
    def test_a_delivered_run_names_the_channels_and_the_cost(self):
        run = CeremonyRun(ceremony="morning", outcome="ok", cost_usd=0.5, delivery=(("slack", True), ("email", False)))
        assert setup.run_summary(run) == "morning ran ($0.50) → slack"

    def test_a_run_that_reached_nobody_says_nowhere(self):
        run = CeremonyRun(ceremony="morning", outcome="ok", delivery=(("slack", False),))
        assert "nowhere" in setup.run_summary(run)

    def test_a_failure_carries_its_reason(self):
        run = CeremonyRun(ceremony="morning", outcome="failed", error="the webhook 404'd")
        assert setup.run_summary(run) == "morning: failed — the webhook 404'd"

    def test_nothing_at_all_still_says_something(self):
        assert setup.run_summary(None) == "the run produced nothing"


class TestOptions:
    def test_every_schedulable_mode_is_offered_with_its_params(self):
        options = setup.mode_options()
        assert options and {"key", "label", "blurb", "est_cost_usd", "default_at", "default_weekdays", "params"} == set(
            options[0]
        )
        standup = next(o for o in options if o["key"] == "standup")
        assert [p["name"] for p in standup["params"]] == ["days"]

    def test_the_channels_come_from_delivery(self):
        from yeaboi.ceremonies.delivery import ALL_CHANNELS

        assert setup.channel_options() == list(ALL_CHANNELS)

    def test_the_add_hint_names_a_real_mode(self):
        from yeaboi.ceremonies import catalog

        assert catalog.lookup(setup.add_hint().split("--mode ")[1].split()[0]) is not None
