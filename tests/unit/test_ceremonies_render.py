"""Tests for the ceremonies Rich renderings (ceremonies/render.py).

One file per source module, and this one had been covered only sideways through
the CLI and screen tests — which is the wrong place to notice that a timestamp
is an hour out.

Two things here are load-bearing rather than cosmetic. ``local_stamp`` converts
the ledger's stored UTC to local wall clock, because a run recorded at 17:13 for
a ceremony scheduled at 18:13 reads as a bug in the scheduler. And
``OUTCOME_MARKS`` names a *tone* rather than a colour, so the CLI (a bare
console) and the TUI screen (a Theme) can each answer it their own way without
either spelling the other's palette.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta, timezone

import pytest
from rich.console import Console

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies import render


def _ceremony(**overrides) -> Ceremony:
    base = {
        "session_id": "s1",
        "name": "morning-standup",
        "mode": "standup",
        "channels": ("terminal",),
        "at": "09:00",
    }
    return Ceremony(**{**base, **overrides})


def _text(renderable, width: int = 100) -> str:
    console = Console(file=io.StringIO(), width=width)
    console.print(renderable)
    return console.file.getvalue()


class TestLocalStamp:
    def test_converts_the_stored_utc_to_local_wall_clock(self):
        # The bug this exists for: a ceremony set for 18:13 recording itself at
        # 17:13, because the stored string was being sliced rather than read.
        moment = datetime(2026, 8, 17, 18, 13).astimezone()
        assert render.local_stamp(moment.astimezone(UTC).isoformat()) == "2026-08-17 18:13"

    def test_the_short_form_drops_the_year_not_the_conversion(self):
        moment = datetime(2026, 8, 17, 18, 13).astimezone()
        stamp = render.local_stamp(moment.astimezone(UTC).isoformat(), with_date=False)
        assert stamp == "08-17 18:13"

    @pytest.mark.parametrize("raw", ["", "not-a-time", "17/08/2026 09:00"])
    def test_an_unparseable_stamp_is_shown_rather_than_raised(self, raw):
        # A history row with an odd stamp is still a row worth showing, and this
        # renderer is the last thing between a bad column and a blank screen.
        assert render.local_stamp(raw) == raw[:16].replace("T", " ")

    def test_a_bare_date_is_parseable_and_gets_a_midnight(self):
        # Not a fallback case: fromisoformat takes it, so it renders as a time.
        assert render.local_stamp("2026-08-17") == "2026-08-17 00:00"

    def test_an_offset_hour_survives_the_round_trip(self):
        fixed = datetime(2026, 8, 17, 9, 0, tzinfo=timezone(timedelta(hours=5)))
        expected = fixed.astimezone().strftime("%Y-%m-%d %H:%M")
        assert render.local_stamp(fixed.isoformat()) == expected


class TestOutcomeMarks:
    def test_every_recorded_outcome_has_a_mark(self):
        from yeaboi.agent.state import CEREMONY_OUTCOMES

        assert set(render.OUTCOME_MARKS) == set(CEREMONY_OUTCOMES)

    def test_a_skip_is_amber_and_a_failure_is_not(self):
        # Colouring a decision the guards made like a failure teaches people to
        # ignore both.
        assert render.outcome_mark("skipped_stale")[1] == "warn"
        assert render.outcome_mark("skipped_over_cap")[1] == "warn"
        assert render.outcome_mark("skipped_paused")[1] == "warn"
        assert render.outcome_mark("failed")[1] == "bad"
        assert render.outcome_mark("ok")[1] == "good"

    def test_the_tones_are_words_a_theme_can_answer(self):
        from yeaboi.ui.shared._components import CEREMONIES_THEME

        for _glyph, tone in render.OUTCOME_MARKS.values():
            assert hasattr(CEREMONIES_THEME, tone), tone

    def test_an_unknown_outcome_degrades_rather_than_raising(self):
        assert render.outcome_mark("invented") == ("?", "dim")


class TestOutcomeChip:
    def test_a_ceremony_that_never_ran_says_so(self):
        assert "never run" in _text(render.outcome_chip(None))

    def test_the_chip_carries_the_outcome_and_when(self):
        chip = _text(render.outcome_chip(CeremonyRun(outcome="ok", fired_at="2026-08-17T09:00:00+00:00")))
        assert "ok" in chip
        assert "✓" in chip


class TestCadenceAndNextFire:
    def test_the_cadence_reads_as_days_and_a_time(self):
        assert render.cadence_label(_ceremony(weekdays="1-5", at="09:00")) == "Mon–Fri at 09:00"

    def test_a_paused_ceremony_reports_the_pause_not_its_cadence(self):
        # The cadence of something that will not fire is trivia, and showing it
        # reads as a schedule. This is also where "(paused)" lives now — the
        # name column ellipsizes it away at the minimum terminal width.
        assert render.next_fire(_ceremony(enabled=False)) == "paused"
        assert render.next_fire(_ceremony(enabled=True)) == "Mon–Fri at 09:00"


class TestListings:
    def test_an_empty_schedule_explains_itself(self):
        out = _text(render.format_ceremonies_rich([], {}))
        assert "ceremonies add" in out

    def test_the_listing_answers_all_three_questions(self):
        # What is declared, when it next happens, and what it did last time —
        # the third being the one nothing else in yeaboi answers.
        ceremony = _ceremony()
        last = {"morning-standup": CeremonyRun(outcome="ok", fired_at="2026-08-17T09:00:00+00:00")}
        out = _text(render.format_ceremonies_rich([ceremony], last))
        assert "morning-standup" in out
        assert "Mon–Fri at 09:00" in out
        assert "ok" in out

    def test_history_shows_the_reason_a_run_did_not_happen(self):
        runs = [
            CeremonyRun(
                ceremony="morning-standup",
                outcome="skipped_stale",
                fired_at="2026-08-17T14:00:00+00:00",
                detail="fired 300 min after its 09:00 slot",
            )
        ]
        out = _text(render.format_history_rich(runs))
        assert "skipped_stale" in out
        assert "300 min" in out

    def test_an_empty_history_says_nothing_has_fired(self):
        assert _text(render.format_history_rich([])).strip()

    def test_the_modes_table_lists_the_refusals_with_their_reasons(self):
        # "Why can't I schedule a retro" is answered on the same screen that
        # answers "what can I schedule".
        out = _text(render.format_modes_rich())
        assert "standup" in out
        assert "retro" in out
        assert "live board" in out
