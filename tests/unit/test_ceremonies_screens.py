"""Render tests for the Ceremonies page (_screens_ceremonies.py).

Rendered at the app's enforced minimum terminal size (84x40), never the
builder's own ``height=24`` default — that is a size the app never draws, and
asserting against it hides exactly the crowding a real user would hit.

What the tests pin is the page's reason for existing: the outcome of the last
run is on the row, and the drift between the store and the operating system is
on the page. Both are invisible everywhere else until a morning goes quiet.
"""

from __future__ import annotations

import io

from rich.console import Console

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ui.mode_select.screens._screens_ceremonies import _build_ceremonies_screen

# The enforced minimum from _screens.py (_MIN_WIDTH / _MIN_HEIGHT).
_W, _H = 84, 40


def _render(**kwargs) -> str:
    panel = _build_ceremonies_screen(width=_W, height=_H, **kwargs)
    console = Console(file=io.StringIO(), width=_W, height=_H)
    console.print(panel)
    return console.file.getvalue()


def _ceremony(**overrides) -> Ceremony:
    base = {
        "session_id": "s1",
        "name": "morning-standup",
        "mode": "standup",
        "at": "09:00",
        "weekdays": "1-5",
        "channels": ("slack",),
    }
    return Ceremony(**{**base, **overrides})


class TestEmptyState:
    def test_explains_what_a_ceremony_is_rather_than_saying_none(self):
        out = _render(ceremonies=[])
        assert "Nothing is scheduled yet" in out
        assert "while closed" in out

    def test_still_draws_its_buttons(self):
        assert "Back" in _render(ceremonies=[])


class TestTheRow:
    def test_shows_the_cadence_the_channels_and_the_mode(self):
        out = _render(ceremonies=[_ceremony()])
        assert "morning-standup" in out
        assert "Mon–Fri at 09:00" in out
        assert "slack" in out

    def test_a_paused_ceremony_says_paused_where_its_cadence_would_be(self):
        # Not trailing the name: that column ellipsizes at the minimum width,
        # and a paused ceremony whose state got cropped is the worst cell here.
        out = _render(ceremonies=[_ceremony(enabled=False)])
        assert "paused" in out
        assert "Mon–Fri at 09:00" not in out

    def test_a_run_that_never_happened_says_never(self):
        assert "never run" in _render(ceremonies=[_ceremony()])

    def test_a_successful_run_shows_when(self):
        out = _render(
            ceremonies=[_ceremony()],
            last_runs={"morning-standup": CeremonyRun(outcome="ok", fired_at="2026-08-17T09:00:12")},
        )
        assert "08-17 09:00" in out

    def test_a_skipped_run_names_the_reason_on_the_row(self):
        # The whole point of the page: "it didn't post" needs a why, and the why
        # must not be behind a keypress.
        out = _render(
            ceremonies=[_ceremony()],
            last_runs={"morning-standup": CeremonyRun(outcome="skipped_stale", fired_at="2026-08-17T14:00:00")},
        )
        assert "stale" in out

    def test_a_failed_run_is_marked(self):
        out = _render(
            ceremonies=[_ceremony()],
            last_runs={"morning-standup": CeremonyRun(outcome="failed", fired_at="2026-08-17T09:00:00")},
        )
        assert "failed" in out


class TestSpend:
    def test_no_cap_and_no_spend_shows_nothing_to_worry_about(self):
        assert "$" not in _render(ceremonies=[_ceremony()]).split("This month")[1]

    def test_a_cap_is_shown_against_the_months_spend(self):
        out = _render(ceremonies=[_ceremony(monthly_cap_usd=5.0)], spend={"morning-standup": 4.3})
        assert "$4.30 / $5" in out


class TestDrift:
    def test_a_declaration_with_no_job_is_reported(self):
        # It will simply never fire, and nothing else in the app would say so.
        out = _render(ceremonies=[_ceremony()], drift=["'morning-standup' is declared but has no scheduled job"])
        assert "has no scheduled job" in out

    def test_no_drift_means_no_warning_line(self):
        assert "!" not in _render(ceremonies=[_ceremony()], drift=[])


class TestChrome:
    def test_the_selected_row_is_marked(self):
        out = _render(ceremonies=[_ceremony(), _ceremony(name="weekly-report", mode="report")], selected=1)
        marker_line = next(line for line in out.splitlines() if "weekly-report" in line)
        assert "▸" in marker_line

    def test_a_message_is_shown(self):
        assert "ran ($0.12)" in _render(ceremonies=[_ceremony()], message="morning-standup ran ($0.12) → slack")

    def test_the_buttons_survive_a_crowded_page(self):
        # A fixed-height Panel crops from the BOTTOM, so the buttons are the
        # first thing a long list would push off screen.
        many = [_ceremony(name=f"ceremony-{n:02d}") for n in range(40)]
        out = _render(ceremonies=many, drift=[f"drift {n}" for n in range(10)])
        assert "Run now" in out
        assert "Back" in out

    def test_geometry_is_published_for_the_scroll_helpers(self):
        meta: dict = {}
        _render(ceremonies=[_ceremony(name=f"c-{n:02d}") for n in range(40)], scroll_meta=meta)
        assert meta.get("max_offset", 0) > 0
        assert meta.get("viewport_h", 0) > 0

    def test_the_actions_can_be_relabelled_for_a_paused_row(self):
        # A button reading "Pause" on something already paused is a bug report.
        out = _render(ceremonies=[_ceremony(enabled=False)], actions=["Run now", "Resume", "Back"])
        assert "Resume" in out
