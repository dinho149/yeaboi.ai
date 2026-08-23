"""Tests for the ceremony store (ceremonies/store.py).

Two things carry the weight here. Validation lives in ``save`` rather than at
each surface, because a name accepted by the CLI and rejected by the TUI is how
a scheduled job ends up named after something nobody can remove. And the ledger
records every fired run including the ones the guards declined — the reason a
run did not happen is the part that was always getting thrown away.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies.store import CeremonyStore, valid_name


@pytest.fixture()
def store(tmp_path):
    with CeremonyStore(db_path=tmp_path / "sessions.db") as s:
        yield s


def _ceremony(**overrides) -> Ceremony:
    base = {
        "session_id": "s1",
        "name": "morning-standup",
        "mode": "standup",
        "args": (("days", "1"),),
        "channels": ("terminal", "slack"),
    }
    return Ceremony(**{**base, **overrides})


class TestValidName:
    @pytest.mark.parametrize("name", ["standup", "morning-standup", "week_1.report", "a"])
    def test_accepts_what_a_job_label_can_hold(self, name):
        assert valid_name(name)

    @pytest.mark.parametrize(
        "name",
        ["", "-leading", "Upper", "has space", "../escape", "sixty-five" + "x" * 60, "semi;colon"],
    )
    def test_rejects_what_it_cannot(self, name):
        assert not valid_name(name)


class TestSaveAndRead:
    def test_round_trips_every_field(self, store):
        saved = store.save(_ceremony(monthly_cap_usd=4.5, stale_after_min=45, weekdays="1,3,5", at="08:15"))
        read = store.get("s1", "morning-standup")
        assert read == saved
        # The args pairs survive as pairs, not as a dict rendered back badly.
        assert read.args == (("days", "1"),)
        assert read.channels == ("terminal", "slack")
        assert read.monthly_cap_usd == 4.5

    def test_created_at_survives_an_edit(self, store):
        first = store.save(_ceremony())
        second = store.save(replace(first, at="10:00"))
        assert second.created_at == first.created_at
        assert second.at == "10:00"
        assert second.updated_at >= first.updated_at

    def test_an_edit_that_forgot_created_at_does_not_lose_it(self, store):
        # Every surface builds the edited record its own way; the store keeps
        # the original creation stamp rather than trusting the caller to carry it.
        first = store.save(_ceremony())
        second = store.save(_ceremony(at="11:00"))
        assert second.created_at == first.created_at

    def test_list_is_scoped_to_the_session(self, store):
        store.save(_ceremony())
        store.save(_ceremony(session_id="s2", name="other"))
        assert [c.name for c in store.list("s1")] == ["morning-standup"]
        assert len(store.list()) == 2

    def test_remove_reports_whether_it_removed_anything(self, store):
        store.save(_ceremony())
        assert store.remove("s1", "morning-standup") is True
        assert store.remove("s1", "morning-standup") is False
        assert store.get("s1", "morning-standup") is None

    def test_set_enabled_toggles_and_misses_cleanly(self, store):
        store.save(_ceremony())
        assert store.set_enabled("s1", "morning-standup", False).enabled is False
        assert store.set_enabled("s1", "nope", False) is None

    def test_mark_fired_on_a_removed_ceremony_is_silent(self, store):
        # A ceremony deleted while its run was in flight must not turn a
        # delivered standup into an error.
        store.mark_fired("s1", "gone")  # no raise
        assert store.get("s1", "gone") is None


class TestSaveRefuses:
    def test_a_name_a_job_label_cannot_hold(self, store):
        with pytest.raises(ValueError, match="scheduled-job label"):
            store.save(_ceremony(name="Morning Standup"))

    def test_a_mode_the_catalog_refuses_by_design(self, store):
        with pytest.raises(ValueError, match="live board"):
            store.save(_ceremony(mode="retro"))

    def test_a_mode_that_does_not_exist(self, store):
        with pytest.raises(ValueError, match="unknown ceremony mode"):
            store.save(_ceremony(mode="brainstorm"))

    def test_a_ceremony_with_nowhere_to_land(self, store):
        with pytest.raises(ValueError, match="tell nobody"):
            store.save(_ceremony(channels=()))

    def test_a_ceremony_with_no_session(self, store):
        with pytest.raises(ValueError, match="needs a session"):
            store.save(_ceremony(session_id=""))

    @pytest.mark.parametrize("at", ["mornings", "9am", "25:00", "09-30", ""])
    def test_a_time_nothing_downstream_can_parse(self, store, at):
        with pytest.raises(ValueError, match="invalid time"):
            store.save(_ceremony(at=at))

    @pytest.mark.parametrize("weekdays", ["mon-fri", "weekdays", "1-9", "0", "8"])
    def test_a_weekday_spec_nothing_downstream_can_parse(self, store, weekdays):
        with pytest.raises(ValueError, match="invalid weekdays"):
            store.save(_ceremony(weekdays=weekdays))

    def test_a_misspelled_channel(self, store):
        # The quieter version of no channel at all: the fan-out records it as
        # undelivered and the run still reports "ok".
        with pytest.raises(ValueError, match="unknown delivery channel"):
            store.save(_ceremony(channels=("slak",)))


class TestAnUnparseableRowNeverLands:
    """The reason ``at``/``weekdays`` are validated at the write and not the read.

    Every reading surface re-parses both — the installer, ``cadence_label`` in
    each listing, ``next_fire`` on the TUI page. A row that stores cleanly and
    raises on read is not a bad row; it is one that takes the Ceremonies screen
    down every time it is drawn, recoverable only from the terminal.
    """

    def test_every_read_surface_survives_whatever_the_store_accepted(self, store):
        from yeaboi.ceremonies.render import cadence_label, format_ceremonies_rich, next_fire
        from yeaboi.ceremonies.scheduler import weekday_list

        for at, weekdays in (("00:00", "7"), ("23:59", "1-5"), ("08:15", "1,3,5")):
            saved = store.save(_ceremony(name=f"c-{weekdays.replace(',', '')}-{at[:2]}", at=at, weekdays=weekdays))
            assert cadence_label(saved)
            assert next_fire(saved, None)
            assert weekday_list(saved.weekdays)
        assert format_ceremonies_rich(store.list("s1"), {}) is not None


class TestLedger:
    def _run(self, **overrides) -> CeremonyRun:
        base = {
            "ceremony": "morning-standup",
            "session_id": "s1",
            "outcome": "ok",
            "cost_usd": 0.20,
            "fired_at": "2026-08-17T09:00:00+00:00",
        }
        return CeremonyRun(**{**base, **overrides})

    def test_a_run_is_recorded_with_its_delivery_results(self, store):
        store.record_run(self._run(delivery=(("slack", True), ("email", False)), detail="On track"))
        (row,) = store.runs("s1")
        assert row.delivery == (("slack", True), ("email", False))
        assert row.detail == "On track"

    def test_a_failed_run_keeps_its_reason(self, store):
        # The whole point of the ledger: the fact of a failure is useless
        # without the reason for it.
        store.record_run(self._run(outcome="failed", error="jira 401", cost_usd=0.0))
        assert store.runs("s1")[0].error == "jira 401"

    def test_runs_are_newest_first_and_capped(self, store):
        for day in range(1, 6):
            store.record_run(self._run(fired_at=f"2026-08-0{day}T09:00:00+00:00"))
        rows = store.runs("s1", limit=2)
        assert len(rows) == 2
        assert rows[0].fired_at.startswith("2026-08-05")

    def test_last_run_is_the_newest_or_none(self, store):
        assert store.last_run("s1", "morning-standup") is None
        store.record_run(self._run())
        assert store.last_run("s1", "morning-standup").outcome == "ok"

    def test_month_spend_sums_only_the_runs_that_reached_an_engine(self, store):
        store.record_run(self._run(cost_usd=0.20))
        store.record_run(self._run(cost_usd=0.30))
        # A run the cap itself declined never reached an engine, so it spent
        # nothing — counting it would latch the cap permanently the first time
        # it bit. The 9.99 here cannot occur in practice (a guard returns before
        # _spend_probe is even taken); it is here so the exclusion is proven by
        # something other than a zero.
        store.record_run(self._run(outcome="skipped_over_cap", cost_usd=9.99))
        assert store.month_spend("s1", "morning-standup", "2026-08") == pytest.approx(0.50)

    def test_a_failed_run_still_counts_against_the_cap(self, store):
        # The expensive direction. An engine that makes its LLM call and then
        # raises on the way out has already spent the money; excluding it lets a
        # ceremony burn its cap every day forever with the cap seeing nothing.
        store.record_run(self._run(cost_usd=0.20))
        store.record_run(self._run(outcome="failed", cost_usd=5.00))
        assert store.month_spend("s1", "morning-standup", "2026-08") == pytest.approx(5.20)

    def test_month_spend_ignores_other_months_and_ceremonies(self, store):
        store.record_run(self._run(cost_usd=0.40))
        store.record_run(self._run(cost_usd=8.00, fired_at="2026-07-02T09:00:00+00:00"))
        store.record_run(self._run(ceremony="weekly-report", cost_usd=7.00))
        assert store.month_spend("s1", "morning-standup", "2026-08") == pytest.approx(0.40)


class TestSkipNext:
    def test_set_and_clear_round_trip(self, store):
        store.save(_ceremony())
        assert store.set_skip_next("s1", "morning-standup", "2026-08-18").skip_next == "2026-08-18"
        assert store.set_skip_next("s1", "morning-standup", "").skip_next == ""

    def test_an_unknown_ceremony_is_none_not_an_error(self, store):
        assert store.set_skip_next("s1", "nope", "2026-08-18") is None

    def test_a_junk_date_is_refused_at_the_write(self, store):
        # It would not crash a screen, but it would silently never match a slot
        # — a skip that reads as set and never takes effect.
        store.save(_ceremony())
        with pytest.raises(ValueError, match="skip_next"):
            store.set_skip_next("s1", "morning-standup", "next tuesday")

    def test_it_survives_the_json_round_trip(self, store):
        store.save(_ceremony(skip_next="2026-08-18"))
        assert store.get("s1", "morning-standup").skip_next == "2026-08-18"

    def test_a_row_written_before_the_field_existed_still_loads(self):
        # The downgrade case the JSON-blob hydrator promises: an older row
        # should lose a field it does not know, never fail to load.
        from yeaboi.ceremonies.store import _dict_to_ceremony

        legacy = {"session_id": "s1", "name": "morning-standup", "mode": "standup"}
        assert _dict_to_ceremony(legacy).skip_next == ""
