"""Tests for scripts/cowork_setup.py and the cowork data it reads.

The script's job is to turn ``cowork/`` into GitHub labels, repository variables
and a routine manifest without anyone retyping a cron expression. That only works
while three files agree: ``README.md``'s registered-routines table, each routine
file's own ``**Trigger**``/``**Model**`` lines, and the tier table in
``models.md``.

Nothing at run time notices when they stop agreeing. A routine keeps firing on
whatever cron was typed into the web form, on whatever the account-side dropdown
says, and reports nothing about either — the drift only surfaces on a bill or in
a sweep that ran on a Tuesday it was never meant to. So it is caught statically
here, the same way ``test_cowork_models.py`` catches a pasted model id.

No test in this file calls ``gh`` or touches the network: every parser takes text,
``--check --local`` skips the remote half by design, and the two GitHub
transports are reached only through their seams — ``_gh`` for the CLI half and
``_api`` for the REST half. Anything asserting on the REST transport must also
clear ``GH_TOKEN``/``GITHUB_TOKEN`` from the environment (the ``no_token``
fixture does), or a developer who exports one turns a unit test into a live call
against their own repository.
"""

from __future__ import annotations

import calendar
import dataclasses
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "cowork_setup.py"
_spec = importlib.util.spec_from_file_location("cowork_setup", _MODULE_PATH)
setup = importlib.util.module_from_spec(_spec)
# Registered before exec: @dataclass resolves annotations through
# sys.modules[cls.__module__], which is None for a module loaded off a path.
sys.modules["cowork_setup"] = setup
_spec.loader.exec_module(setup)

ROUTINES = setup.parse_routines()
TIERS = setup.parse_tiers()
WORKSTREAMS = setup.parse_workstreams()
CRON_ROUTINES = [r for r in ROUTINES if r.kind == "cron"]


def _routine_ids(routine) -> str:
    return routine.path


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch):
    """Nothing in this file may reach GitHub, by either transport.

    ``_gh`` used to be the only seam, and faking it was enough. There are two
    now, and the second authenticates from the *environment*: on a machine with
    no `gh` installed and ``GH_TOKEN`` exported, `apply_teardown`'s tests would
    resolve the developer's own repository from `origin` and issue real DELETEs
    against their labels. Nothing in the test body would look wrong.

    So: both token variables cleared for every test, ``_api`` refusing by
    default so any new test that falls through the seam fails loudly rather than
    silently going live, and the per-run module state the transports memoise
    reset — ``_SLUG`` in particular, which otherwise carries one test's fake
    remote into the next.
    """
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(setup, "TRANSPORT", "gh")
    setup.transport.reset_slug_cache()

    def refuse(method, path, body=None):
        raise AssertionError(f"a test reached the REST transport unstubbed: {method} {path}")

    monkeypatch.setattr(setup.transport, "api", refuse)


class TestRoutinesResolve:
    """Every row of the README table resolves to a real, complete routine."""

    def test_the_table_and_the_directory_hold_the_same_routines(self):
        listed = {r.path for r in ROUTINES}
        on_disk = {str(p.relative_to(setup.ROUTINES_DIR)) for p in setup.ROUTINES_DIR.rglob("*.md")}
        assert listed == on_disk, (
            "cowork/README.md's registered-routines table and cowork/routines/ disagree. "
            f"only in the table: {sorted(listed - on_disk)}; only on disk: {sorted(on_disk - listed)}"
        )

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_every_routine_points_at_a_file_that_exists(self, routine):
        assert (setup.ROUTINES_DIR / routine.path).exists()

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_every_routine_names_a_tier_the_table_defines(self, routine):
        assert routine.tier in TIERS, (
            f"{routine.path} is tiered `{routine.tier}`, which cowork/models.md does not define"
        )

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_every_routine_resolves_to_a_model_id(self, routine):
        # `inherit` has no id by design, but no routine may use it: a routine has
        # no caller to inherit from, so it would resolve to nothing at all.
        assert routine.model_id, f"{routine.path} is tiered `{routine.tier}`, which names no model id"

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_every_routine_has_a_trigger_line(self, routine):
        assert routine.trigger, f"{routine.path} has no `**Trigger** — …` line for the manifest to read"


class TestFilesAgreeWithTheTable:
    """The routine file and the README row are two copies of the same facts."""

    @pytest.mark.parametrize("routine", CRON_ROUTINES, ids=_routine_ids)
    def test_the_cron_matches_the_table(self, routine):
        rows = {f"{kind}/{stem}.md": trigger for kind, stem, trigger, _, _ in setup._routine_rows()}
        assert setup.readme_cron(rows[routine.path]) == routine.cron, (
            f"{routine.path} runs on `{routine.cron}` but README.md's table says something else"
        )

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_a_declared_model_line_matches_the_table(self, routine):
        """The non-sweeps carry their own ``**Model**`` line; it must agree.

        The sweeps deliberately have none — they take their tier from
        ``sweep-procedure.md`` — so this asserts agreement where a line exists
        rather than requiring one.
        """
        body = (setup.ROUTINES_DIR / routine.path).read_text(encoding="utf-8")
        declared = setup._MODEL_LINE.search(body)
        if declared is None:
            return
        assert declared.group(1) == routine.tier, (
            f"{routine.path} declares tier `{declared.group(1)}` but README.md's table says `{routine.tier}`"
        )

    def test_only_the_non_sweeps_declare_a_model(self):
        declaring = [
            r.path
            for r in ROUTINES
            if setup._MODEL_LINE.search((setup.ROUTINES_DIR / r.path).read_text(encoding="utf-8"))
        ]
        assert sorted(declaring) == sorted(
            [
                "cron/day-ahead.md",
                "cron/digest.md",
                "cron/cd-deploy.md",
                "cron/go-migration-campaign.md",
                "cron/go-migration-progress.md",
                "cron/integrations-campaign.md",
                "cron/release-promote-ask.md",
                "cron/retune.md",
                "cron/shipped-standup.md",
                "cron/slack-relay.md",
                "events/go-migration-wave-merged.md",
                "events/pr-merged-close-loop.md",
                "events/pr-opened-dod-audit.md",
                "events/release-published-announce.md",
            ]
        ), (
            "a sweep has grown its own **Model** line, or a non-sweep has lost one. "
            "Sweeps take their tier from sweep-procedure.md; anything doing its own "
            "model-worthy work needs a row in models.md and a line of its own."
        )


class TestCronExpressions:
    @pytest.mark.parametrize("routine", CRON_ROUTINES, ids=_routine_ids)
    def test_five_fields(self, routine):
        assert len(routine.cron.split()) == 5, f"{routine.path} cron `{routine.cron}` is not a 5-field expression"

    @pytest.mark.parametrize("routine", CRON_ROUTINES, ids=_routine_ids)
    def test_never_restricts_both_day_fields(self, routine):
        """The trap documented under "Cron trap" in cowork/README.md.

        Standard cron ORs day-of-month with day-of-week when both are restricted,
        so a fortnightly routine written as `30 7 1-7 * 2` fires every day 1–7
        *and* every Tuesday. It runs near-daily and says nothing.
        """
        assert not setup.restricts_both_day_fields(routine.cron), (
            f"{routine.path} cron `{routine.cron}` restricts day-of-month AND day-of-week — "
            "cron ORs them, so this fires far more often than the cadence claims"
        )

    def test_every_routine_clears_the_one_hour_minimum(self):
        """RemoteTrigger rejects anything more frequent than hourly."""
        for routine in CRON_ROUTINES:
            minute = routine.cron.split()[0]
            assert "/" not in minute and "," not in minute, (
                f"{routine.path} cron `{routine.cron}` fires more than once an hour, which the routines API rejects"
            )


def _fake_routine(cron: str, name: str = "fake-sweep", summary: str = "a fake routine") -> object:
    """A ``Routine`` with only the fields the agenda reads, for the boundary cases.

    The real fleet cannot exercise every branch — nothing in it fires four times a
    day and nothing crosses midnight in Europe/London — and inventing a cron here
    is cheaper and clearer than inventing a routine file to hold one.
    """
    return setup.Routine(
        name=name,
        path=f"cron/{name}.md",
        kind="cron",
        tier="fast",
        model_id="x",
        workstream=None,
        cron=cron,
        trigger=f"cron `{cron}` (a fake cadence)",
        filters=None,
        summary=summary,
        prompt="p",
        trigger_name=f"cowork: {name}",
    )


class TestCronFields:
    """The field parser. Everything the agenda knows about *when* starts here."""

    def test_a_star_covers_the_whole_range(self):
        assert setup._cron_field("*", 0, 59) == frozenset(range(60))

    def test_a_single_value(self):
        assert setup._cron_field("45", 0, 59) == frozenset({45})

    def test_a_list(self):
        assert setup._cron_field("1,4", 0, 7) == frozenset({1, 4})

    def test_a_range(self):
        assert setup._cron_field("7-23", 0, 23) == frozenset(range(7, 24))

    def test_a_step_over_a_star(self):
        assert setup._cron_field("*/15", 0, 59) == frozenset({0, 15, 30, 45})

    def test_a_step_over_a_range(self):
        assert setup._cron_field("1-7/2", 1, 31) == frozenset({1, 3, 5, 7})

    def test_a_list_of_ranges(self):
        assert setup._cron_field("1-3,20-21", 1, 31) == frozenset({1, 2, 3, 20, 21})

    def test_an_out_of_range_value_raises(self):
        """Not "matches nothing" — a field matching nothing is a routine that
        never fires, and the agenda would report its absence as a quiet day."""
        with pytest.raises(ValueError, match="outside"):
            setup._cron_field("60", 0, 59)

    def test_a_name_alias_raises_and_says_so(self):
        with pytest.raises(ValueError, match="name aliases"):
            setup._cron_field("MON", 0, 7)

    def test_a_backwards_range_raises(self):
        with pytest.raises(ValueError, match="backwards"):
            setup._cron_field("20-3", 1, 31)

    def test_a_bare_value_with_a_step_raises(self):
        """Vixie cron reads `1/2` as `1-31/2`. Modelling it as {1} would be the one
        form that disagrees with the scheduler quietly instead of loudly."""
        with pytest.raises(ValueError, match="explicit range"):
            setup._cron_field("1/2", 1, 31)

    def test_a_zero_step_raises(self):
        with pytest.raises(ValueError, match="positive step"):
            setup._cron_field("*/0", 0, 59)


class TestCronTimes:
    """Whether a cron fires on a given day, against the cadences README claims.

    The table says "Mon + Thu" and "3rd and 17th" in prose, and nothing has ever
    checked that the expression beside it agrees. These do.
    """

    def _days(self, cron: str, year: int, month: int) -> list[int]:
        last = calendar.monthrange(year, month)[1]
        return [day for day in range(1, last + 1) if setup.cron_times(cron, date(year, month, day))]

    def test_a_short_expression_raises(self):
        with pytest.raises(ValueError, match="5-field"):
            setup.cron_times("0 6 * *", date(2026, 8, 13))

    def test_the_twice_weekly_security_cron_is_mondays_and_thursdays(self):
        for day in range(1, 32):
            when = date(2026, 8, 1) + timedelta(days=day - 1)
            if when.month != 8:
                break
            fires = bool(setup.cron_times("0 6 * * 1,4", when))
            assert fires == (when.weekday() in {0, 3}), when

    def test_a_weekly_cron_fires_once_a_week(self):
        assert self._days("0 7 * * 1", 2026, 8) == [3, 10, 17, 24, 31]

    @pytest.mark.parametrize("month", range(1, 13))
    def test_a_fortnightly_cron_fires_exactly_twice_every_month(self, month: int):
        """The claim the README makes in a section heading and nowhere else."""
        assert self._days("30 7 3,17 * *", 2026, month) == [3, 17]

    def test_a_monthly_cron_fires_twelve_times_a_year(self):
        hits = [month for month in range(1, 13) if setup.cron_times("30 7 12 * *", date(2026, month, 12))]
        assert hits == list(range(1, 13))

    def test_the_hourly_relay_fires_seventeen_times(self):
        times = setup.cron_times("0 7-23 * * *", date(2026, 8, 13))
        assert len(times) == 17
        assert times[0] == time(7, 0) and times[-1] == time(23, 0)

    def test_a_daily_cron_fires_once_on_every_day_of_a_month(self):
        assert self._days("15 8 * * *", 2026, 2) == list(range(1, 29))

    def test_day_of_week_seven_is_sunday(self):
        """Every cron implementation accepts both 0 and 7; so does this one."""
        sunday = date(2026, 8, 16)
        assert sunday.weekday() == 6
        assert setup.cron_times("0 6 * * 7", sunday)
        assert setup.cron_times("0 6 * * 0", sunday)

    def test_a_month_field_restricts_to_that_month(self):
        assert setup.cron_times("0 7 * 2 *", date(2026, 2, 3))
        assert not setup.cron_times("0 7 * 2 *", date(2026, 3, 3))

    def test_both_day_fields_are_ored_not_anded(self):
        """The trap `restricts_both_day_fields()` exists to stop.

        Modelled rather than corrected: the agenda has to say what the scheduler
        will do, or it would disagree with the fleet about exactly the case the
        doctor is there to catch. `0 7 1 * 2` is the 1st *and* every Tuesday.
        """
        assert setup.cron_times("0 7 1 * 2", date(2026, 8, 1))  # a Saturday, by day-of-month
        assert setup.cron_times("0 7 1 * 2", date(2026, 8, 4))  # a Tuesday, by day-of-week
        assert not setup.cron_times("0 7 1 * 2", date(2026, 8, 5))
        assert setup.restricts_both_day_fields("0 7 1 * 2"), "the doctor must still refuse it"

    @pytest.mark.parametrize("routine", CRON_ROUTINES, ids=_routine_ids)
    def test_every_registered_cron_fires_inside_six_weeks(self, routine):
        """A typo like `30 7 31 2 *` parses, validates, and never fires.

        Nothing else would notice: the routine sits registered and silent, and the
        digest's 21-day health line reports it as a workstream that found nothing.
        """
        start = date(2026, 8, 1)
        assert any(setup.cron_times(routine.cron, start + timedelta(days=offset)) for offset in range(42)), (
            f"{routine.path} cron `{routine.cron}` fires on no day in six weeks"
        )


class TestAgenda:
    """The payload and the rendered schedule `make cowork-agenda` prints."""

    ZONE = setup.display_zone()[0]

    def test_three_firings_is_a_timed_run_and_four_is_background(self):
        """A threshold, not a name check — the next hourly routine needs no edit."""
        three = [_fake_routine("0 6,7,8 * * *")]
        four = [_fake_routine("0 6,7,8,9 * * *")]
        assert len(setup.day_plan(three, date(2026, 8, 13), self.ZONE)[0]) == 1
        assert setup.day_plan(three, date(2026, 8, 13), self.ZONE)[1] == []
        assert setup.day_plan(four, date(2026, 8, 13), self.ZONE)[0] == []
        assert len(setup.day_plan(four, date(2026, 8, 13), self.ZONE)[1]) == 1

    def test_timed_are_ordered_by_the_time_they_fire(self):
        routines = [_fake_routine("0 9 * * *", "late"), _fake_routine("0 6 * * *", "early")]
        timed, _ = setup.day_plan(routines, date(2026, 8, 13), self.ZONE)
        assert [entry["name"] for entry in timed] == ["early", "late"]

    def test_a_routine_that_does_not_fire_is_absent(self):
        assert setup.day_plan([_fake_routine("0 6 * * 1")], date(2026, 8, 13), self.ZONE) == ([], [])

    @pytest.mark.skipif(setup.display_zone()[0] is None, reason="no tz database")
    def test_the_same_cron_renders_bst_in_summer_and_gmt_in_winter(self):
        """DST is derived per date. An offset baked in once is wrong half the year."""
        routines = [_fake_routine("30 7 * * *")]
        summer, _ = setup.day_plan(routines, date(2026, 8, 13), self.ZONE)
        winter, _ = setup.day_plan(routines, date(2026, 1, 13), self.ZONE)
        assert summer[0]["times_local"] == ["08:30"]
        assert winter[0]["times_local"] == ["07:30"]
        assert summer[0]["times_utc"] == winter[0]["times_utc"] == ["07:30"]

    def test_a_local_time_on_another_date_is_marked(self, monkeypatch):
        """DISPLAY_TZ is a one-line change somebody will make. At 06:00 UTC
        London is the same day and Los Angeles is the day before, and a bare
        23:00 next to "Today" would be a whole day wrong."""
        monkeypatch.setattr(setup, "DISPLAY_TZ", "America/Los_Angeles")
        zone, note = setup.display_zone()
        if zone is None:
            pytest.skip(note)
        timed, _ = setup.day_plan([_fake_routine("0 6 * * *")], date(2026, 8, 13), zone)
        assert timed[0]["times_local"] == ["23:00 (-1d)"]

    @pytest.mark.skipif(setup.display_zone()[0] is None, reason="no tz database")
    def test_a_window_ending_at_midnight_reads_as_2400(self):
        """`00:00 (+1d)` at the end of a span is true and reads as a bug."""
        _, background = setup.day_plan([_fake_routine("0 7-23 * * *")], date(2026, 8, 13), self.ZONE)
        assert background[0]["window_local"] == "08:00-24:00"
        assert background[0]["window_utc"] == "07:00-23:00"

    def test_a_missing_tz_database_degrades_to_utc_rather_than_raising(self, monkeypatch):
        """Losing a bracket is acceptable; losing the morning post is not."""
        monkeypatch.setattr(setup, "DISPLAY_TZ", "Mars/Olympus_Mons")
        zone, note = setup.display_zone()
        assert zone is None
        assert note and "UTC" in note
        timed, _ = setup.day_plan([_fake_routine("0 6 * * *")], date(2026, 8, 13), zone)
        assert timed[0]["times_local"] == ["06:00"]

    def test_the_tail_covers_the_horizon_and_excludes_today(self):
        payload = setup.agenda(date(2026, 8, 13), horizon=7)
        assert len(payload["ahead"]) == 7
        assert payload["date"] not in {entry["date"] for entry in payload["ahead"]}
        assert payload["ahead"][0]["date"] == "2026-08-14"

    def test_a_name_on_every_day_of_the_tail_is_lifted_out_of_it(self):
        """Seven lines that all say `digest` are the daily routines restated, not news."""
        payload = setup.agenda(date(2026, 8, 13))
        assert "digest" in payload["daily"]
        for entry in payload["ahead"]:
            assert "digest" not in entry["names"]

    def test_a_quiet_sunday_still_says_something(self):
        """The reason this routine posts every day. Silence from a schedule is
        ambiguous — nothing scheduled, or the routine broke?"""
        payload = setup.agenda(date(2026, 8, 16))
        names = {entry["name"] for entry in payload["today"]}
        assert not {name for name in names if name.endswith("-sweep")}
        assert "digest" in names
        assert any("Next" in line for line in payload["lines"])

    def test_the_fortnightly_sweeps_reach_the_tail_before_they_run(self):
        """The whole point of a horizon: `30 7 11,25 * *` is unreadable, and a
        fortnightly sweep arriving unannounced is what prompted this."""
        payload = setup.agenda(date(2026, 8, 6))
        upcoming = {name for entry in payload["ahead"] for name in entry["names"]}
        assert {"performance-sweep", "artifacts-sharing-sweep", "roadmap-sweep"} <= upcoming

    def test_the_evening_post_is_announced_like_everything_else(self):
        """`shipped-standup` used to be filtered out of the rendered message. It is
        one of the two things a reader waits for in Slack, so a schedule that
        answers "when should I expect something" has to name it. Being daily, it
        is named once in the closing line rather than seven times down the tail."""
        payload = setup.agenda(date(2026, 8, 16))
        assert "shipped-standup" in {entry["name"] for entry in payload["today"]}
        assert "shipped-standup" in payload["daily"]
        blob = "\n".join(payload["lines"])
        assert "shipped-standup" in blob
        assert not any("shipped-standup" in entry["names"] for entry in payload["ahead"]), (
            "a daily routine restated on every day of the tail is noise"
        )

    def test_the_payload_is_json_serialisable(self):
        assert json.loads(json.dumps(setup.agenda(date(2026, 8, 13))))

    def test_the_lines_meet_the_scribes_format_contract(self):
        """`.claude/agents/cowork-scribe.md`: standard Markdown, fixed section
        anchors, no bare URLs."""
        lines = setup.agenda(date(2026, 8, 13))["lines"]
        blob = "\n".join(lines)
        assert "http" not in blob, "the schedule links to nothing; a bare URL would be the scribe's rule broken"
        assert lines[0].startswith(f"{setup.SECTION_EMOJI['today']} **Today** —"), lines[0]

    def test_no_line_carries_slack_mrkdwn_emphasis(self):
        """The bug this format replaced, written down as a check.

        The connector reads standard Markdown, where `*x*` is *italic* and bold
        is `**x**`. Slack's own mrkdwn has it the other way, so a heading in the
        wrong dialect does not fail — it renders the wrong weight, in a message
        nobody diffs. `.claude/agents/cowork-scribe.md` had recorded that for
        weeks and the digest had been fixed; this renderer had not, because the
        only test looking at it asserted `*Today*` as though that were correct.
        """
        for line in setup.agenda(date(2026, 8, 13))["lines"]:
            assert not re.search(r"(?<!\*)\*(?!\*)", line), line

    def test_the_only_emoji_are_the_section_anchors_at_a_line_start(self):
        """An allowlist rather than a codepoint range.

        The range this replaced — `\U0001f300-\U0001faff`, `✀-➿`, `☀-⛿` — has a
        hole over U+2300-U+23FF, so ⏱ and ⏳ would have walked through the guard
        that existed to stop exactly them.
        """
        anchors = set(setup.SECTION_EMOJI.values())
        for line in setup.agenda(date(2026, 8, 13))["lines"]:
            symbols = [char for char in line if unicodedata.category(char) == "So"]
            assert set(symbols) <= anchors, line
            if symbols:
                assert symbols == [line[0]], f"an anchor belongs at the start of its heading, once: {line}"

    def test_the_approval_verbs_are_never_spent_decoratively(self):
        """`cron/slack-relay.md` maps a ✅/❌ reaction onto an issue, so a reader
        who meets either in a heading has to work out whether it meant something."""
        blob = "\n".join(setup.agenda(date(2026, 8, 13))["lines"])
        assert "✅" not in blob and "❌" not in blob

    def test_the_sections_are_separated_by_blank_lines(self):
        """One undifferentiated paragraph of thirteen lines is what this replaced."""
        lines = setup.agenda(date(2026, 8, 13))["lines"]
        ahead = next(i for i, line in enumerate(lines) if line.startswith(setup.SECTION_EMOJI["ahead"]))
        assert lines[1] == "", "today's heading stands apart from the times under it"
        assert lines[ahead - 1] == "", "the tail does not run on from today's block"
        assert lines[ahead + 1] == "", "the tail's heading stands apart from its days"

    def test_every_listed_routine_reaches_the_rendered_lines(self):
        """A payload entry the renderer drops is a routine that runs unannounced."""
        payload = setup.agenda(date(2026, 8, 13))
        blob = "\n".join(payload["lines"])
        for entry in payload["today"] + payload["background"]:
            assert entry["name"] in blob, entry["name"]

    @pytest.mark.skipif(setup.display_zone()[0] is None, reason="no tz database")
    def test_the_utc_bracket_appears_only_when_it_says_something(self):
        """Half the year London *is* UTC, so the bracket would repeat the time it
        sits beside on every line. The heading carries the zone instead, which is
        what keeps a bare time unambiguous once the bracket is gone."""
        winter = setup.agenda(date(2026, 1, 12))["lines"]
        summer = setup.agenda(date(2026, 8, 13))["lines"]
        assert "Europe/London" in winter[0] and "Europe/London" in summer[0]
        assert not any("UTC)" in line for line in winter), "GMT == UTC; the bracket is noise"
        assert any("UTC)" in line for line in summer), "BST != UTC; the bracket is the anchor"

    def test_the_tail_names_the_month_again_when_it_changes(self):
        lines = setup.agenda(date(2026, 8, 27))["lines"]
        assert any(line.startswith("**Tue 1 Sep** —") for line in lines), lines

    def test_a_quiet_day_folds_into_the_closing_sentence(self):
        """A day with nothing on it, written as its own line, reads as a routine
        called "nothing" — and spends a line of the post saying so."""
        payload = setup.agenda(date(2026, 8, 16))
        quiet = [entry for entry in payload["ahead"] if not entry["names"]]
        assert quiet, "16 Aug 2026 is chosen for having a quiet day in its tail"
        for entry in quiet:
            future = date.fromisoformat(entry["date"])
            stamp = f"{entry['weekday']} {future.day}"
            assert not any(line.startswith(f"**{stamp}**") for line in payload["lines"]), stamp
            assert any(stamp in line and "clear" in line for line in payload["lines"]), stamp

    def test_a_quiet_day_does_not_spend_the_month_marker(self):
        """The marker belongs to the day that shows it, not to the day that has it.

        1 Aug 2026 is a Saturday and Saturday is the fleet's one quiet weekday, so
        the day that changes month is the one folded away — and advancing on every
        day walked rather than on every day rendered leaves `**Fri 31**` followed
        by a bare `**Sun 2**`, with August announced only in an aside below it.
        That is the ambiguity the marker exists to prevent, and it recurs every
        month whose 1st falls on a quiet day.

        The fixture was a Sunday until `cron/retune.md` started firing at 08:00 on
        Sundays. Which day is quiet is a property of the schedule, and the
        schedule is what everything else in this class is testing.
        """
        lines = setup.agenda(date(2026, 7, 27))["lines"]
        assert any(line.startswith("**Sun 2 Aug** —") for line in lines), lines
        assert not any(line.startswith("**Sat 1") for line in lines), lines
        # Matched in two parts, like the sibling test above: how many quiet days
        # the closing sentence lists depends on the cadence. The fact under test
        # is that the quiet day's month is named there and nowhere else, which is
        # exactly these two substrings.
        assert any("Sat 1 Aug" in line and "clear" in line for line in lines), lines

    def test_a_collapsed_quiet_day_does_not_take_a_rendered_month_with_it(self):
        """The other half: Sat 31 Oct is folded away and Sun 1 Nov still says Nov."""
        lines = setup.agenda(date(2026, 10, 26))["lines"]
        assert any(line.startswith("**Sun 1 Nov** —") for line in lines), lines
        assert not any(line.startswith("**Sat 31**") for line in lines), lines

    def test_every_anchor_is_a_single_codepoint(self):
        """No variation sequences. A trailing U+FE0F that one client needs and
        another drops is a heading that renders two ways, and U+FE0F is category
        `Mn` — the allowlist test above would not see it."""
        for section, emoji in setup.SECTION_EMOJI.items():
            assert len(emoji) == 1, f"{section}: {emoji!r}"


def _message_payload(**overrides) -> dict:
    """An empty agenda payload, for rendering one section at a time.

    Built by hand rather than taken from ``agenda()`` because several of the
    renderer's branches do not occur in any one week of the real schedule — the
    fleet's one quiet weekday is Saturday and it is never quiet for seven days
    running, and it has exactly one background routine — so they would go unexercised until the day
    they were wrong.
    """
    base = {
        "date": "2026-08-13",
        "weekday": "Thu",
        "display_timezone": "Europe/London",
        "note": None,
        "today": [],
        "background": [],
        "events": [],
        "daily": [],
        "ahead": [],
    }
    return base | overrides


def _tail_payload(ahead: list[tuple[str, list[str]]], daily: list[str], day: str = "2026-08-13") -> dict:
    """``_message_payload`` with just the seven-day tail filled in."""
    return _message_payload(
        date=day,
        daily=daily,
        ahead=[{"date": iso, "weekday": f"{date.fromisoformat(iso):%a}", "names": names} for iso, names in ahead],
    )


def _background(name: str, firings: int, window: str) -> dict:
    """One ``day_plan`` background entry, with local and UTC windows agreeing."""
    return {
        "name": name,
        "workstream": None,
        "summary": "",
        "firings": firings,
        "window_utc": window,
        "window_local": window,
    }


class TestStandingSections:
    """Background and event routines — true all day rather than at a time."""

    def test_one_background_routine_stays_on_the_heading_line(self):
        lines = setup.agenda_lines(_message_payload(background=[_background("slack-relay", 17, "07:00-23:00")]))
        assert f"{setup.SECTION_EMOJI['background']} **Background** — slack-relay, 17 runs `07:00-23:00`" in lines

    def test_a_second_background_routine_does_not_get_a_second_anchor(self):
        """An emoji repeating down a column has stopped being a heading.

        `BACKGROUND_AFTER` is a threshold rather than a name check so that the
        next hourly routine needs no edit here — which means the plural case is
        reachable without one, and has to already be right.
        """
        lines = setup.agenda_lines(
            _message_payload(
                background=[_background("slack-relay", 17, "07:00-23:00"), _background("watcher", 5, "09:00-13:00")]
            )
        )
        anchored = [line for line in lines if line.startswith(setup.SECTION_EMOJI["background"])]
        assert anchored == [f"{setup.SECTION_EMOJI['background']} **Background**"]
        assert "**slack-relay** — 17 runs `07:00-23:00`" in lines
        assert "**watcher** — 5 runs `09:00-13:00`" in lines

    def test_the_degraded_timezone_note_sits_with_the_heading_and_unemphasised(self):
        """It qualifies the heading above it, not the first entry below it — and it
        goes out verbatim, because a zone name carries underscores and `_…_` around
        one leaks a stray delimiter mid-line."""
        note = "times in UTC — no tz database for America/Los_Angeles (`uv add --dev tzdata` fixes it)"
        lines = setup.agenda_lines(_message_payload(note=note, display_timezone=None))
        assert lines[1] == note
        assert lines[2] == ""

    def test_the_month_marker_is_not_spent_on_a_collapsed_day(self):
        """The synthetic twin of the live-schedule test, immune to a cadence change."""
        lines = setup.agenda_lines(
            _tail_payload(
                [("2026-10-31", ["marketing-weekly"]), ("2026-11-01", []), ("2026-11-02", ["security-sweep"])],
                [],
                day="2026-10-30",
            )
        )
        assert "**Mon 2 Nov** — security-sweep" in lines
        assert "_Sun 1 Nov is clear._" in lines


class TestClosingSentence:
    """The one line carrying both of the tail's asides — quiet days and dailies."""

    BUSY = ("2026-08-14", ["platform-sweep"])
    QUIET = ("2026-08-15", [])
    ALSO_QUIET = ("2026-08-16", [])
    THIRD_QUIET = ("2026-08-17", [])

    def _closing(self, ahead, daily) -> str | None:
        lines = setup.agenda_lines(_tail_payload(ahead, daily))
        return lines[-1] if lines[-1].startswith("_") else None

    def test_it_carries_both_clauses_when_there_are_both(self):
        assert self._closing([self.BUSY, self.QUIET], ["digest"]) == "_Sat 15 is clear. Every day: digest._"

    def test_a_week_with_no_quiet_day_says_nothing_about_one(self):
        assert self._closing([self.BUSY], ["digest"]) == "_Every day: digest._"

    def test_no_daily_routine_drops_that_clause(self):
        assert self._closing([self.BUSY, self.QUIET], []) == "_Sat 15 is clear._"

    def test_neither_means_no_closing_line_at_all(self):
        """And no stray blank line left behind where it would have been."""
        lines = setup.agenda_lines(_tail_payload([self.BUSY], []))
        assert self._closing([self.BUSY], []) is None
        assert lines[-1] != "", lines

    def test_quiet_days_are_counted_before_they_are_conjugated(self):
        """`Sat 15 and Sun 16 is clear` is the kind of thing nobody reports."""
        two = self._closing([self.BUSY, self.QUIET, self.ALSO_QUIET], [])
        assert two == "_Sat 15 and Sun 16 are clear._"

    def test_three_or_more_quiet_days_take_a_comma_and_a_final_and(self):
        three = self._closing([self.BUSY, self.QUIET, self.ALSO_QUIET, self.THIRD_QUIET], [])
        assert three == "_Sat 15, Sun 16 and Mon 17 are clear._"

    def test_an_entirely_quiet_week_still_renders_its_heading(self):
        """A tail with no rendered day must not leave the heading over a blank."""
        lines = setup.agenda_lines(_tail_payload([self.QUIET, self.ALSO_QUIET], ["digest"]))
        heading = lines.index(f"{setup.SECTION_EMOJI['ahead']} **Next 2 days**")
        assert lines[heading + 1] == ""
        assert lines[heading + 2] == "_Sat 15 and Sun 16 are clear. Every day: digest._"
        assert lines[-1] == lines[heading + 2], "nothing trails the closing sentence"


class TestAgendaCli:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_MODULE_PATH), "--agenda", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_it_prints_json_by_default(self):
        result = self._run("--date", "2026-08-13")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["lines"]

    def test_text_prints_the_message_the_routine_posts(self):
        result = self._run("--text", "--date", "2026-08-13")
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith(f"{setup.SECTION_EMOJI['today']} **Today** —")

    def test_a_bad_date_is_refused_rather_than_guessed(self):
        result = self._run("--date", "next tuesday")
        assert result.returncode == 2
        assert "YYYY-MM-DD" in result.stdout + result.stderr

    def test_no_date_means_the_utc_day_not_the_local_one(self):
        """Every cron in cowork/ is UTC, and `cron_times` matches a UTC date. A
        local `date.today()` hands a laptop east of UTC yesterday's schedule —
        already fully run — with today's heading on it.

        Bracketed rather than compared to one clock reading, so a run that
        straddles midnight cannot fail on a technicality.
        """
        before = datetime.now(UTC).date()
        result = self._run()
        after = datetime.now(UTC).date()
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["date"] in {before.isoformat(), after.isoformat()}


class TestSummaries:
    """Every routine says what it does, in one line short enough to post."""

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_every_routine_has_a_summary_line(self, routine):
        assert routine.summary, (
            f"{routine.path} has no `**Summary** — ...` line, so the rendered agenda "
            "would name it with nothing beside it"
        )

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_a_summary_stays_inside_the_cap(self, routine):
        assert len(routine.summary) <= setup.SUMMARY_LIMIT, (
            f"{routine.path} has a {len(routine.summary)}-character summary; it is rendered "
            f"as one line of a Slack message, so the cap is {setup.SUMMARY_LIMIT}"
        )

    def test_the_manifest_carries_the_summaries(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        for routine in setup.manifest()["routines"]:
            assert routine["summary"]


class TestPromptTemplate:
    """The registered prompt is thin on purpose, and points at a real file."""

    def test_the_template_matches_the_readme_blockquote(self):
        """The script's template and README's quoted one are the same sentence.

        They are two copies because a blockquote is not worth parsing, and the
        prompt is the entire link between an account-side routine and the repo
        file that actually instructs it. If they drift, the fleet keeps running
        and stops reading this folder.
        """
        tail = setup.README.read_text(encoding="utf-8").partition("So the registered prompt")[2]
        lines: list[str] = []
        for line in tail.splitlines():
            if line.startswith(">"):
                lines.append(line.lstrip("> ").strip())
            elif lines:
                break  # first contiguous run only — the Cron trap is a blockquote too
        quoted = " ".join(lines).strip()
        template = setup.PROMPT_TEMPLATE.format(name="<name>", path="<kind>/<file>.md")
        assert quoted == template, (
            f"README.md quotes:\n  {quoted}\nbut cowork_setup.py builds:\n  {template}\n"
            "These are the same sentence and must stay identical."
        )

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_the_prompt_names_the_routines_own_file(self, routine):
        assert f"`cowork/routines/{routine.path}`" in routine.prompt

    @pytest.mark.parametrize("routine", ROUTINES, ids=_routine_ids)
    def test_trigger_names_are_prefixed_and_unique(self, routine):
        assert routine.trigger_name.startswith("cowork: ")
        assert [r.trigger_name for r in ROUTINES].count(routine.trigger_name) == 1


class TestLabels:
    def test_the_label_set_is_shared_plus_workstreams_plus_types(self):
        names = {label.name for label in setup.expected_labels()}
        assert names == (
            {
                "cowork",
                "cowork:proposal",
                "claude-implement",
                "cowork:queued",
                "implement-blocked",
                "feedback-override",
                "release:promotion",
                "release:promote",
                "integration:candidate",
                "integration:approved",
                "review-capped",
                "fleet-ledger",
            }
            | {f"workstream:{w}" for w in WORKSTREAMS}
            | {f"type:{t}" for t in setup.PROPOSAL_TYPES}
        )

    def test_teardown_never_deletes_the_shared_type_labels(self):
        # The feedback system labels user-filed issues type:*; deleting a label
        # strips it off every issue on the repo, so teardown must leave them.
        survivors = {label.name for label in setup.teardown_labels()}
        assert not any(name.startswith("type:") for name in survivors)

    def test_the_type_vocabulary_covers_the_feedback_system(self):
        # feedback.py titles issues `[Bug] …` and labels them type:<kind>; the
        # two systems share the repo's label namespace, and cowork-setup is the
        # only machinery that creates labels — so every feedback type must have
        # its label created here. Derived, not retyped: a fifth FEEDBACK_TYPE
        # must fail this test until PROPOSAL_TYPES carries it.
        from yeaboi.feedback import FEEDBACK_TYPES

        assert {t.lower() for t in FEEDBACK_TYPES} <= set(setup.PROPOSAL_TYPES)

    def test_the_scout_vocabulary_is_narrower_than_the_label_vocabulary(self):
        # `feature` and `improvement` are labels the repo needs and finds no
        # routine may produce. They survive because `src/yeaboi/feedback.py`
        # files in-app USER feedback under the same names; what went away is
        # cowork-scout's opportunity pass, and with it any way for a sweep to
        # emit one. Capability work has exactly one home now — the campaign lane.
        assert set(setup.SCOUT_TYPES) <= set(setup.PROPOSAL_TYPES)
        assert "feature" not in setup.SCOUT_TYPES
        assert "improvement" not in setup.SCOUT_TYPES

    def test_the_scout_agent_and_the_constant_agree(self):
        # Derived, not retyped. The agent file's JSON schema is what a model
        # actually reads at run time, so a constant that drifts from it describes
        # a fleet that no longer exists — the same argument `parse_tiers` makes
        # about models.md. This pins the CONTRACT TEXT and not the behaviour;
        # nothing can test that a model honours a vocabulary, exactly as nothing
        # can test that `critical` is used honestly.
        assert setup.parse_scout_types() == setup.SCOUT_TYPES

    def test_no_charter_still_advertises_an_opportunity_pass(self):
        # The opportunity sections told a scout where a `feature` find was most
        # likely to be real. With the type gone from its vocabulary, a surviving
        # section is an instruction to produce something it cannot file.
        for charter in sorted(setup.WORKSTREAMS_DIR.glob("*.md")):
            body = charter.read_text(encoding="utf-8")
            assert "## Opportunity space" not in body, f"{charter.name} still declares an opportunity pass"
        agent = setup.SCOUT_AGENT.read_text(encoding="utf-8")
        assert "Hunt opportunities" not in agent

    def test_the_extends_grant_is_declared_on_both_sides(self):
        # `Extends` lets a campaign append a provider inside six other
        # workstreams' files. A grant written down only where it is USED is one
        # the owner can delete half of without noticing — so each owner names it
        # too, and this asserts the pair. The owners are parsed out of the
        # granting paragraph itself, so adding a site to it without telling its
        # owner fails here.
        charter = (setup.WORKSTREAMS_DIR / "integrations.md").read_text(encoding="utf-8")
        # The paragraph that GRANTS, not the Reads paragraph that cross-references
        # it — both contain the word, and only one names owners.
        _, sep, tail = charter.partition("\n**Extends** —")
        assert sep, "integrations.md no longer declares an Extends paragraph"
        block = tail.split("\n\n", 1)[0]
        assert block, "integrations.md no longer declares an Extends paragraph"
        owners = set(re.findall(r"— \*\*([a-z-]+)\*\*", block))
        assert owners, f"no owners named in the Extends paragraph: {block!r}"
        assert owners <= set(WORKSTREAMS), f"Extends names a workstream that does not exist: {owners}"
        for owner in sorted(owners):
            body = (setup.WORKSTREAMS_DIR / f"{owner}.md").read_text(encoding="utf-8")
            assert "**Extends**" in body, f"{owner}.md never acknowledges the campaign's Extends grant"
            assert "integrations" in body, f"{owner}.md acknowledges Extends without naming who holds it"
        # The file the rule was written for is outside the grant, on every angle.
        assert "mode_select/__init__.py" not in block

    def test_the_migration_extends_grant_is_declared_on_both_sides(self):
        # The migration lane carries the same grant shape: a wave PR moves the
        # version lockstep and the dual-maintenance record in platform's files,
        # by site and by operation. Same pairing rule as the campaign's above —
        # a grant written down only where it is used is one the owner can delete
        # half of without noticing.
        charter = (setup.WORKSTREAMS_DIR / "go-migration.md").read_text(encoding="utf-8")
        _, sep, tail = charter.partition("\n**Extends**")
        assert sep, "go-migration.md no longer declares an Extends paragraph"
        block = tail.split("\n\n", 1)[0]
        owners = set(re.findall(r"— \*\*([a-z-]+)\*\*", block))
        assert owners == {"platform"}, f"the migration lockstep sites belong to platform, not {owners}"
        body = (setup.WORKSTREAMS_DIR / "platform.md").read_text(encoding="utf-8")
        assert "**Extends**" in body, "platform.md never acknowledges the migration's Extends grant"
        assert "go-migration" in body, "platform.md acknowledges Extends without naming who holds it"
        # The repo's worst merge surface stays outside this grant too.
        assert "mode_select/__init__.py" not in block

    def test_the_digest_declares_a_section_for_every_scout_proposal_type(self):
        # The digest lists proposals in one section per type, so a type missing
        # from its section order is a kind of work that gets filed and then
        # never surfaced to the human who approves it. Derived, not retyped: an
        # eighth PROPOSAL_TYPES entry must fail here until the routine says
        # where it goes. `other` is excluded on purpose — cowork_setup.py
        # records that it is the feedback system's fallback and that no cowork
        # scout emits it, and digest.md says the same in step 2.
        #
        # Asserted against the section-order line rather than the whole file:
        # `feature` occurs eight times inside `feature-candidate` alone, so a
        # substring search over the document would still pass with every
        # section deleted.
        digest = (setup.ROUTINES_DIR / "cron" / "digest.md").read_text(encoding="utf-8")
        order = re.search(r"Section order is fixed: \*\*(.+?)\*\*", digest)
        assert order, "digest.md no longer declares a fixed section order"
        sections = {name.strip().lower() for name in order.group(1).split(",")}
        for kind in setup.SCOUT_TYPES:
            assert {kind, f"{kind}s"} & sections, f"digest.md's section order omits type:{kind}"
        # And the two a scout cannot emit have no section, because a heading for a
        # bucket that is always empty is the "nothing today" fatigue the stop
        # conditions exist to prevent. User feedback reaches the digest through
        # 💡 Feature candidates instead, which is a different query and says so.
        assert not {"features", "improvements"} & sections

    # Sections the digest heads that are not one of the PROPOSAL_TYPES: user
    # feedback, the marketing draft, and the health/calibration reporting.
    NON_TYPE_SECTIONS = (
        "Feature candidates",
        "Integration",
        "Approved, no PR yet",
        "Blocked",
        "Held",
        "Silent",
        "Calibration",
    )

    @staticmethod
    def _emoji_table() -> dict[str, str]:
        """The digest's ``| Section | Emoji |`` table, as ``{name: emoji}``."""
        digest = (setup.ROUTINES_DIR / "cron" / "digest.md").read_text(encoding="utf-8")
        # Deliberately permissive on the name (a section could gain a digit or a
        # hyphen) and strict about there being exactly two cells, so a row that
        # drifts out of the shape fails here rather than vanishing from the set.
        pairs = re.findall(r"^\s*\|\s*([^|\-][^|]*?)\s*\|\s*([^|\s]+)\s*\|\s*$", digest, re.M)
        rows = [(name, emoji) for name, emoji in pairs if name != "Section"]
        names = [name for name, _ in rows]
        assert len(names) == len(set(names)), f"digest.md's emoji table repeats a section: {names}"
        return dict(rows)

    def test_the_digest_declares_an_emoji_for_every_section(self):
        # Each digest section is headed by one fixed emoji, so a returning
        # reader finds a section by shape before reading a word. That only
        # works if the emoji is constant, which means it has to be written
        # down — and the table is the other half of the section order above:
        # an eighth PROPOSAL_TYPES entry gets a section from that test and an
        # anchor from this one.
        rows = self._emoji_table()
        assert rows, "digest.md no longer declares an emoji table"
        for kind in setup.SCOUT_TYPES:
            assert {kind, f"{kind}s"} & {name.lower() for name in rows}, f"digest.md's emoji table omits type:{kind}"
        # The type sections are only half the message; a dropped row here is a
        # heading with nothing to anchor it and nothing to notice.
        for section in self.NON_TYPE_SECTIONS:
            assert section in rows, f"digest.md's emoji table omits the {section} section"
        # Two sections sharing an emoji defeats the whole point of having one —
        # you can no longer find a section by shape.
        assert len(set(rows.values())) == len(rows), f"digest.md reuses a section emoji: {rows}"
        # The approval verbs are never spent as decoration: a reader who meets
        # one in a heading has to stop and work out whether it means something.
        assert not {"\u2705", "\u274c"} & set(rows.values()), "digest.md uses an approval verb as a section emoji"

    def test_every_digest_heading_uses_its_declared_emoji(self):
        # The table above is only worth having if the headings obey it. Nothing
        # at run time would notice a heading that quietly lost its emoji, or an
        # example that drifted onto a different one from the table it sits next
        # to — the digest is a prompt, so the worked example is what actually
        # gets copied.
        digest = (setup.ROUTINES_DIR / "cron" / "digest.md").read_text(encoding="utf-8")
        rows = self._emoji_table()

        # ```slack marks a literal channel message, which is what TestSlackTemplates
        # lints; the info string is optional here so this keeps passing either way.
        fences = re.findall(r"^\s*```(?:slack)?\n(.*?)^\s*```", digest, re.M | re.S)
        assert fences, "digest.md no longer shows the message shape as a worked example"
        example = "\n".join(fences)

        # A heading in the example: "<emoji> **<Section>** (<n> open…)".
        headings = re.findall(r"^\s*(\S+)\s+\*\*([^*]+)\*\*\s*\(", example, re.M)
        assert len(headings) >= 2, "the worked example no longer shows more than one section"
        for emoji, name in headings:
            section = next((s for s in rows if name.startswith(s)), None)
            assert section, f"the worked example heads a section the emoji table omits: {name!r}"
            assert emoji == rows[section], (
                f"the worked example heads {name!r} with {emoji!r}, table says {rows[section]!r}"
            )
        # …and the same line with the emoji stripped is the failure this catches.
        bare = re.findall(r"^\s*\*\*([^*]+)\*\*\s*\(", example, re.M)
        assert not bare, f"a worked-example heading lost its emoji anchor: {bare}"

        # The sections that live in prose rather than in the example still have
        # to pair with their emoji somewhere, or the anchor was never written.
        for section in self.NON_TYPE_SECTIONS:
            assert f"{rows[section]} **{section}" in digest, (
                f"digest.md never heads the {section} section with {rows[section]!r}"
            )

    def test_there_are_seventeen_workstreams(self):
        # The count is load-bearing: CLAUDE.md, cowork/README.md and the digest's
        # health check all spell it out in prose, and none of them is derived.
        # Thirteen maintain a surface, `security` scouts twice a week, and
        # `integrations` is the one that builds — `marketing` went with the
        # opportunity lane, because it fed neither hand-test track. The sixteenth
        # is `fleet`, whose subject is `cowork/` rather than any of the code: it
        # is the only charter that owns the instructions the others run under,
        # and the only one bounded by a list of files no charter may reach
        # (`setup.CONSTITUTION`, asserted in `test_cowork_retune.py`). The
        # seventeenth is `go-migration`, the second builder — its approval is
        # the merged program of record rather than a weekly ✅
        # (`house-rules.md`, **The migration lane**).
        assert len(WORKSTREAMS) == 17

    def test_every_workstream_owns_at_least_one_routine(self):
        owned = {r.workstream for r in ROUTINES if r.workstream}
        assert owned == set(WORKSTREAMS), (
            f"workstreams with no routine: {sorted(set(WORKSTREAMS) - owned)}; "
            f"routines naming an unknown workstream: {sorted(owned - set(WORKSTREAMS))}"
        )

    def test_labels_carry_a_colour_and_a_description(self):
        for label in setup.expected_labels():
            assert re.fullmatch(r"[0-9a-f]{6}", label.color), f"{label.name} has a malformed colour"
            assert label.description


class TestCharterCoverage:
    """The charters must cover the repo, not merely agree with the label list.

    A scout reads only the paths its charter declares, so a module no charter
    names is one no routine will ever open — and every routine still reports
    itself healthy. Fourteen top-level modules were in that state when this class
    was written.
    """

    def test_every_top_level_module_is_owned_or_excused(self):
        report = setup.Report()
        setup.check_charter_coverage(report)
        assert report.ok, report.problems

    def test_an_unclaimed_module_fails(self, tmp_path, monkeypatch):
        package = tmp_path / "src" / "yeaboi"
        package.mkdir(parents=True)
        (package / "orphan.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(setup, "REPO_ROOT", tmp_path)

        report = setup.Report()
        setup.check_charter_coverage(report)
        assert not report.ok

    def test_the_excuse_list_names_a_reason(self):
        for module, reason in setup.UNOWNED_MODULES.items():
            assert reason.strip(), f"{module} is excused with no reason"

    def test_an_excused_module_is_never_also_reported_as_owned(self):
        """The declaration wins over an accidental substring match.

        `__init__.py` is excused as a package marker, and is *also* matched by
        platform's `mcp/.../__init__.py`. If the coincidence were allowed to
        stand in for the reason, deleting that nested path from a charter would
        silently turn the excuse back on with nothing to say it had.
        """
        assert not (setup.owned_modules() & set(setup.UNOWNED_MODULES))

    def test_ownership_is_read_from_the_owns_block_only(self, tmp_path, monkeypatch):
        """A charter disclaiming a module must not read as claiming it.

        Charters say things like "**`telemetry.py` is not this feature**" in their
        standing concerns. Matching the whole document counts that as ownership,
        so the next module excused by a "not yours" sentence would pass the check
        silently — the exact failure the check exists to catch.
        """
        charters = tmp_path / "cowork" / "workstreams"
        charters.mkdir(parents=True)
        (charters / "example.md").write_text(
            "# example\n\n**Owns** — `src/yeaboi/claimed.py`\n\n"
            "## Standing concerns\n\n- **`disclaimed.py` is not yours** — it belongs to platform.\n",
            encoding="utf-8",
        )
        package = tmp_path / "src" / "yeaboi"
        package.mkdir(parents=True)
        for name in ("claimed.py", "disclaimed.py"):
            (package / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(setup, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(setup, "WORKSTREAMS_DIR", charters)

        assert setup.owned_modules() == {"claimed.py"}

    def test_a_multi_line_owns_block_is_read_whole(self, tmp_path, monkeypatch):
        """Real `**Owns**` lines wrap over several lines; all of them count."""
        charters = tmp_path / "cowork" / "workstreams"
        charters.mkdir(parents=True)
        (charters / "example.md").write_text(
            "# example\n\n**Owns** — `src/yeaboi/first.py`,\n`second.py`, `third.py`\n\n## Standing concerns\n",
            encoding="utf-8",
        )
        package = tmp_path / "src" / "yeaboi"
        package.mkdir(parents=True)
        for name in ("first.py", "second.py", "third.py"):
            (package / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(setup, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(setup, "WORKSTREAMS_DIR", charters)

        assert setup.owned_modules() == {"first.py", "second.py", "third.py"}


class TestModelsTable:
    def test_all_four_repository_variables_are_defined(self):
        variables = setup.parse_model_variables()
        assert set(variables) == {
            "YEABOI_MODEL_HEAVY",
            "YEABOI_MODEL_DEEP",
            "YEABOI_MODEL_STANDARD",
            "YEABOI_MODEL_FAST",
        }
        assert all(variables.values())

    def test_every_variable_value_is_a_tier_id(self):
        ids = {tier.model_id for tier in TIERS.values() if tier.model_id}
        for name, value in setup.parse_model_variables().items():
            assert value in ids, f"{name} is `{value}`, which is not any tier's id in the table above it"

    def test_only_the_tier_table_is_read_as_tiers(self):
        """models.md has three tables, and all three open with a backticked cell.

        Unscoped, the tier map gains a `migrator` "tier" whose model id is an
        English sentence and a `YEABOI_MODEL_HEAVY` one — and a routine mis-tiered
        `migrator` then passes the does-this-tier-exist check and gets that
        sentence POSTed as its model.
        """
        assert set(TIERS) == {"heavy", "deep", "standard", "fast", "inherit"}

    def test_inherit_names_no_model(self):
        # `inherit` is the safe failure: an agent that pins nothing lands on the
        # caller's model rather than something cheap and wrong.
        assert TIERS["inherit"].model_id is None

    def test_security_is_never_tiered_heavy(self):
        """Fable reroutes cybersecurity queries, so a security sweep on `heavy`
        would silently survey the guardrails with a model nobody chose."""
        security = next(r for r in ROUTINES if r.workstream == "security")
        assert security.tier != "heavy"


class TestTargets:
    def test_the_three_targets_parse(self):
        targets = setup.parse_targets()
        assert set(targets) == {"linear", "slack", "notion"}
        assert all(targets.values())

    def test_the_dod_checklist_is_not_read_as_a_target(self):
        # The ten-item table above ## Targets has the same three-cell shape and a
        # backticked last column, so reading the file whole yields `make test`.
        assert "make test" not in setup.parse_targets().values()


class TestManifest:
    def test_the_manifest_is_json_serialisable_and_complete(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)  # no gh call
        payload = json.loads(json.dumps(setup.manifest()))
        assert set(payload) == {
            "repo",
            "repo_url",
            "connectors",
            "default_allowed_tools",
            "targets",
            "labels",
            "linear_labels",
            "variables",
            "routines",
        }
        assert len(payload["routines"]) == len(ROUTINES)
        assert set(payload["routines"][0]) >= {"trigger_name", "trigger_id", "prompt", "allowed_tools"}
        assert payload["connectors"] == ["Linear", "Slack", "Notion"]
        assert "Task" in payload["default_allowed_tools"], "a sweep spawns the crew agents"

    def test_every_cron_routine_carries_what_registration_needs(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        for routine in setup.manifest()["routines"]:
            if routine["kind"] != "cron":
                continue
            assert routine["cron"] and routine["model"] and routine["prompt"] and routine["trigger_name"]

    def test_a_registered_routine_carries_the_id_that_addresses_it(self, monkeypatch):
        """`pause`/`resume`/`run` need an id, and listing the fleet to find one pages.

        The relay used to resolve a name against a `RemoteTrigger list`, which
        stopped answering for most of the fleet the moment it crossed twenty
        routines — and answered "no such routine" rather than failing.
        """
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        addressable = [r for r in setup.manifest()["routines"] if r["trigger_id"]]
        assert len(addressable) == len(setup.recorded_triggers())
        assert all(r["trigger_id"].startswith("trig_") for r in addressable)

    def test_an_undeployed_routine_carries_no_id_rather_than_a_guess(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        monkeypatch.setattr(setup, "recorded_triggers", lambda *a, **k: {})
        assert all(routine["trigger_id"] is None for routine in setup.manifest()["routines"])


class TestMergeGateCheck:
    """The doctor's probe of the one setting that decides if the lane merges.

    `pr-feedback` on the main-branch ruleset is what every workflow checks before
    arming `gh pr merge --auto`. If it were dropped later, those workflows would
    fail *quietly* — declining to arm auto-merge looks exactly like a lane that
    had nothing to do — so `cron/cd-deploy.md` running `--check` on every merge is
    the thing that would notice.
    """

    def test_an_armed_gate_is_silent(self, monkeypatch):
        monkeypatch.setattr(setup, "merge_gate_armed", lambda: True)
        report = setup.Report()
        setup.check_merge_gate(report)
        assert report.ok
        assert report.notes == []

    def test_a_missing_gate_is_a_finding(self, monkeypatch):
        monkeypatch.setattr(setup, "merge_gate_armed", lambda: False)
        report = setup.Report()
        setup.check_merge_gate(report)
        assert not report.ok
        assert "pr-feedback" in report.problems[0]

    def test_an_unanswerable_question_is_a_note_not_a_finding(self, monkeypatch):
        """No `gh` is not the same answer as no gate.

        Conflating them reddens the doctor on every machine without `gh`
        authenticated, which is how a check gets ignored rather than fixed.
        """
        monkeypatch.setattr(setup, "merge_gate_armed", lambda: None)
        report = setup.Report()
        setup.check_merge_gate(report)
        assert report.ok
        assert report.notes and "not checked" in report.notes[0]

    def test_the_probe_reports_none_without_gh_or_a_token(self, monkeypatch):
        """Neither transport can answer, so the answer is None.

        The token has to be cleared explicitly: without `gh` the probe now falls
        through to REST, and a developer with GH_TOKEN exported would otherwise
        have this test make a real request against their own repo.
        """
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setattr(setup.shutil, "which", lambda name: None)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert setup.merge_gate_armed() is None

    def test_the_probe_reads_the_ruleset_over_rest(self, monkeypatch):
        """The jq expression's answer, computed in Python — there is no jq in a
        routine session either."""
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setenv("GH_TOKEN", "t")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        ruleset = [
            {"type": "deletion"},
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci"}]}},
        ]
        monkeypatch.setattr(setup.transport, "api", lambda *a, **k: setup.ApiResult(True, ruleset))
        assert setup.merge_gate_armed() is False

        ruleset[1]["parameters"]["required_status_checks"].append({"context": "pr-feedback"})
        assert setup.merge_gate_armed() is True

    def test_a_failed_rest_query_is_still_unanswerable(self, monkeypatch):
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setenv("GH_TOKEN", "t")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.transport, "api", lambda *a, **k: setup.ApiResult(False, error="HTTP 404"))
        assert setup.merge_gate_armed() is None

    def test_the_probe_reports_none_when_the_query_fails(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup, "_gh", lambda *a: subprocess.CompletedProcess(a, 1, "", "boom"))
        assert setup.merge_gate_armed() is None

    @pytest.mark.parametrize(("stdout", "expected"), [("true", True), ("false", False), ("", False)])
    def test_the_probe_reads_the_jq_answer(self, monkeypatch, stdout, expected):
        monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup, "_gh", lambda *a: subprocess.CompletedProcess(a, 0, stdout, ""))
        assert setup.merge_gate_armed() is expected


class TestCheckMode:
    """``--check --local`` is the half that needs no network, so it is testable."""

    def _run(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(cwd / "scripts" / "cowork_setup.py"), "--check", "--local"],
            capture_output=True,
            text=True,
            check=False,
        )

    @pytest.fixture
    def repo_copy(self, tmp_path: Path) -> Path:
        """A throwaway copy of cowork/ + the script, safe to corrupt."""
        copy = tmp_path / "repo"
        (copy / "scripts").mkdir(parents=True)
        shutil.copy(_MODULE_PATH, copy / "scripts" / "cowork_setup.py")
        # The transport is a sibling import, so it has to travel with the script.
        shutil.copy(ROOT / "scripts" / "_gh_transport.py", copy / "scripts" / "_gh_transport.py")
        shutil.copytree(ROOT / "cowork", copy / "cowork")
        return copy

    def test_a_pristine_copy_is_clean(self, repo_copy: Path):
        result = self._run(repo_copy)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_cron_that_disagrees_with_the_table_fails(self, repo_copy: Path):
        readme = repo_copy / "cowork" / "README.md"
        readme.write_text(readme.read_text().replace("`0 7 * * 1` Mon", "`0 9 * * 1` Mon"))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "planning-sweep" in result.stderr and "README says" in result.stderr

    def test_a_routine_missing_from_the_table_fails(self, repo_copy: Path):
        (repo_copy / "cowork" / "routines" / "cron" / "orphan-sweep.md").write_text(
            "# orphan\n\n**Trigger** — cron `0 5 * * 1`\n"
        )
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "orphan-sweep.md is not in the README" in result.stderr

    def test_a_table_row_with_no_file_fails(self, repo_copy: Path):
        (repo_copy / "cowork" / "routines" / "cron" / "digest.md").unlink()
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "which does not exist" in result.stderr

    def test_an_unknown_tier_fails(self, repo_copy: Path):
        readme = repo_copy / "cowork" / "README.md"
        readme.write_text(readme.read_text().replace("| planning | `standard` |", "| planning | `turbo` |"))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "turbo" in result.stderr

    def test_a_routine_with_no_summary_fails(self, repo_copy: Path):
        """The doctor half of TestSummaries — deleting the branch must not leave
        the suite green, and `make cowork-check` is what a contributor runs."""
        path = repo_copy / "cowork" / "routines" / "cron" / "digest.md"
        path.write_text("\n".join(line for line in path.read_text().splitlines() if not line.startswith("**Summary**")))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "digest.md has no `**Summary**" in result.stderr

    def test_a_summary_longer_than_the_cap_fails(self, repo_copy: Path):
        path = repo_copy / "cowork" / "routines" / "cron" / "digest.md"
        path.write_text(path.read_text().replace("**Summary** — ", "**Summary** — " + "x" * setup.SUMMARY_LIMIT))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "character **Summary** line" in result.stderr

    def test_a_cron_that_never_fires_fails(self, repo_copy: Path):
        """Five valid fields that never come round. It would register and stay
        silent forever, and the only thing that would notice is the digest
        reporting a silent scout three weeks later."""
        for path in (
            repo_copy / "cowork" / "README.md",
            repo_copy / "cowork" / "routines" / "cron" / "roadmap-sweep.md",
        ):
            path.write_text(path.read_text().replace("30 7 12 * *", "30 7 31 2 *"))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "fires on no day of a whole year" in result.stderr

    def test_a_cron_the_parser_does_not_model_fails(self, repo_copy: Path):
        for path in (
            repo_copy / "cowork" / "README.md",
            repo_copy / "cowork" / "routines" / "cron" / "roadmap-sweep.md",
        ):
            path.write_text(path.read_text().replace("30 7 12 * *", "30 7 * * MON"))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "does not parse" in result.stderr and "name aliases" in result.stderr

    def test_the_cron_trap_fails(self, repo_copy: Path):
        """A fortnightly slot that also restricts day-of-week runs near-daily."""
        for path in (
            repo_copy / "cowork" / "README.md",
            repo_copy / "cowork" / "routines" / "cron" / "roadmap-sweep.md",
        ):
            path.write_text(path.read_text().replace("30 7 12 * *", "30 7 12 * 2"))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "day-of-month AND day-of-week" in result.stderr


class TestApplyRunExitCodes:
    """The default run's exit codes, which `cron/cd-deploy.md` step 3 branches on.

    Not part of `TestCheckMode` above: this is neither ``--check`` nor
    ``--local``. It is the apply run, and the only reason it is assertable
    without a network is that it refuses before choosing a transport.
    """

    @pytest.fixture
    def repo_copy(self, tmp_path: Path) -> Path:
        """A throwaway copy of cowork/ + the script, safe to corrupt."""
        copy = tmp_path / "repo"
        (copy / "scripts").mkdir(parents=True)
        shutil.copy(_MODULE_PATH, copy / "scripts" / "cowork_setup.py")
        # The transport is a sibling import, so it has to travel with the script.
        shutil.copy(ROOT / "scripts" / "_gh_transport.py", copy / "scripts" / "_gh_transport.py")
        shutil.copytree(ROOT / "cowork", copy / "cowork")
        return copy

    def test_the_default_run_refuses_a_disagreeing_repo_with_exit_2(self, repo_copy: Path):
        """2, not 1, and `cron/cd-deploy.md` step 3 is what reads the difference.

        1 from that step means the GitHub apply degraded — no labels created, or
        a variable rejected — which says nothing about a routine's trigger body
        and must not stop the deploy. 2 means `cowork/` itself disagrees, and
        registering anything from it would be registering a routine that cannot
        read its own instructions. The run reaches neither transport: it refuses
        before `github_ready()` is ever called, which is what makes this
        assertable without a network.
        """
        (repo_copy / "cowork" / "routines" / "cron" / "digest.md").unlink()
        result = subprocess.run(
            [sys.executable, str(repo_copy / "scripts" / "cowork_setup.py")],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", "HOME": str(repo_copy)},
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "disagrees with itself" in result.stderr


# --- the account half --------------------------------------------------------
#
# Routines live in the account, not the repo, so the only way to test any of this
# is to hand the functions a snapshot. Two kinds are used, deliberately:
#
#   `_perfect_snapshot()` is generated from desired_trigger(), so it never goes
#   stale when a routine is added — but a mistake mirrored in desired_trigger()
#   and observed_trigger() would round-trip through it cleanly and pass.
#
#   `tests/fixtures/cowork_trigger_live.json` is one real API response, which is
#   the only input in this file that neither function produced.

CONNECTORS = [
    {"name": name, "connector_uuid": f"uuid-{name.lower()}", "url": f"https://mcp.{name.lower()}.com/mcp"}
    for name in setup.CONNECTORS
]
REPO_URL = "https://github.com/dinho149/yeaboi.ai"
ENVIRONMENT = "env_test"
LIVE_FIXTURE = ROOT / "tests" / "fixtures" / "cowork_trigger_live.json"
# The fixture carries a placeholder rather than a model id: a real one there would
# be a second place a model is written down, which is the drift models.md exists
# to prevent (and test_cowork_models.py enforces). The tier is resolved instead.
FIXTURE_MODEL = "MODEL_ID_FROM_MODELS_MD"


def _trigger_id(index: int) -> str:
    return f"trig_{index:024d}"


def _perfect_snapshot(routines=None) -> list[dict]:
    """What a fleet that exactly matches the repo would look like on the wire.

    Every routine, event ones included — they are registered by API now, so a
    "perfect" fleet that omitted them would read as four permanent creates.
    """
    routines = list(routines or ROUTINES)
    snapshot = []
    for index, routine in enumerate(routines):
        body = setup.desired_trigger(routine, REPO_URL, ENVIRONMENT, CONNECTORS)
        body["id"] = _trigger_id(index)
        snapshot.append(body)
    return snapshot


def _by_name(snapshot: list[dict], name: str) -> dict:
    return next(entry for entry in snapshot if entry["name"] == f"cowork: {name}")


class TestDesiredTrigger:
    """The create body, which nothing else in the repo can validate."""

    @pytest.fixture
    def body(self) -> dict:
        routine = next(r for r in CRON_ROUTINES if r.name == "security-sweep")
        return setup.desired_trigger(routine, REPO_URL, ENVIRONMENT, CONNECTORS)

    def test_it_carries_the_prompt_verbatim(self, body: dict):
        """The prompt is the routine's entire behaviour — it points at the file.

        A prompt that is paraphrased, truncated or re-wrapped still looks fine in
        the web form and still runs; it just reads a file that does not exist and
        reports nothing.
        """
        content = body["job_config"]["ccr"]["events"][0]["data"]["message"]["content"]
        expected = next(r for r in CRON_ROUTINES if r.name == "security-sweep").prompt
        assert content == expected
        assert "cowork/routines/cron/security-sweep.md" in content

    def test_the_model_comes_from_the_tier_table(self, body: dict):
        assert body["job_config"]["ccr"]["session_context"]["model"] == TIERS["deep"].model_id

    def test_the_name_is_prefixed_so_a_rerun_reconciles(self, body: dict):
        assert body["name"] == "cowork: security-sweep"

    def test_a_new_routine_starts_enabled(self, body: dict):
        assert body["enabled"] is True

    def test_the_connectors_ride_through_unchanged(self, body: dict):
        """Their uuids are account-specific, so they are reused and never invented."""
        assert body["mcp_connections"] == CONNECTORS

    def test_it_names_the_repo_and_the_tools(self, body: dict):
        context = body["job_config"]["ccr"]["session_context"]
        assert context["sources"] == [{"git_repository": {"url": REPO_URL}}]
        assert context["allowed_tools"] == list(setup.ALLOWED_TOOLS)

    def test_the_body_is_json_serialisable(self, body: dict):
        assert json.loads(json.dumps(body))["cron_expression"] == "0 6 * * 1,4"


class TestObservedTrigger:
    """Read against a real API response, not one this module generated."""

    @pytest.fixture
    def live(self) -> dict:
        text = LIVE_FIXTURE.read_text().replace(FIXTURE_MODEL, TIERS["deep"].model_id)
        return setup.snapshot(json.loads(text))[0]

    def test_every_compared_field_is_found(self, live: dict):
        observed = setup.observed_trigger(live)
        assert observed["trigger_name"] == "cowork: security-sweep"
        assert observed["cron"] == "0 6 * * 1,4"
        assert observed["model"] == TIERS["deep"].model_id
        assert observed["prompt"].startswith("You are the `security` workstream")
        assert observed["allowed_tools"] == tuple(sorted(setup.ALLOWED_TOOLS))
        assert observed["repo_url"] == REPO_URL
        assert observed["connectors"] == ("Linear", "Notion", "Slack")
        assert observed["enabled"] is True
        assert observed["url"].endswith(live["id"])

    def test_the_real_response_matches_what_we_would_send(self, live: dict):
        """The two halves agree on a payload neither of them produced."""
        routine = next(r for r in CRON_ROUTINES if r.name == "security-sweep")
        wanted = setup.desired_trigger(routine, REPO_URL, live["job_config"]["ccr"]["environment_id"], CONNECTORS)
        observed = setup.observed_trigger(live)
        assert observed["prompt"] == wanted["job_config"]["ccr"]["events"][0]["data"]["message"]["content"]
        assert observed["cron"] == wanted["cron_expression"]
        assert observed["model"] == wanted["job_config"]["ccr"]["session_context"]["model"]

    def test_a_hollow_payload_reads_as_differing_rather_than_raising(self):
        """A doctor that crashes on an unfamiliar response is one nobody re-runs."""
        observed = setup.observed_trigger({"name": "cowork: ghost", "id": "trig_x"})
        assert observed["cron"] is None and observed["prompt"] == "" and observed["connectors"] == ()

    def test_the_fixture_names_no_model(self):
        """Same contract as cowork/: models.md is the only place an id is written."""
        assert not re.search(r"claude-(?:opus|sonnet|haiku|fable)-[\w.-]*\d", LIVE_FIXTURE.read_text())


class TestTriggerPlan:
    def test_a_matching_fleet_needs_nothing(self):
        plan = setup.trigger_plan(_perfect_snapshot())
        assert plan.clean
        assert sorted(plan.ok) == sorted(r.name for r in ROUTINES)
        assert plan.create == [] and plan.update == [] and plan.orphans == []

    def test_every_registered_routine_yields_a_url(self):
        plan = setup.trigger_plan(_perfect_snapshot())
        assert len(plan.urls) == len(ROUTINES)
        assert plan.urls["cron/security-sweep.md"].startswith("https://claude.ai/code/routines/trig_")

    def test_a_missing_routine_becomes_a_create_with_a_full_body(self):
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        plan = setup.trigger_plan(snapshot)
        assert [action.name for action in plan.create] == ["digest"]
        body = plan.create[0].body
        assert body["name"] == "cowork: digest" and body["cron_expression"] == "15 8 * * *"
        assert body["mcp_connections"] == CONNECTORS

    @pytest.mark.parametrize(
        "field, mutate",
        [
            ("cron", lambda e: e.update(cron_expression="0 0 * * 0")),
            ("model", lambda e: e["job_config"]["ccr"]["session_context"].update(model="something-else")),
            (
                "prompt",
                lambda e: e["job_config"]["ccr"]["events"][0]["data"]["message"].update(content="do whatever"),
            ),
            ("allowed_tools", lambda e: e["job_config"]["ccr"]["session_context"].update(allowed_tools=["Read"])),
            (
                "repo_url",
                lambda e: e["job_config"]["ccr"]["session_context"].update(
                    sources=[{"git_repository": {"url": "https://github.com/someone/else"}}]
                ),
            ),
            ("connectors", lambda e: e.update(mcp_connections=[{"name": "Gmail"}])),
        ],
    )
    def test_each_compared_field_is_actually_compared(self, field: str, mutate):
        snapshot = _perfect_snapshot()
        mutate(_by_name(snapshot, "retro-sweep"))
        plan = setup.trigger_plan(snapshot)
        assert [action.name for action in plan.update] == ["retro-sweep"]
        assert field in plan.update[0].fields
        assert plan.update[0].trigger_id is not None

    def test_an_extra_connector_is_removed_rather_than_adopted(self):
        """The live connector set is the one input that cannot be trusted.

        Every connector on the account is attached by default, so an over-broad
        set is the state *before* a deploy, not an anomaly. Reading the desired
        set off the live routines would make wanted == drifted: deploy would
        report connector drift, post a patch changing nothing, and report the
        same drift forever. Mutating every entry, because reading only the first
        is precisely the mistake being guarded against.
        """
        snapshot = _perfect_snapshot()
        for entry in snapshot:
            entry["mcp_connections"].append({"name": "Gmail", "connector_uuid": "uuid-gmail", "url": "x"})
        plan = setup.trigger_plan(snapshot)
        assert len(plan.update) == len(ROUTINES)
        assert [c["name"] for c in plan.update[0].body["mcp_connections"]] == list(setup.CONNECTORS)

    def test_the_connector_order_is_ours_not_the_accounts(self):
        snapshot = _perfect_snapshot()
        for entry in snapshot:
            entry["mcp_connections"].reverse()
        assert [c["name"] for c in setup.connectors_of(snapshot)] == list(setup.CONNECTORS)

    def test_a_cron_change_patches_only_the_cron(self):
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "retro-sweep")["cron_expression"] = "0 0 * * 0"
        patch = setup.trigger_plan(snapshot).update[0].body
        assert patch == {"cron_expression": "30 7 5,19 * *"}

    def test_a_prompt_change_resends_the_whole_job_config(self):
        """A nested partial merge is not something to guess at from the outside."""
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "retro-sweep")["job_config"]["ccr"]["events"][0]["data"]["message"]["content"] = "x"
        patch = setup.trigger_plan(snapshot).update[0].body
        assert "job_config" in patch and "cron_expression" not in patch
        content = patch["job_config"]["ccr"]["events"][0]["data"]["message"]["content"]
        assert content.endswith("follow it exactly.")

    def test_a_routine_with_no_readme_row_is_an_orphan(self):
        """A renamed routine keeps firing at a file that no longer exists."""
        snapshot = _perfect_snapshot()
        snapshot.append({"id": "trig_ghost", "name": "cowork: ghost-sweep", "enabled": True})
        plan = setup.trigger_plan(snapshot)
        assert [orphan["trigger_name"] for orphan in plan.orphans] == ["cowork: ghost-sweep"]
        assert plan.create == [] and plan.update == []

    def test_a_missing_routine_next_to_an_orphan_is_flagged(self):
        """The snapshot is transcribed by a model, so a damaged name is a real risk.

        It presents as one routine missing and one unrecognised — and acting on
        that would register a second copy of a routine that is already firing.
        Every other consequence of a bad snapshot self-corrects on the next run.
        """
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "retro-sweep")["name"] = "cowork: retro-sweeep"
        plan = setup.trigger_plan(snapshot)
        assert plan.suspicious
        assert [action.name for action in plan.create] == ["retro-sweep"]
        assert [orphan["trigger_name"] for orphan in plan.orphans] == ["cowork: retro-sweeep"]

    def test_a_plain_missing_routine_is_not_flagged(self):
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        assert not setup.trigger_plan(snapshot).suspicious

    def test_a_plain_orphan_is_not_flagged(self):
        snapshot = _perfect_snapshot()
        snapshot.append({"id": "trig_ghost", "name": "cowork: ghost-sweep", "enabled": True})
        assert not setup.trigger_plan(snapshot).suspicious

    def test_a_blocked_create_beside_an_orphan_is_not_flagged(self):
        """A run that cannot create anything has nothing dangerous to refuse.

        Retiring a routine leaves an orphan no API can delete, so if a blocked
        create counted, `cd-deploy` — which always runs `--no-create` — would
        exit 2 on every firing from the first retirement onwards and quietly
        stop applying updates for good.
        """
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        snapshot.append({"id": "trig_ghost", "name": "cowork: retired-sweep", "enabled": True})
        plan = setup.trigger_plan(snapshot, allow_create=False)
        assert plan.creates_blocked == ["digest"] and plan.orphans
        assert not plan.suspicious
        assert setup.trigger_plan(snapshot).suspicious, "an interactive deploy still asks"

    def test_a_routine_someone_else_made_is_left_alone(self):
        """Only the `cowork: ` prefix is ours. Deleting anything else is not our call."""
        snapshot = _perfect_snapshot()
        snapshot.append({"id": "trig_theirs", "name": "my morning inbox", "enabled": True})
        assert setup.trigger_plan(snapshot).orphans == []

    def test_a_paused_routine_is_reported_and_not_reconciled(self):
        """`pause` is a supported verb, so deploy must not quietly undo it."""
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "poker-sweep")["enabled"] = False
        plan = setup.trigger_plan(snapshot)
        assert plan.disabled == ["poker-sweep"]
        assert plan.update == [], "deploy would have re-enabled a deliberate pause"
        assert plan.clean

    def test_a_different_environment_is_not_drift(self):
        """environment_id is per-machine — comparing it flags every teammate's fleet."""
        snapshot = _perfect_snapshot()
        for entry in snapshot:
            entry["job_config"]["ccr"]["environment_id"] = "env_someone_elses_laptop"
        assert setup.trigger_plan(snapshot).clean

    def test_an_empty_account_is_all_creates(self):
        plan = setup.trigger_plan([], repo_url=REPO_URL, environment_id=ENVIRONMENT, connectors=CONNECTORS)
        assert len(plan.create) == len(ROUTINES)
        assert plan.ok == [] and plan.urls == {} and plan.needs == []

    def test_a_first_deploy_names_what_only_the_account_can_supply(self):
        """The API accepts an empty string, so nothing downstream would notice.

        Twenty-two routines register pointing at no repository, on no environment,
        with every connector attached — and it looks like it worked until the
        first Monday.
        """
        plan = setup.trigger_plan([])
        assert sorted(plan.needs) == ["connectors", "environment_id", "repo_url"]

    def test_nothing_to_create_needs_nothing(self):
        assert setup.trigger_plan(_perfect_snapshot()).needs == []

    def test_a_truncated_page_is_refused_rather_than_read_as_missing(self):
        """A short page yields creates with no orphan, so `suspicious` cannot see it."""
        with pytest.raises(ValueError, match="has_more"):
            setup.snapshot({"data": _perfect_snapshot()[:4], "has_more": True})

    def test_the_event_routines_are_planned_without_a_cron(self):
        """They are registered by API now; a webhook trigger attaches the event.

        They were unplannable while the routines API took a cron expression only
        — that was this test's old assertion, and the reason cowork/README.md
        told you to add three routines by hand in a web form. The API accepts a
        cron-less create now, so the body they get is the one every other routine
        gets, minus `cron_expression`; what fires them is a second POST, planned
        under `webhooks`.
        """
        plan = setup.trigger_plan([], repo_url=REPO_URL, environment_id=ENVIRONMENT, connectors=CONNECTORS)
        created = {action.name: action for action in plan.create}
        events = [r for r in ROUTINES if r.kind == "event"]
        assert events, "the fixture lost its event routines"
        for routine in events:
            assert routine.name in created, f"{routine.path} is registered now and must be planned"
            assert "cron_expression" not in created[routine.name].body

    def test_an_event_routine_that_grew_a_cron_is_drift(self):
        """Comparing cron only when either side has one is not the same as not comparing it."""
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "pr-merged-close-loop")["cron_expression"] = "0 6 * * *"
        drifted = {action.name: action for action in setup.trigger_plan(snapshot).update}
        assert "cron" in drifted["pr-merged-close-loop"].fields

    def test_a_cron_routine_that_lost_its_cron_is_drift(self):
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "digest")["cron_expression"] = ""
        drifted = {action.name: action for action in setup.trigger_plan(snapshot).update}
        assert "cron" in drifted["digest"].fields

    def test_the_plan_is_json_serialisable(self):
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "retro-sweep")["cron_expression"] = "0 0 * * 0"
        payload = json.loads(json.dumps(setup.trigger_plan(snapshot).as_dict()))
        assert payload["update"][0]["fields"]["cron"]["wanted"] == "30 7 5,19 * *"

    def test_a_bare_array_snapshot_is_accepted(self):
        """Captured either way depending on how /cowork saved the response."""
        entries = _perfect_snapshot()
        assert setup.snapshot({"data": entries}) == setup.snapshot(entries) == entries


class TestPagedSnapshot:
    """Reading a fleet that no longer fits in one `RemoteTrigger list` response.

    The account pages at twenty and the tool exposes no cursor, so the whole
    fleet stopped being readable in one call the moment it crossed that line —
    which is a deploy that cannot plan, and a `cd-deploy` that silently cannot
    reconcile. The fleet is read in parts instead: the newest page, plus a `get`
    for every trigger id cowork/README.md records.

    Everything here is about what such a read may and may not conclude. An
    update is safe from it; a create is the one that cannot be taken back.
    """

    GET_FIXTURE = ROOT / "tests" / "fixtures" / "cowork_trigger_get_live.json"

    def _parts(self, entries: list[dict], page: int = 2) -> tuple[dict, list[dict]]:
        """A truncated page, and the rest as the per-id reads that recover them."""
        return {"data": entries[:page], "has_more": True}, [{"trigger": e} for e in entries[page:]]

    def test_the_parts_are_joined_into_one_fleet(self):
        entries = _perfect_snapshot()
        page, gets = self._parts(entries)
        snap = setup.read_parts([page, gets], recorded=[e["id"] for e in entries])
        assert len(snap.triggers) == len(entries)
        assert setup.trigger_plan(snap).clean

    def test_overlapping_parts_are_not_two_routines(self):
        """The page and the per-id reads both carry the newest routines, by design."""
        entries = _perfect_snapshot()
        page, gets = self._parts(entries, page=len(entries))
        snap = setup.read_parts([page, *gets, page], recorded=[e["id"] for e in entries])
        assert len(snap.triggers) == len(entries)

    def test_a_read_that_saw_the_last_page_is_not_partial(self):
        """`has_more: false` is the only thing that can close the question."""
        entries = _perfect_snapshot()
        first = {"data": entries[:3], "has_more": True}
        last = {"data": entries[3:], "has_more": False}
        assert setup.read_parts([first, last]).partial is None

    def test_a_get_alone_never_proves_completeness(self):
        """It answers for one routine and says nothing about how many there are."""
        entries = _perfect_snapshot()
        page, gets = self._parts(entries)
        assert setup.read_parts([page, gets]).boundary is True

    def test_no_unresolved_ids_is_not_a_clean_ledger_when_none_was_read(self):
        """Two different statements, and only one of them is worth making."""
        entries = _perfect_snapshot()
        page, gets = self._parts(entries)
        assert "not consulted" in setup.read_parts([page, gets]).partial
        checked = setup.read_parts([page, gets], recorded=[e["id"] for e in entries])
        assert "every routine id the README records was read back" in checked.partial

    def test_one_truncated_file_still_refuses(self, tmp_path):
        """Unchanged where it matters: a short page alone is the original hazard."""
        path = tmp_path / "page.json"
        path.write_text(json.dumps({"data": _perfect_snapshot()[:4], "has_more": True}))
        with pytest.raises(ValueError, match="has_more"):
            setup.load_snapshot(path)

    def test_wrapping_a_truncated_page_in_an_array_does_not_smuggle_it_past(self):
        """The refusal reads the parts, not the top level.

        An array of envelopes is a documented way to save these, so a check that
        only looked at `payload["has_more"]` would wave a short page through the
        moment somebody wrapped it in a list — and every routine past the
        boundary would then read as one to register.
        """
        page = {"data": _perfect_snapshot()[:4], "has_more": True}
        with pytest.raises(ValueError, match="has_more"):
            setup.snapshot([page])

    def test_per_id_reads_with_no_page_beside_them_are_checked_against_the_ledger(self):
        """The signal that says "there is more" can simply be absent.

        A caller that saves the `get` envelopes and forgets the page file hands
        over something that declares nothing at all. Without the ledger it reads
        as an account containing four routines, and everything else in the table
        becomes a create.
        """
        entries = _perfect_snapshot()
        gets = [{"trigger": e} for e in entries[:4]]
        snap = setup.read_parts([gets], recorded=[e["id"] for e in entries])
        assert snap.boundary is False, "nothing here said there was more — that is the point"
        assert snap.partial and "were not read back" in snap.partial
        assert setup.trigger_plan(snap).postable_creates == []

    def test_a_hand_saved_array_is_still_the_whole_account(self):
        """The older convention, and it must not become partial by accident.

        A bare array is how a `list` response gets saved by hand; only the
        `{"trigger": …}` envelope marks a fleet read one routine at a time.
        """
        entries = _perfect_snapshot()
        snap = setup.read_parts([entries], recorded=["trig_stale"])
        assert snap.partial is None
        assert setup.trigger_plan(snap).clean

    def test_updates_still_flow_from_a_partial_read(self):
        """The whole point: drift on a routine you can see is still drift."""
        entries = _perfect_snapshot()
        _by_name(entries, "digest")["cron_expression"] = "0 0 * * 0"
        page, gets = self._parts(entries)
        plan = setup.trigger_plan(setup.read_parts([page, gets], recorded=[e["id"] for e in entries]))
        assert [action.name for action in plan.update] == ["digest"]
        assert plan.update[0].body["cron_expression"] == "15 8 * * *"

    def test_an_unread_recorded_id_blocks_every_create(self):
        """A routine the ledger records and no part read back is one that exists."""
        entries = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        page, gets = self._parts(entries)
        snap = setup.read_parts([page, gets], recorded=[*[e["id"] for e in entries], "trig_unread"])
        plan = setup.trigger_plan(snap)
        assert plan.creates_blocked == ["digest"]
        assert plan.postable_creates == []
        assert plan.create[0].body == {}, "a blocked action must carry nothing postable"
        assert "trig_unread" in plan.partial

    def test_a_routine_the_readme_records_is_never_created_from_a_partial_read(self):
        """The dangerous case: it is registered, and the page boundary hid it."""
        entries = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        page, gets = self._parts(entries)
        snap = setup.read_parts([page, gets], recorded=[e["id"] for e in entries])
        plan = setup.trigger_plan(snap)
        assert plan.creates_blocked == ["digest"], "cowork/README.md records a URL for digest"
        assert plan.postable_creates == []

    def test_a_routine_the_readme_does_not_record_is_still_creatable(self, monkeypatch):
        """Nothing of ours can hide past the boundary under a name no deploy used.

        Without this the first deploy after a routine is added would refuse it
        for as long as the fleet stays over one page — which is forever.
        """
        entries = _perfect_snapshot()
        fresh = next(r for r in ROUTINES if r.name == "digest")
        monkeypatch.setattr(setup, "unregistered_routines", lambda *a, **k: frozenset({fresh.path}))
        page, gets = self._parts([e for e in entries if e["name"] != fresh.trigger_name])
        snap = setup.read_parts([page, gets], recorded=[e["id"] for e in entries if e["name"] != fresh.trigger_name])
        plan = setup.trigger_plan(snap)
        assert [action.name for action in plan.postable_creates] == ["digest"]
        assert plan.create[0].body["name"] == "cowork: digest"

    def test_the_plan_reports_the_partial_read(self):
        entries = _perfect_snapshot()
        page, gets = self._parts(entries)
        payload = json.loads(json.dumps(setup.trigger_plan(setup.read_parts([page, gets])).as_dict()))
        assert payload["partial"], "a plan that cannot see the whole fleet must say so"
        assert setup.trigger_plan(entries).as_dict()["partial"] is None

    def test_an_unread_routine_is_a_note_and_not_a_failure(self, tmp_path):
        """A doctor that calls a healthy fleet broken is one nobody re-runs."""
        entries = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        page, gets = self._parts(entries)
        paths = []
        for index, part in enumerate([page, gets]):
            path = tmp_path / f"part{index}.json"
            path.write_text(json.dumps(part))
            paths.append(path)
        report = setup.Report()
        setup.check_triggers(report, paths)
        assert not [p for p in report.problems if "cowork: digest" in p]
        assert any("cowork: digest" in n and "unknown" in n for n in report.notes)
        assert any("partial read" in n for n in report.notes)

    def test_the_readme_ledger_is_read_off_the_url_column(self):
        ids = setup.recorded_ids()
        assert ids and all(i.startswith("trig_") for i in ids)
        assert len(set(ids)) == len(ids), "two rows claiming one id is drift, not a ledger"

    def test_an_em_dash_is_not_an_id(self):
        """The table's own mark for a row that is written down but not running."""
        table = "| `cron/ghost.md` | daily | ghost | fast | — |\n"
        assert setup.unregistered_routines(table) == frozenset({"cron/ghost.md"})
        live = "| `cron/ghost.md` | daily | ghost | fast | https://claude.ai/code/routines/trig_01 |\n"
        assert setup.unregistered_routines(live) == frozenset()

    def test_the_real_get_envelope_is_understood(self):
        """Read against a real API response, not one this module generated."""
        payload = json.loads(self.GET_FIXTURE.read_text().replace(FIXTURE_MODEL, TIERS["standard"].model_id))
        snap = setup.read_parts([payload])
        assert len(snap.triggers) == 1
        assert setup.observed_trigger(snap.triggers[0])["trigger_name"] == "cowork: standup-sweep"
        assert snap.boundary is False, "a get on its own declares nothing about the rest"

    def test_the_get_fixture_names_no_model(self):
        """Same contract as cowork/: models.md is the only place an id is written."""
        assert not re.search(r"claude-(?:opus|sonnet|haiku|fable)-[\w.-]*\d", self.GET_FIXTURE.read_text())


class TestToolOverrides:
    """slack-relay and cd-deploy are the only routines registered with RemoteTrigger.

    The relay drives pause/resume/run from inside its own session and the deployer
    reconciles the fleet, so both need the tool; a *sweep* that could reach the
    routines API would be a sweep that can un-pause the fleet, so the override is
    per-routine and the plan treats any deviation — in either direction — as drift.
    """

    def _tools_of(self, body: dict) -> list[str]:
        return body["job_config"]["ccr"]["session_context"]["allowed_tools"]

    def test_the_relay_registers_with_remote_trigger(self):
        relay = next(r for r in ROUTINES if r.name == "slack-relay")
        body = setup.desired_trigger(relay, REPO_URL, ENVIRONMENT, CONNECTORS)
        assert "RemoteTrigger" in self._tools_of(body)

    def test_exactly_two_routines_reach_the_routines_api(self):
        """The relay carries a human's pause/resume/run; the deployer reconciles the fleet.

        Nothing else: a sweep that can reach the routines API is a sweep that can
        un-pause the fleet. Asserted as an exact set rather than a loop with an
        exception, so widening it is a reviewed edit here and not a quiet one in
        TOOL_OVERRIDES.
        """
        holders = {r.name for r in ROUTINES if "RemoteTrigger" in setup.routine_tools(r.name)}
        assert holders == {"slack-relay", "cd-deploy"}

    def test_no_routine_registers_with_remote_trigger_it_should_not_have(self):
        for routine in ROUTINES:
            if routine.name in {"slack-relay", "cd-deploy"}:
                continue
            body = setup.desired_trigger(routine, REPO_URL, ENVIRONMENT, CONNECTORS)
            assert "RemoteTrigger" not in self._tools_of(body), (
                f"{routine.path} would register with RemoteTrigger — only the relay and deployer carry it"
            )

    def test_nothing_holds_the_routines_api_and_a_write_tool(self):
        """RemoteTrigger plus Write/Edit is one routine that can rewrite the repo *and*
        reprogram the fleet from what it wrote. Neither holder needs both: the relay
        edits nothing, and the deployer's only file write is the README URL column,
        done inside cowork_setup.py under Bash."""
        for routine in ROUTINES:
            tools = set(setup.routine_tools(routine.name))
            assert not ("RemoteTrigger" in tools and tools & {"Write", "Edit"}), (
                f"{routine.path} holds the routines API and a write tool"
            )

    def test_nothing_that_reads_a_fork_can_edit_a_file(self):
        """The two PR routines read text nobody in this repo wrote.

        `gh pr view` and `gh pr diff` return a title, a body and a diff authored by
        whoever opened the PR — on a public repo, anyone with a fork — and both
        routines hold the Linear, Slack and Notion connectors. Neither writes
        anything itself; `cowork-scribe` does. This is the grant that says so.
        """
        for name in ("pr-opened-dod-audit", "pr-merged-close-loop"):
            tools = set(setup.routine_tools(name))
            assert not tools & {"Write", "Edit"}, f"{name} can edit a file while reading a fork's diff"
            assert "Task" in tools, f"{name} spawns the scribe, which does its writing"

    def test_no_event_routine_writes_for_itself(self):
        """All three delegate every outbound word to the scribe. None needs an editor."""
        for routine in ROUTINES:
            if routine.kind == "event":
                assert not set(setup.routine_tools(routine.name)) & {"Write", "Edit"}

    def test_no_grant_is_ever_empty(self):
        """An empty allowed_tools registers as the full default preset — see
        tests/fixtures/cowork_webhook_live.json. The narrowest-looking grant would
        be the widest routine in the fleet."""
        for routine in ROUTINES:
            assert setup.routine_tools(routine.name)

    def test_a_live_relay_missing_remote_trigger_is_drift(self):
        """The failure a stale deploy leaves behind: a relay that cannot pause anything."""
        snapshot = _perfect_snapshot()
        context = _by_name(snapshot, "slack-relay")["job_config"]["ccr"]["session_context"]
        context["allowed_tools"] = [tool for tool in context["allowed_tools"] if tool != "RemoteTrigger"]
        plan = setup.trigger_plan(snapshot)
        assert [action.name for action in plan.update] == ["slack-relay"]
        assert "allowed_tools" in plan.update[0].fields

    def test_a_sweep_granted_remote_trigger_is_drift(self):
        snapshot = _perfect_snapshot()
        context = _by_name(snapshot, "retro-sweep")["job_config"]["ccr"]["session_context"]
        context["allowed_tools"] = [*context["allowed_tools"], "RemoteTrigger"]
        plan = setup.trigger_plan(snapshot)
        assert [action.name for action in plan.update] == ["retro-sweep"]
        assert "allowed_tools" in plan.update[0].fields

    def test_the_relay_is_narrowed_not_just_widened(self):
        """The one routine reading attacker-influenceable text every hour gets no
        Write/Edit, and spawns no crew."""
        tools = setup.routine_tools("slack-relay")
        assert not {"Write", "Edit", "Task"} & set(tools)

    def test_the_relays_shell_is_scoped_to_the_verbs_it_relays(self):
        """Issue #172: relaying one ✅ replaced the issue's entire label set.

        `gh issue edit --add-label` adds; `gh api -X PUT .../labels` replaces. The
        routine had always specified the first, and prose was the only thing
        enforcing it — so #172 lost `cowork:proposal`, `workstream:web-ux` and
        `type:security` in the second it gained `claude-implement`. The workstream
        label is what `claude.yml`'s implement job reads to find the charter
        declaring which paths an unattended 110-turn run may touch, so this is a
        boundary rather than bookkeeping. Bare Bash puts it back within reach.
        """
        tools = set(setup.routine_tools("slack-relay"))
        assert "Bash" not in tools, "a bare shell can spell the label-replacing call the relay must not make"
        shells = {tool for tool in tools if tool.startswith("Bash(")}
        assert shells, "the relay still needs a shell — scoped, not absent"
        assert not any("gh api" in shell for shell in shells), "gh api is how a label set gets replaced"

    def test_the_relay_can_reach_the_helper_that_decides_for_it(self):
        """The decision moved into `scripts/cowork_relay.py`; a grant that cannot
        run it would send the relay straight back to judging the thread by eye."""
        tools = set(setup.routine_tools("slack-relay"))
        assert any("cowork_relay.py" in tool for tool in tools)

    def test_the_doctor_fails_on_an_unscoped_relay(self, monkeypatch):
        """The invariant above, through check_grants' own failure path."""
        monkeypatch.setitem(setup.TOOL_OVERRIDES, "slack-relay", ("Bash", "Read", "RemoteTrigger"))
        report = setup.Report()
        setup.check_grants(report, ROUTINES)
        assert any("unscoped Bash" in problem for problem in report.problems)

    def test_the_deployer_keeps_a_bare_shell(self):
        """The narrowing is the relay's alone. `cd-deploy` runs git and `gh pr
        create` against a plan Python composed; enumerating that is a list that
        would rot, and it runs once a merge rather than seventeen times a day on
        text it did not write."""
        assert "Bash" in set(setup.routine_tools("cd-deploy"))

    def test_the_daily_poster_cannot_write(self):
        """`cron/shipped-standup.md` states this as a safety property — it reads a
        day of merged PRs and posts one message — so it is pinned rather than left
        as prose. Task stays: unlike the relay, it spawns the scribe."""
        tools = setup.routine_tools("shipped-standup")
        assert not {"Write", "Edit"} & set(tools)
        assert "Task" in tools and "Bash" in tools

    def test_the_promotion_ask_cannot_answer_itself(self):
        """The routine that asks whether to release must not be able to approve it.

        `publish.yml` fires on `release:promote`, which is applied by the relay
        carrying a human's ✅. A grant of `gh issue edit` here would let the ask
        label its own issue and cut a release nobody approved — the same shape as
        a sweep applying `claude-implement` to its own proposal.
        """
        tools = setup.routine_tools("release-promote-ask")
        assert not {"Write", "Edit"} & set(tools)
        assert not any("gh issue edit" in tool for tool in tools)
        assert "Bash" not in tools, "a bare Bash grant would include `gh issue edit`"

    def test_every_override_names_a_real_routine(self):
        """A renamed relay would otherwise silently lose its extra tools."""
        stems = {routine.name for routine in ROUTINES}
        assert set(setup.TOOL_OVERRIDES) <= stems, (
            f"TOOL_OVERRIDES names routines that do not exist: {sorted(set(setup.TOOL_OVERRIDES) - stems)}"
        )

    def test_the_doctor_fails_on_a_stale_override_key(self, monkeypatch):
        """The invariant above, exercised through check_repo's own failure path —
        deleting the doctor branch must not leave the suite green."""
        monkeypatch.setitem(setup.TOOL_OVERRIDES, "not-a-routine", ())
        report = setup.Report()
        setup.check_repo(report)
        assert any("not-a-routine" in problem for problem in report.problems)

    def test_the_manifest_carries_the_per_routine_tools(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        by_name = {routine["name"]: routine for routine in setup.manifest()["routines"]}
        assert "RemoteTrigger" in by_name["slack-relay"]["allowed_tools"]
        assert "RemoteTrigger" not in by_name["retro-sweep"]["allowed_tools"]


class TestUrlWriteback:
    """The step that got skipped when it was sixteen hand-edits."""

    @pytest.fixture
    def blank_readme(self) -> str:
        """The README with its URL column emptied, as it ships before a deploy."""
        text = setup.README.read_text()
        # Four cells (routine's own closing pipe, trigger, workstream, tier), then
        # the fifth — the URL — is the one emptied.
        return re.sub(r"(^\| `(?:cron|events)/[a-z0-9-]+\.md`(?:[^|\n]*\|){4})[^|\n]*\|", r"\1 |", text, flags=re.M)

    def test_the_fixture_really_is_blank(self, blank_readme: str):
        assert len(setup.missing_urls(blank_readme)) == len(ROUTINES)

    def test_every_cron_row_gets_its_url(self, blank_readme: str):
        plan = setup.trigger_plan(_perfect_snapshot())
        filled = setup.readme_with_urls(blank_readme, plan.urls)
        assert setup.missing_urls(filled) == []
        assert f"https://claude.ai/code/routines/{_trigger_id(0)} |" in filled

    def test_the_event_rows_get_their_url_too(self, blank_readme: str):
        """They are registered routines now, so the table records them like any other.

        This row used to be asserted blank, because an event routine could only be
        made by hand in a web form and the table had no id to record.
        """
        filled = setup.readme_with_urls(blank_readme, setup.trigger_plan(_perfect_snapshot()).urls)
        rows = [line for line in filled.splitlines() if line.startswith("| `events/")]
        assert rows, "the fixture lost its event rows"
        for line in rows:
            assert "https://claude.ai/code/routines/" in line

    def test_it_is_idempotent(self, blank_readme: str):
        urls = setup.trigger_plan(_perfect_snapshot()).urls
        once = setup.readme_with_urls(blank_readme, urls)
        assert setup.readme_with_urls(once, urls) == once

    def test_a_stale_url_is_replaced(self, blank_readme: str):
        urls = setup.trigger_plan(_perfect_snapshot()).urls
        stale = setup.readme_with_urls(blank_readme, dict.fromkeys(urls, "https://claude.ai/code/routines/trig_old"))
        assert setup.readme_with_urls(stale, urls) == setup.readme_with_urls(blank_readme, urls)

    def test_nothing_but_the_url_cell_moves(self, blank_readme: str):
        filled = setup.readme_with_urls(blank_readme, setup.trigger_plan(_perfect_snapshot()).urls)
        assert len(filled.splitlines()) == len(blank_readme.splitlines())
        stripped = re.sub(r"https://claude\.ai/code/routines/\S+ ", "", filled)
        assert stripped == blank_readme

    def test_a_row_added_but_not_yet_deployed_does_not_break_the_suite(self):
        """The blank-URL check belongs to the doctor, not to `make test`.

        `cowork/README.md`'s own procedure has you add the table row and *then*
        run `/cowork deploy`, so a suite that asserted every row carries a URL
        would be red in between — and permanently red for a contributor with no
        access to the account, since the URLs are real trigger ids. It is checked
        in `check_triggers()` instead, where there is a snapshot to check against.
        """
        blank = re.sub(
            r"(^\| `cron/digest\.md`(?:[^|\n]*\|){4})[^|\n]*\|", r"\1 |", setup.README.read_text(), flags=re.M
        )
        # Relative to the real README's own blanks: the file may itself carry
        # rows in exactly this added-but-not-deployed state, and that is the
        # point — but blanking digest must add digest and nothing else.
        assert set(setup.missing_urls(blank)) - set(setup.missing_urls()) == {"cron/digest.md"}


class TestTeardown:
    def test_the_gate_labels_are_never_deleted(self):
        """Neither belongs to cowork, and both gate something that outlives it.

        ``claude-implement`` predates cowork and gates the claude.yml implement
        job; ``feedback-override`` is the escape hatch on the pr-feedback merge
        gate; the ``release:*`` pair is what ``publish.yml`` fires on, so deleting
        either disarms the only path that cuts an official release; and
        ``integration:approved`` is what the campaign routine reads to know which
        provider it is building, so deleting it strands every ✅ on a shortlist;
        and ``implement-blocked`` is the terminal state that stops a level-triggered
        reconciler re-firing a broken implement job every six hours, so deleting it
        both strips that record off every issue and restarts the loop.
        Removing any of them with the fleet would break a live gate, and the
        breakage is silent: applying a label that does not exist does nothing.

        ``fleet-ledger`` is the one entry here that gates nothing. It is kept for
        the opposite reason: teardown stops the fleet, and deleting this label
        would leave every monthly run ledger intact and unfindable — the history
        surviving with nothing able to read it.
        """
        assert setup.KEEP_LABELS == {
            "claude-implement",
            "implement-blocked",
            "feedback-override",
            "release:promotion",
            "release:promote",
            "integration:approved",
            "fleet-ledger",
        }
        assert not (setup.KEEP_LABELS & {label.name for label in setup.teardown_labels()})

    def test_everything_else_cowork_creates_is_in_scope(self):
        expected = {
            label.name
            for label in setup.expected_labels()
            if label.name not in setup.KEEP_LABELS and not label.name.startswith("type:")
        }
        assert {label.name for label in setup.teardown_labels()} == expected
        # cowork, cowork:proposal, cowork:queued, review-capped,
        # integration:candidate — the five non-workstream, non-type labels cowork
        # creates and may therefore also remove. `integration:approved` is not
        # among them: it is a live gate, so it sits in KEEP_LABELS beside the
        # `release:*` pair for the same reason.
        #
        # `cowork:queued` is deletable for the same reason `cowork:proposal` is:
        # it records which lane owns a find, and a torn-down fleet owns nothing.
        assert len(expected) == len(WORKSTREAMS) + 5

    def test_it_refuses_without_yes(self):
        result = subprocess.run(
            [sys.executable, str(_MODULE_PATH), "--teardown", "--labels"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2 and "--yes" in result.stderr

    def test_selecting_nothing_is_refused_rather_than_silently_doing_nothing(self):
        assert setup.apply_teardown(labels=False, variables=False) == 1


class TestGhWrites:
    """The half of the script that mutates anything.

    ``_gh`` is the seam the CLI half goes through, so all of this is reachable
    with one monkeypatch. It is no longer the *only* seam — `_no_live_github`
    above keeps the REST one shut for this class, and the fixture below pins the
    transport so these assertions stay assertions about `gh`.
    """

    @pytest.fixture
    def gh(self, monkeypatch):
        """Record every gh invocation; reply from a per-test script."""
        calls: list[tuple[str, ...]] = []
        replies: dict[tuple[str, ...], tuple[int, str]] = {}

        def fake(*args: str):
            calls.append(args)
            code, out = replies.get(args[:2], (0, ""))
            return subprocess.CompletedProcess(args, code, out, "boom" if code else "")

        monkeypatch.setattr(setup, "_gh", fake)
        # `apply_teardown()` picks a transport before it writes anything, and
        # `gh_ready()` would otherwise shell a real `gh auth status` here — or,
        # on a machine without `gh`, hand these tests to the REST transport.
        monkeypatch.setattr(setup, "gh_ready", lambda: True)
        return type("Gh", (), {"calls": calls, "replies": replies})()

    def _label_list(self, names: list[str]) -> tuple[int, str]:
        return 0, json.dumps([{"name": name} for name in names])

    def _variable_list(self, values: dict[str, str]) -> tuple[int, str]:
        return 0, json.dumps([{"name": k, "value": v} for k, v in values.items()])

    def test_only_missing_labels_are_created(self, gh, capsys):
        gh.replies[("label", "list")] = self._label_list(["cowork", "claude-implement"])
        setup.apply_labels()
        created = [args[2] for args in gh.calls if args[:2] == ("label", "create")]
        assert "cowork" not in created and "claude-implement" not in created
        assert "workstream:security" in created
        assert len(created) == len(setup.expected_labels()) - 2

    def test_an_existing_label_is_never_overwritten(self, gh):
        """Deliberately not `--force`.

        A colour or description someone changed on purpose is not drift worth
        correcting, and clobbering it would make a second run of `make
        cowork-setup` destructive for no benefit.
        """
        gh.replies[("label", "list")] = self._label_list([label.name for label in setup.expected_labels()])
        setup.apply_labels()
        assert [args for args in gh.calls if args[:2] == ("label", "create")] == []
        assert not any("--force" in args for args in gh.calls)

    def test_a_failed_label_query_writes_nothing(self, gh):
        """`gh_ready()` passing does not mean the next call succeeds."""
        gh.replies[("label", "list")] = (1, "")
        setup.apply_labels()
        assert [args for args in gh.calls if args[:2] == ("label", "create")] == []

    def test_variables_are_set_from_the_tier_table(self, gh):
        gh.replies[("variable", "list")] = self._variable_list({})
        setup.apply_variables()
        written = {args[2]: args[4] for args in gh.calls if args[:2] == ("variable", "set")}
        assert written == setup.parse_model_variables()

    def test_a_variable_already_correct_is_left_alone(self, gh):
        gh.replies[("variable", "list")] = self._variable_list(setup.parse_model_variables())
        setup.apply_variables()
        assert [args for args in gh.calls if args[:2] == ("variable", "set")] == []

    def test_a_variable_holding_the_wrong_model_is_rewritten(self, gh):
        wanted = setup.parse_model_variables()
        stale = dict.fromkeys(wanted, "some-old-model")
        gh.replies[("variable", "list")] = self._variable_list(stale)
        setup.apply_variables()
        written = {args[2] for args in gh.calls if args[:2] == ("variable", "set")}
        assert written == set(wanted)

    def test_teardown_deletes_the_labels_but_never_claude_implement(self, gh):
        gh.replies[("label", "list")] = self._label_list([label.name for label in setup.expected_labels()])
        gh.replies[("variable", "list")] = self._variable_list(setup.parse_model_variables())
        monkeypatched = setup.apply_teardown(labels=True, variables=True)
        deleted = {args[2] for args in gh.calls if args[:2] == ("label", "delete")}
        assert monkeypatched == 0
        assert not (setup.KEEP_LABELS & deleted)
        assert deleted == {label.name for label in setup.teardown_labels()}

    def test_teardown_unsets_every_model_variable(self, gh):
        gh.replies[("variable", "list")] = self._variable_list(setup.parse_model_variables())
        setup.apply_teardown(labels=False, variables=True)
        unset = {args[2] for args in gh.calls if args[:2] == ("variable", "delete")}
        assert unset == set(setup.parse_model_variables())

    def test_teardown_deletes_nothing_it_was_not_asked_to(self, gh):
        gh.replies[("label", "list")] = self._label_list([label.name for label in setup.expected_labels()])
        setup.apply_teardown(labels=True, variables=False)
        assert [args for args in gh.calls if args[:2] == ("variable", "delete")] == []

    def test_teardown_stops_when_the_label_query_fails(self, gh):
        gh.replies[("label", "list")] = (1, "")
        assert setup.apply_teardown(labels=True, variables=False) == 1
        assert [args for args in gh.calls if args[:2] == ("label", "delete")] == []

    def test_a_failed_query_is_not_an_empty_repo(self, gh):
        """None and empty are different facts, and the difference is 22 findings."""
        gh.replies[("label", "list")] = (1, "")
        gh.replies[("variable", "list")] = (1, "")
        assert setup.existing_labels() is None
        assert setup.existing_variables() is None

    def test_apply_urls_writes_the_readme_and_is_idempotent(self, tmp_path: Path, monkeypatch):
        readme = tmp_path / "README.md"
        readme.write_text(
            re.sub(r"(^\| `cron/[a-z-]+\.md`(?:[^|\n]*\|){4})[^|\n]*\|", r"\1 |", setup.README.read_text(), flags=re.M)
        )
        monkeypatch.setattr(setup, "README", readme)
        path = tmp_path / "live.json"
        path.write_text(json.dumps({"data": _perfect_snapshot()}))

        assert setup.apply_urls(path) == 0
        assert setup.missing_urls(readme.read_text()) == []
        once = readme.read_text()
        assert setup.apply_urls(path) == 0 and readme.read_text() == once


class TestApiTransport:
    """The half that runs where `gh` does not.

    `cron/cd-deploy.md` executes this script from a cloud routine session, and
    that session has a GitHub token but no CLI. Every firing therefore took
    `gh_ready()`'s "not on PATH" branch, exited 1 under ``--strict``, and the
    routine's stop condition halted it before reconciling the fleet — the one
    thing it exists to do. Nothing here was covered by a test, which is why it
    ran that way for as long as it did.

    ``_api`` is the seam, the way ``_gh`` is for the CLI half. Nothing below
    opens a socket.
    """

    @pytest.fixture
    def api(self, monkeypatch):
        """Record every REST call; reply from a per-test script keyed on
        ``(method, path)`` with the query string stripped."""
        calls: list[tuple[str, str, dict | None]] = []
        replies: dict[tuple[str, str], object] = {}

        def fake(method: str, path: str, body: dict | None = None):
            calls.append((method, path, body))
            key = (method, path.split("?")[0])
            if key in replies:
                answer = replies[key]
                if isinstance(answer, setup.ApiResult):
                    return answer
                return setup.ApiResult(True, answer)
            return setup.ApiResult(True, [])

        monkeypatch.setattr(setup.transport, "api", fake)
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setenv("GH_TOKEN", "t")
        # This fixture *is* "the REST transport is the one that answered", and
        # `github_ready()` re-resolves that by shelling out to `gh auth status`.
        # Unstubbed it made a live call on every teardown test — invisible until
        # `_no_real_gh_calls` started refusing.
        monkeypatch.setattr(setup, "github_ready", lambda: True)
        return type("Api", (), {"calls": calls, "replies": replies})()

    # --- token resolution ----------------------------------------------------

    def test_gh_token_wins_over_github_token(self, monkeypatch):
        """`gh`'s own precedence, so a machine setting both gets one identity."""
        monkeypatch.setenv("GITHUB_TOKEN", "second")
        assert setup.github_token() == "second"
        monkeypatch.setenv("GH_TOKEN", "first")
        assert setup.github_token() == "first"

    def test_no_token_is_none_not_empty(self):
        assert setup.github_token() is None

    # --- transport selection -------------------------------------------------

    def test_gh_is_preferred_when_it_is_there(self, monkeypatch):
        """Local behaviour must not change: an authenticated CLI still wins."""
        monkeypatch.setattr(setup, "gh_ready", lambda: True)
        monkeypatch.setenv("GH_TOKEN", "t")
        assert setup.github_ready() is True
        assert setup.TRANSPORT == "gh"

    def test_rest_takes_over_when_gh_is_absent(self, monkeypatch, capsys):
        monkeypatch.setattr(setup, "gh_ready", lambda: False)
        monkeypatch.setattr(setup.shutil, "which", lambda name: None)
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setenv("GH_TOKEN", "t")
        assert setup.github_ready() is True
        assert setup.TRANSPORT == "api"
        out = capsys.readouterr().out
        assert "REST API" in out and "is not on PATH" in out

    def test_rest_takes_over_when_gh_is_merely_unauthenticated(self, monkeypatch, capsys):
        """The other half of the same fallback, said accurately: an installed but
        logged-out `gh` is a different problem from a missing one, and printing
        the wrong one sends the reader to the wrong remedy."""
        monkeypatch.setattr(setup, "gh_ready", lambda: False)
        monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setenv("GH_TOKEN", "t")
        assert setup.github_ready() is True
        assert setup.TRANSPORT == "api"
        assert "is not authenticated" in capsys.readouterr().out

    def test_neither_transport_degrades_exactly_once(self, monkeypatch, capsys):
        """The production failure, and the case nothing asserted before."""
        monkeypatch.setattr(setup, "gh_ready", lambda: False)
        monkeypatch.setattr(setup.shutil, "which", lambda name: None)
        setup.STRICT.degraded.clear()
        assert setup.github_ready() is False
        assert len(setup.STRICT.degraded) == 1
        out = capsys.readouterr().out
        assert "GH_TOKEN" in out, "the remedy must name the fallback, not just `brew install gh`"

    def test_a_token_with_no_repo_is_not_ready(self, monkeypatch):
        """Both halves or neither — a token pointed at nothing cannot be used."""
        monkeypatch.setenv("GH_TOKEN", "t")
        monkeypatch.setattr(setup, "repo_slug", lambda: None)
        assert setup.api_ready() is False

    # --- reads ---------------------------------------------------------------

    def test_labels_are_read_from_the_rest_endpoint(self, api):
        api.replies[("GET", "/repos/o/r/labels")] = [{"name": "cowork"}, {"name": "type:bug"}]
        assert setup.existing_labels() == {"cowork", "type:bug"}
        assert api.calls[0][0] == "GET"
        assert api.calls[0][1].startswith("/repos/o/r/labels?per_page=100")

    def test_variables_are_unwrapped_from_their_envelope(self, api):
        """`/actions/variables` returns an object around the list; `/labels` does
        not. Reading the wrong shape would look like an empty repo."""
        api.replies[("GET", "/repos/o/r/actions/variables")] = {
            "total_count": 1,
            "variables": [{"name": "YEABOI_MODEL_HEAVY", "value": "x"}],
        }
        assert setup.existing_variables() == {"YEABOI_MODEL_HEAVY": "x"}

    def test_a_failed_query_is_none_not_an_empty_repo(self, api):
        """The invariant `TestGhWrites` pins for the CLI half, on this one too:
        None and empty are different facts, and the difference is 29 findings."""
        api.replies[("GET", "/repos/o/r/labels")] = setup.ApiResult(False, error="HTTP 403")
        api.replies[("GET", "/repos/o/r/actions/variables")] = setup.ApiResult(False, error="HTTP 403")
        assert setup.existing_labels() is None
        assert setup.existing_variables() is None

    def test_no_slug_is_a_degradation_not_a_crash(self, api, monkeypatch):
        monkeypatch.setattr(setup, "repo_slug", lambda: None)
        setup.STRICT.degraded.clear()
        assert setup.existing_labels() is None
        assert setup.STRICT.degraded

    # --- writes --------------------------------------------------------------

    def test_only_missing_labels_are_posted(self, api):
        api.replies[("GET", "/repos/o/r/labels")] = [{"name": label.name} for label in setup.expected_labels()][:2]
        setup.apply_labels()
        posted = [body["name"] for method, path, body in api.calls if method == "POST" and path == "/repos/o/r/labels"]
        assert len(posted) == len(setup.expected_labels()) - 2
        assert "workstream:security" in posted

    def test_a_posted_label_carries_bare_hex(self, api):
        """REST rejects a leading `#`. `expected_labels()` already spells them
        bare, which is what `gh label create --color` wanted too — asserted
        rather than assumed, because nothing else would catch it changing."""
        setup.apply_labels()
        colors = [body["color"] for method, path, body in api.calls if method == "POST"]
        assert colors and all(re.fullmatch(r"[0-9a-fA-F]{6}", color) for color in colors)

    def test_a_new_variable_is_posted_to_the_collection(self, api):
        api.replies[("GET", "/repos/o/r/actions/variables")] = {"variables": []}
        setup.apply_variables()
        writes = [(method, path, body) for method, path, body in api.calls if method in {"POST", "PATCH"}]
        assert writes and all(method == "POST" for method, _, _ in writes)
        assert all(path == "/repos/o/r/actions/variables" for _, path, _ in writes)

    def test_an_existing_variable_is_patched_on_its_item(self, api):
        """One `gh variable set` is two different REST calls, and using the
        collection for an update is a 409 rather than a silent no-op."""
        wanted = setup.parse_model_variables()
        name = next(iter(wanted))
        api.replies[("GET", "/repos/o/r/actions/variables")] = {
            "variables": [{"name": name, "value": "stale"}],
        }
        setup.apply_variables()
        patches = [(path, body) for method, path, body in api.calls if method == "PATCH"]
        assert (f"/repos/o/r/actions/variables/{name}", {"name": name, "value": wanted[name]}) in patches

    def test_a_rejected_write_degrades_with_the_api_reason(self, api, capsys):
        api.replies[("GET", "/repos/o/r/actions/variables")] = {"variables": []}
        api.replies[("POST", "/repos/o/r/actions/variables")] = setup.ApiResult(
            False, error="HTTP 403 on POST: Resource not accessible by integration"
        )
        setup.STRICT.degraded.clear()
        setup.apply_variables()
        assert setup.STRICT.degraded
        assert "not accessible by integration" in capsys.readouterr().out

    def test_teardown_deletes_by_item_path(self, api):
        api.replies[("GET", "/repos/o/r/labels")] = [{"name": "workstream:security"}]
        api.replies[("GET", "/repos/o/r/actions/variables")] = {"variables": []}
        setup.apply_teardown(labels=True, variables=False)
        deletes = [path for method, path, _ in api.calls if method == "DELETE"]
        assert "/repos/o/r/labels/workstream%3Asecurity" in deletes

    # --- slug resolution -----------------------------------------------------

    def test_the_env_names_the_repo_when_gh_cannot(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda name: None)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
        assert setup.repo_slug() == "owner/name"

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:owner/name.git",
            "https://github.com/owner/name.git",
            "https://github.com/owner/name",
            "ssh://git@github.com/owner/name.git",
        ],
    )
    def test_the_origin_remote_names_the_repo(self, monkeypatch, url):
        """What actually resolves it in a routine session: step 1 of
        `cron/cd-deploy.md` runs `git fetch origin main`, so a remote is there
        even though `gh` is not."""
        monkeypatch.setattr(setup.shutil, "which", lambda name: None)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.setattr(
            setup.transport, "_run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, url + "\n", "")
        )
        assert setup.repo_slug() == "owner/name"

    def test_an_unreadable_remote_is_none(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda name: None)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.setattr(setup.transport, "_run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"))
        assert setup.repo_slug() is None

    # --- the token never leaks ----------------------------------------------

    def test_the_token_is_never_printed(self, monkeypatch, capsys):
        """It is a token even when it is not a secret-shaped one, and this script
        prints everything it does."""
        monkeypatch.setattr(setup, "gh_ready", lambda: False)
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setenv("GH_TOKEN", "ghp_notarealtoken")
        monkeypatch.setattr(setup.transport, "api", lambda *a, **k: setup.ApiResult(False, error="HTTP 403"))
        setup.STRICT.degraded.clear()
        setup.github_ready()
        setup.existing_labels()
        captured = capsys.readouterr()
        assert "ghp_notarealtoken" not in captured.out + captured.err
        setup.STRICT.degraded.clear()


class TestSnapshotFlags:
    """``--plan`` and ``--urls`` are useless without a snapshot, and say so."""

    @pytest.mark.parametrize("flag", ["--plan", "--urls"])
    def test_they_refuse_without_triggers(self, flag: str):
        result = subprocess.run([sys.executable, str(_MODULE_PATH), flag], capture_output=True, text=True, check=False)
        assert result.returncode == 2 and "--triggers" in result.stderr

    def test_plan_prints_json(self, tmp_path: Path):
        path = tmp_path / "live.json"
        path.write_text(json.dumps({"data": _perfect_snapshot()}))
        result = subprocess.run(
            [sys.executable, str(_MODULE_PATH), "--plan", "--triggers", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert len(json.loads(result.stdout)["ok"]) == len(ROUTINES)

    def test_check_says_so_when_the_account_half_was_not_checked(self):
        """Silence would read as "the routines are fine", which it cannot know."""
        result = subprocess.run(
            [sys.executable, str(_MODULE_PATH), "--check", "--local"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "registered routines were not checked" in result.stdout


WEBHOOK_FIXTURE = ROOT / "tests" / "fixtures" / "cowork_webhook_live.json"


class TestWebhookDeclaration:
    """The ```json webhook block: what a routine file may say about what fires it.

    Every check here guards a failure the API will not. It accepts an unknown
    event name with a 200, stores a filter it never reads back, does not dedup and
    has no delete — so a block that is wrong produces a routine that looks
    deployed, fires never or twice, and says nothing either way. This is the only
    place that can catch it.
    """

    def test_every_event_routine_declares_one(self):
        for routine in ROUTINES:
            if routine.kind == "event":
                assert routine.webhook, f"{routine.path} would register as a routine nothing ever fires"

    def test_the_deployer_declares_one(self):
        deployer = next(r for r in ROUTINES if r.name == setup.DEPLOY_ROUTINE)
        assert deployer.webhook and deployer.webhook["events"] == ["push"]

    def test_a_sweep_declares_none(self):
        assert next(r for r in ROUTINES if r.name == "security-sweep").webhook is None

    def test_every_declared_event_is_one_the_repo_knows(self):
        for routine in ROUTINES:
            for event in (routine.webhook or {}).get("events", []):
                assert event in setup.WEBHOOK_EVENTS

    @pytest.mark.parametrize(
        "block, expected",
        [
            ('{"source": "github", "events": ["push"], "surprise": 1}', "unknown key"),
            ('{"source": "github", "events": ["push"], "routine_trigger_id": "trig_x"}', "declares"),
            ('{"source": "github", "events": ["push"], "scope_id": "github.com/a/b"}', "declares"),
            ('{"source": "gitlab", "events": ["push"]}', "webhook source"),
            ('{"source": "github", "events": []}', "no `events` list"),
            ('{"source": "github", "events": "push"}', "no `events` list"),
            ('{"source": "github", "events": ["nope"]}', "unknown event"),
            ('{"source": "github", "events": ["push"], "filter": "x"}', "not an object"),
        ],
    )
    def test_a_bad_block_is_refused(self, block: str, expected: str):
        routine = _routine_with_webhook(block)
        problems = " ".join(problem for problem, _ in setup.webhook_problems(routine))
        assert expected in problems, problems

    def test_an_oversized_filter_is_refused(self):
        big = json.dumps({"actions": ["x" * setup.WEBHOOK_FILTER_LIMIT]})
        routine = _routine_with_webhook(f'{{"source": "github", "events": ["push"], "filter": {big}}}')
        assert any("filter is over" in problem for problem, _ in setup.webhook_problems(routine))

    def test_events_must_agree_with_the_trigger_prose(self):
        routine = _routine_with_webhook('{"source": "github", "events": ["release"]}', trigger="cron `0 4 * * *`")
        assert any("**Trigger** line does not mention" in problem for problem, _ in setup.webhook_problems(routine))

    def test_a_malformed_block_is_a_finding_not_an_exception(self):
        """parse_routines() runs at import time all over this suite. A bad block has
        to arrive as a doctor finding naming the file, not a collection error."""
        webhook, error = setup.parse_webhook("```json webhook\n{not json}\n```\n")
        assert webhook is None and "not valid JSON" in error

    def test_two_blocks_are_refused(self):
        block = '```json webhook\n{"source": "github", "events": ["push"]}\n```\n'
        webhook, error = setup.parse_webhook(block * 2)
        assert webhook is None and "one event source" in error

    def test_no_block_is_not_an_error(self):
        assert setup.parse_webhook("# a sweep\n\nnothing to see\n") == (None, None)


def _routine_with_webhook(block: str, trigger: str = "GitHub event, push and pull request and release"):
    """One synthetic routine carrying a given webhook block, for the parser tests."""
    webhook, error = setup.parse_webhook(f"```json webhook\n{block}\n```\n")
    base = next(r for r in ROUTINES if r.kind == "event")
    return dataclasses.replace(base, trigger=trigger, webhook=webhook, webhook_error=error)


class TestWebhookDoctor:
    """The same checks, reached the way a contributor reaches them: `make cowork-check`."""

    def _run(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(cwd / "scripts" / "cowork_setup.py"), "--check", "--local"],
            capture_output=True,
            text=True,
            check=False,
        )

    @pytest.fixture
    def repo_copy(self, tmp_path: Path) -> Path:
        copy = tmp_path / "repo"
        (copy / "scripts").mkdir(parents=True)
        shutil.copy(_MODULE_PATH, copy / "scripts" / "cowork_setup.py")
        # The transport is a sibling import, so it has to travel with the script.
        shutil.copy(ROOT / "scripts" / "_gh_transport.py", copy / "scripts" / "_gh_transport.py")
        shutil.copytree(ROOT / "cowork", copy / "cowork")
        return copy

    def test_an_event_routine_with_no_block_fails(self, repo_copy: Path):
        path = repo_copy / "cowork" / "routines" / "events" / "release-published-announce.md"
        body = path.read_text()
        start = body.index("```json webhook")
        end = body.index("```", start + 3) + 4
        path.write_text(body[:start] + body[end:])
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "declares no ```json webhook block" in result.stderr

    def test_a_misspelled_event_fails(self, repo_copy: Path):
        path = repo_copy / "cowork" / "routines" / "events" / "release-published-announce.md"
        path.write_text(path.read_text().replace('"events": ["release"]', '"events": ["releases"]'))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "unknown event" in result.stderr

    def test_a_deployer_that_stopped_firing_on_push_fails(self, repo_copy: Path):
        path = repo_copy / "cowork" / "routines" / "cron" / "cd-deploy.md"
        path.write_text(path.read_text().replace('"events": ["push"]', '"events": ["release"]'))
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "not `push`" in result.stderr

    def test_a_routine_holding_the_api_and_a_write_tool_fails(self, repo_copy: Path):
        script = repo_copy / "scripts" / "cowork_setup.py"
        script.write_text(
            script.read_text().replace(
                '"cd-deploy": ("Bash", "Glob", "Grep", "Read", "RemoteTrigger", "Task", "TodoWrite"),',
                '"cd-deploy": ("Bash", "Glob", "Grep", "Read", "RemoteTrigger", "Task", "TodoWrite", "Write"),',
            )
        )
        result = self._run(repo_copy)
        assert result.returncode == 1
        assert "holds RemoteTrigger and Write" in result.stderr


class TestWebhookPlan:
    """Whether a webhook is safe to POST — the one question with no way to ask the API.

    Nothing reports the webhooks attached to a routine, an identical POST is not
    deduped, and there is no delete. So a duplicate is permanent and doubles every
    firing, and the only defensible rule is to post exactly once, at the moment a
    routine is created and provably holds none.
    """

    def _plan(self, snapshot, created=()):
        return setup.trigger_plan(snapshot, created=created)

    def test_a_routine_being_created_now_is_deferred(self):
        """There is no id to fire yet — re-plan after the creates."""
        plan = setup.trigger_plan([], repo_url=REPO_URL, environment_id=ENVIRONMENT, connectors=CONNECTORS)
        deferred = {a.name: a for a in plan.webhooks}
        assert deferred["pr-opened-dod-audit"].action == "deferred"
        assert deferred["pr-opened-dod-audit"].body == {}
        assert plan.postable_webhooks == []

    def test_a_snapshot_that_cannot_answer_blocks_the_post(self):
        """The central case, and today's steady state: an absent key is not zero."""
        plan = self._plan(_perfect_snapshot())
        unknown = {a.name: a for a in plan.webhooks if a.action == "unknown"}
        assert "pr-merged-close-loop" in unknown
        assert unknown["pr-merged-close-loop"].body == {}
        assert "duplicate" in unknown["pr-merged-close-loop"].blocked
        assert plan.postable_webhooks == []

    def _just_created(self, snapshot, name: str):
        """Stamp one entry the way the account stamps a routine made moments ago."""
        _by_name(snapshot, name)["created_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return snapshot

    def test_a_routine_created_this_run_is_postable(self):
        snapshot = self._just_created(_perfect_snapshot(), "pr-merged-close-loop")
        plan = self._plan(snapshot, created=["cowork: pr-merged-close-loop"])
        postable = {a.name: a for a in plan.postable_webhooks}
        assert set(postable) == {"pr-merged-close-loop"}
        body = postable["pr-merged-close-loop"].body
        assert body["routine_trigger_id"] == postable["pr-merged-close-loop"].trigger_id
        assert body["hook_type"] == setup.WEBHOOK_HOOK_TYPE
        assert body["scope_id"] == setup.slug_from_url(REPO_URL)
        assert body["events"] == ["pull_request"]

    def test_an_explicit_empty_array_is_postable(self):
        """The path that lights up if the API ever starts reporting attachments."""
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "release-published-announce")["webhook_triggers"] = []
        postable = {a.name for a in self._plan(snapshot).postable_webhooks}
        # No --created and no timestamp: the empty array alone is the proof.
        assert postable == {"release-published-announce"}

    def test_an_attached_webhook_is_left_alone(self):
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "release-published-announce")["webhook_triggers"] = [{"trigger_id": "hook-1"}]
        action = next(a for a in self._plan(snapshot).webhooks if a.name == "release-published-announce")
        assert action.action == "ok" and action.body == {} and action.webhook_id == "hook-1"

    def test_a_routine_with_no_block_produces_no_action(self):
        planned = {a.name for a in self._plan(_perfect_snapshot()).webhooks}
        assert "security-sweep" not in planned

    def test_a_created_claim_the_account_contradicts_is_refused(self):
        """--created is a caller's claim; created_at is the server's answer.

        The dangerous mistake the flag could carry is naming a routine that already
        existed — which attaches a second webhook to one that already fires, with
        no way to delete it. So the claim is checked, not trusted.
        """
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "pr-merged-close-loop")["created_at"] = "2020-01-01T00:00:00Z"
        plan = self._plan(snapshot, created=["cowork: pr-merged-close-loop"])
        assert plan.postable_webhooks == []
        blocked = next(a for a in plan.webhooks if a.name == "pr-merged-close-loop")
        assert "not within the last" in blocked.blocked

    def test_a_created_claim_with_no_timestamp_is_refused(self):
        plan = self._plan(_perfect_snapshot(), created=["cowork: pr-merged-close-loop"])
        assert plan.postable_webhooks == []

    def test_an_invalid_block_never_reaches_a_body(self):
        """The function that builds the irreversible POST is the one that refuses it.

        The API accepts a misspelled event with a 200 and has no delete, so a bad
        block posted once is a webhook that never fires and cannot be removed.
        """
        snapshot = self._just_created(_perfect_snapshot(), "release-published-announce")
        broken = dataclasses.replace(
            next(r for r in ROUTINES if r.name == "release-published-announce"),
            webhook={"source": "github", "events": ["not_an_event"]},
        )
        routines = [r for r in ROUTINES if r.name != "release-published-announce"] + [broken]
        plan = setup.trigger_plan(snapshot, routines=routines, created=["cowork: release-published-announce"])
        action = next(a for a in plan.webhooks if a.name == "release-published-announce")
        assert action.body == {} and "not valid" in action.blocked
        assert plan.postable_webhooks == []

    def test_a_postable_body_carries_nothing_the_file_wrote(self):
        snapshot = self._just_created(_perfect_snapshot(), "pr-opened-dod-audit")
        plan = self._plan(snapshot, created=["cowork: pr-opened-dod-audit"])
        body = plan.postable_webhooks[0].body
        assert set(body) == {"source", "hook_type", "scope_id", "events", "filter", "routine_trigger_id"}

    def test_the_webhook_plan_is_json_serialisable(self):
        payload = json.loads(json.dumps(self._plan(_perfect_snapshot()).as_dict()))
        assert payload["webhooks_blocked"]
        assert all(entry["body"] == {} for entry in payload["webhooks"])

    def test_a_blocked_plan_is_still_clean(self):
        """`webhooks_blocked` is the ordinary state, not a change waiting to be applied."""
        assert self._plan(_perfect_snapshot()).clean

    def test_self_update_names_the_deployer_and_nothing_else(self):
        snapshot = _perfect_snapshot()
        _by_name(snapshot, setup.DEPLOY_ROUTINE)["cron_expression"] = "0 5 * * *"
        plan = self._plan(snapshot)
        assert plan.self_update["name"] == setup.DEPLOY_ROUTINE
        assert plan.self_update["fields"]["cron"]["live"] == "0 5 * * *"

    def test_no_self_update_when_the_deployer_matches(self):
        assert self._plan(_perfect_snapshot()).self_update is None

    def test_no_create_reports_instead_of_registering(self):
        """An unattended run applies updates and leaves creates to a human.

        Two runs of a create race with no lock and no undo: both list a fleet
        missing the same routine, both POST it, and both copies then fire. There
        is no orphan, so `suspicious` stays false and nothing downstream objects.
        An update is safe to race, because applying it twice writes the same value.
        """
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        _by_name(snapshot, "retro-sweep")["cron_expression"] = "0 0 * * 0"
        plan = setup.trigger_plan(snapshot, allow_create=False)

        assert plan.creates_blocked == ["digest"]
        assert plan.postable_creates == []
        assert [a.body for a in plan.create] == [{}], "a blocked action must carry nothing to post"
        assert "no delete" in plan.create[0].blocked
        # The update is untouched: it is the half that is safe to apply unattended.
        assert [a.name for a in plan.update] == ["retro-sweep"]
        assert plan.update[0].body and plan.update[0].blocked is None

    def test_allowing_creates_is_still_the_default(self):
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        plan = setup.trigger_plan(snapshot)
        assert plan.creates_blocked == []
        assert plan.postable_creates and plan.create[0].body

    def test_a_blocked_create_needs_nothing_from_the_account(self):
        """`needs` names what a *postable* body would want. A blocked one wants nothing."""
        plan = setup.trigger_plan([], routines=ROUTINES, allow_create=False)
        assert plan.needs == []
        assert plan.applied_nothing

    def test_a_plan_that_only_blocks_is_not_silent(self):
        """`clean` and `applied_nothing` differ, and the difference is whether anyone is told."""
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        plan = setup.trigger_plan(snapshot, allow_create=False)
        assert plan.applied_nothing and not plan.clean

    def test_no_planned_body_ever_carries_enabled(self):
        """Structural, across every kind of drift: a deploy cannot un-pause the fleet.

        `pause` is a supported verb, and a deploy that quietly re-enabled a routine
        somebody switched off would undo that decision with nothing said.
        """
        snapshot = _perfect_snapshot()
        for entry in snapshot:
            entry["enabled"] = False
            entry["cron_expression"] = "0 0 * * 0"
        for action in setup.trigger_plan(snapshot).update:
            assert "enabled" not in action.body


class TestWebhookFixture:
    """The create body, pinned against one real exchange.

    The counterpart of TestObservedTrigger: every other webhook body in this suite
    is generated by desired_webhook(), so a mistake mirrored in the test would
    round-trip cleanly. This file is the one input neither side produced.
    """

    @pytest.fixture
    def live(self) -> dict:
        return json.loads(WEBHOOK_FIXTURE.read_text(encoding="utf-8"))

    def test_the_body_we_send_matches_the_one_that_worked(self, live: dict):
        routine = next(r for r in ROUTINES if r.name == setup.DEPLOY_ROUTINE)
        body = setup.desired_webhook(routine, live["request"]["routine_trigger_id"], live["request"]["scope_id"])
        assert set(body) == set(live["request"])
        assert body["hook_type"] == live["request"]["hook_type"]
        assert body["source"] == live["request"]["source"]

    def test_the_response_carries_no_filter(self, live: dict):
        """Recorded because it is why nothing can verify a stored filter — including us."""
        assert "filter" not in live["response"]["trigger"]

    def test_a_routine_get_reports_no_webhooks(self, live: dict):
        """The fact the whole `created` gate rests on."""
        assert not set(live["routine_get_keys"]) & set(setup.WEBHOOK_KEYS)

    def test_the_fixture_names_no_model(self, live: dict):
        assert "claude-" not in WEBHOOK_FIXTURE.read_text(encoding="utf-8")

    def test_observed_webhooks_reads_the_response_shape(self):
        assert setup.observed_webhooks({}) is None
        assert setup.observed_webhooks({"webhook_triggers": []}) == ()
        assert setup.observed_webhooks({"webhook_triggers": [{"trigger_id": "x"}]}) == ({"trigger_id": "x"},)

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/example/yeaboi.ai",
            "https://github.com/example/yeaboi.ai.git",
            "git@github.com:example/yeaboi.ai.git",
            "ssh://git@github.com/example/yeaboi.ai",
        ],
    )
    def test_every_remote_form_resolves_to_one_scope_id(self, url: str):
        assert setup.slug_from_url(url) == "github.com/example/yeaboi.ai"

    def test_an_unresolvable_remote_is_none_rather_than_a_guess(self):
        assert setup.slug_from_url("") is None
        assert setup.slug_from_url("https://gitlab.com/example/thing") is None


class TestStrict:
    """`--strict` — the difference between a laptop and an unattended deploy.

    Without it, a `gh` call rejected for lack of repo-admin prints a note and
    exits 0, which is right when a human is reading stdout and is a silent green
    when nothing is.
    """

    @pytest.fixture
    def gh(self, monkeypatch):
        calls: list[tuple[str, ...]] = []
        replies: dict[tuple[str, ...], tuple[int, str]] = {}

        def fake(*args: str):
            calls.append(args)
            code, out = replies.get(args[:2], (0, ""))
            return subprocess.CompletedProcess(args, code, out, "boom" if code else "")

        monkeypatch.setattr(setup, "_gh", fake)
        monkeypatch.setattr(setup, "gh_ready", lambda: True)
        monkeypatch.setattr(setup, "repo_slug", lambda: "example/yeaboi.ai")
        return type("Gh", (), {"calls": calls, "replies": replies})()

    @pytest.fixture(autouse=True)
    def reset_strict(self):
        """main() resets its own state; this only stops one test leaking into the next."""
        yield
        setup.STRICT.strict = False
        setup.STRICT.degraded.clear()

    def test_two_runs_in_one_process_do_not_accumulate(self, gh):
        """A degradation from an earlier call must not fail a later clean one."""
        gh.replies[("variable", "set")] = (1, "")
        assert setup.main(["--strict"]) == 1
        gh.replies.clear()
        assert setup.main(["--strict"]) == 0

    def test_a_rejected_variable_write_fails_under_strict(self, gh, capsys):
        gh.replies[("variable", "set")] = (1, "")
        assert setup.main(["--strict"]) == 1
        assert "strict:" in capsys.readouterr().err

    def test_the_same_run_exits_zero_without_strict(self, gh, capsys):
        gh.replies[("variable", "set")] = (1, "")
        assert setup.main([]) == 0

    def test_a_clean_run_exits_zero_under_strict(self, gh):
        assert setup.main(["--strict"]) == 0

    def test_an_informational_note_does_not_fail(self, capsys):
        """`--local` skipping the GitHub half is a remark, not a step that failed."""
        assert setup.main(["--check", "--local", "--strict"]) == 0

    def _plan(self, tmp_path: Path, snapshot, *extra) -> subprocess.CompletedProcess[str]:
        path = tmp_path / "live.json"
        path.write_text(json.dumps({"data": snapshot}))
        return subprocess.run(
            [sys.executable, str(_MODULE_PATH), "--plan", "--triggers", str(path), *extra],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_suspicious_plan_refuses_under_strict(self, tmp_path: Path):
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        snapshot[0]["name"] = "cowork: something-nobody-wrote"
        result = self._plan(tmp_path, snapshot, "--strict")
        assert result.returncode == 2
        assert "suspicious" in result.stderr
        assert json.loads(result.stdout)["suspicious"] is True, "the plan is still printed before refusing"

    def test_the_same_plan_is_advisory_without_strict(self, tmp_path: Path):
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        snapshot[0]["name"] = "cowork: something-nobody-wrote"
        assert self._plan(tmp_path, snapshot).returncode == 0

    def test_a_fleet_wide_update_refuses_under_strict(self, tmp_path: Path):
        snapshot = _perfect_snapshot()
        for entry in snapshot:
            entry["mcp_connections"] = [*entry["mcp_connections"], {"name": "Gmail"}]
        result = self._plan(tmp_path, snapshot, "--strict")
        assert result.returncode == 2
        assert "a human should look first" in result.stderr

    def test_a_fleet_wide_change_is_allowed_when_asked_for(self, tmp_path: Path):
        snapshot = _perfect_snapshot()
        for entry in snapshot:
            entry["mcp_connections"] = [*entry["mcp_connections"], {"name": "Gmail"}]
        assert self._plan(tmp_path, snapshot, "--strict", "--allow-mass-change").returncode == 0

    def test_no_create_bounds_the_cap_to_what_would_be_applied(self, tmp_path: Path):
        """A truncated snapshot is harmless once creates are blocked — nothing is posted."""
        result = self._plan(tmp_path, _perfect_snapshot()[:4], "--strict", "--no-create")
        assert result.returncode == 0
        assert "need registering and were not" in result.stderr
        assert all(entry["body"] == {} for entry in json.loads(result.stdout)["create"])

    def test_a_truncated_snapshot_refuses_under_strict(self, tmp_path: Path):
        """The create half of the cap, and the reason it counts creates at all.

        `suspicious` needs a create *and* an orphan. A snapshot that loses its
        trailing entries — the plausible failure, since it reaches this script by
        way of a model writing a large API response to a file — produces creates
        with no orphans, and every surviving entry still supplies repo_url,
        environment_id and connectors, so `needs` is empty too. Nothing else in
        the plan would object, and a create cannot be undone: this API has no
        delete, so the fleet would end up with two of everything that got cut.
        """
        snapshot = _perfect_snapshot()[:4]
        result = self._plan(tmp_path, snapshot, "--strict")
        assert result.returncode == 2
        assert "create(s)" in result.stderr and "a human should look first" in result.stderr

        plan = json.loads(result.stdout)
        assert plan["suspicious"] is False, "the point: nothing else in the plan objects"
        assert plan["needs"] == []
        assert plan["orphans"] == []

    def test_the_cap_can_be_lifted_for_a_deliberate_fleet_wide_change(self, tmp_path: Path):
        """A first-ever deploy legitimately creates every routine — and is interactive.

        It still stops, but on `needs` rather than on the cap: an empty account has
        no live routine to lift the connector objects off, and a body carrying an
        empty `mcp_connections` attaches every connector on the account. Two
        independent refusals, and the flag only clears one of them.
        """
        result = self._plan(tmp_path, [], "--strict", "--allow-mass-change", "--environment", ENVIRONMENT)
        assert result.returncode == 2
        assert "connectors unresolved" in result.stderr
        assert "a human should look first" not in result.stderr

    def test_an_ordinary_one_routine_change_passes(self, tmp_path: Path):
        snapshot = [e for e in _perfect_snapshot() if e["name"] != "cowork: digest"]
        assert self._plan(tmp_path, snapshot, "--strict").returncode == 0

    def test_a_created_name_the_snapshot_never_heard_of_is_refused(self, tmp_path: Path):
        """The create and the re-list disagree — the one state in which posting a
        webhook could attach it to the wrong routine, or to none."""
        result = self._plan(tmp_path, _perfect_snapshot(), "--created", "cowork: not-a-routine")
        assert result.returncode == 2
        assert "does not contain" in result.stderr


class TestSlackTemplates:
    """The literal Slack templates in ``cowork/routines/**.md``, linted.

    Every message the fleet posts is specified as a worked example rather than
    as a list of topics to cover, because the one routine specified the other
    way — ``cron/cd-deploy.md`` step 7 — wrote a fresh essay per run and put
    thirty-six of them in ``#yeaboi-claude`` in a single day. These checks are
    what stop a template drifting back out of the shared grammar in
    ``.claude/agents/cowork-scribe.md``; nothing at run time would notice.

    Two info strings carry meaning, and the tests are near-inverses:

    ``slack``        a channel message — the grammar applies.
    ``slack-reply``  a thread reply parsed by ``scripts/cowork_relay.py``
                     before anybody reads it — the grammar must *not* apply.
    """

    # Column 0 or indented inside a numbered step; the closing fence matches the
    # opening indent, which is what keeps a nested fence from ending this one.
    FENCE = re.compile(r"^(?P<indent>[ \t]*)```(?P<info>[a-z-]*)\n(?P<body>.*?)^(?P=indent)```", re.M | re.S)

    # `<emoji> **<Name>** — <clause>`. Deliberately not `(n)`: that is a *section*
    # heading's shape, and a title line wearing it reads as one.
    TITLE = re.compile(r"^(?P<emoji>\S+) \*\*(?P<name>[^*]+)\*\* — .+")
    HEADING = re.compile(r"^\S+ \*\*[^*]+\*\*\s*\(")

    # The five that were actually observed on `cd-deploy` within one day in
    # August 2026, before step 7 had a template. A blocklist is all a template
    # can be checked against — "no sign-off" is not provable from the text.
    SIGN_OFF = re.compile(
        r"(?im)^\s*(?:—\s*(?:cowork-scribe|cd-deploy|posted by)|_?generated by|co-authored-by:)",
    )

    @staticmethod
    def _blocks(info: str) -> dict[str, str]:
        """Every fenced block with ``info`` under ``cowork/routines``, dedented."""
        found: dict[str, str] = {}
        for path in sorted(setup.ROUTINES_DIR.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            for n, match in enumerate(TestSlackTemplates.FENCE.finditer(text), start=1):
                if match.group("info") != info:
                    continue
                indent = match.group("indent")
                body = "\n".join(
                    line[len(indent) :] if line.startswith(indent) else line
                    for line in match.group("body").rstrip("\n").split("\n")
                )
                found[f"{path.relative_to(setup.ROUTINES_DIR)}#{n}"] = body
        return found

    def test_every_routine_that_posts_shows_a_template(self):
        """The list is the point: a poster with no worked example is the state
        this whole class exists to stop coming back."""
        posters = {
            "cron/digest.md",
            "cron/shipped-standup.md",
            "cron/agents-standup.md",
            "cron/release-promote-ask.md",
            "cron/cd-deploy.md",
            "cron/security-sweep.md",
            "cron/go-migration-progress.md",
            "events/release-published-announce.md",
            "events/go-migration-wave-merged.md",
        }
        have = {key.split("#")[0] for key in self._blocks("slack")}
        assert posters <= have, f"these routines post to Slack with no template: {sorted(posters - have)}"

    def test_the_first_line_is_a_title_line(self):
        for key, body in sorted(self._blocks("slack").items()):
            first = body.split("\n")[0]
            assert self.TITLE.match(first), f"{key}: not a title line: {first!r}"
            assert not self.HEADING.match(first), f"{key}: title line wears a section heading's `(n)`: {first!r}"

    def test_no_line_carries_slack_mrkdwn_emphasis(self):
        """`*x*` is Markdown italic and mrkdwn bold, and the connector reads
        Markdown — so the wrong dialect does not fail, it renders the wrong
        weight. Same check as the agenda's, on the templates."""
        for key, body in sorted(self._blocks("slack").items()):
            for line in body.split("\n"):
                assert not re.search(r"(?<!\*)\*(?!\*)", line), f"{key}: {line}"

    def test_no_bare_urls(self):
        """Links are embedded in the text they name. Unlike the agenda's check,
        this cannot be `"http" not in blob` — these templates carry embedded
        links on purpose, so the link form is stripped before looking."""
        for key, body in sorted(self._blocks("slack").items()):
            stripped = re.sub(r"\]\(https?://[^)]+\)", "]()", body)
            assert "http" not in stripped, f"{key}: a URL outside a [title](url) link"

    def test_emoji_only_ever_anchor_a_line(self):
        """One anchor, at the start. `line[0]` would be wrong: 🗳️ and ⚠️ are
        `So` followed by a variation selector, so the anchor is not one char."""
        for key, body in sorted(self._blocks("slack").items()):
            for line in body.split("\n"):
                # The divider is U+2500 BOX DRAWINGS LIGHT HORIZONTAL, whose
                # category is also `So`. It is a rule, not an anchor, and a
                # line of them is the whole line.
                if line and set(line) == {"\u2500"}:
                    continue
                # ✅/❌ are verbs, not anchors, and a footer that instructs
                # carries both on one line by design. They have their own rule
                # in `test_the_approval_verbs_never_head_anything`.
                # ▰/▱ are the migration meter's track — a bar, not an
                # anchor, the same ruling as the all-─ divider above.
                symbols = [
                    char
                    for char in line
                    if unicodedata.category(char) == "So" and char not in {"\u2705", "\u274c", "\u25b0", "\u25b1"}
                ]
                if not symbols:
                    continue
                assert len(symbols) == 1, f"{key}: more than one emoji on a line: {line}"
                assert line.startswith(symbols[0]), f"{key}: an anchor belongs at the start: {line}"

    def test_the_approval_verbs_never_head_anything(self):
        """✅/❌ are the verbs a human reacts with. Forbidden in a title line or
        a heading, where a reader has to work out whether they mean something;
        allowed in a footer that instructs, which is them doing their job."""
        for key, body in sorted(self._blocks("slack").items()):
            for line in body.split("\n"):
                if self.TITLE.match(line) or self.HEADING.match(line):
                    assert not {"✅", "❌"} & set(line), f"{key}: approval verb in a heading: {line}"

    def test_the_robot_marker_is_never_written_as_text(self):
        """🤖 is the relay's `handled` marker. An allowlisted human's 🤖 on a
        digest item hides it from every future run, so the glyph is never made
        ambient in the channel it acts in."""
        for key, body in sorted(self._blocks("slack").items()):
            assert "\U0001f916" not in body, f"{key}: 🤖 is a reserved marker, not decoration"

    def test_no_template_signs_off(self):
        for key, body in sorted(self._blocks("slack").items()):
            assert not self.SIGN_OFF.search(body), f"{key}: the channel has one voice; drop the sign-off"

    def test_a_parsed_reply_is_exempt_from_all_of_it(self):
        """The inverse lint, and the more valuable one. These lines are parsed
        before they are read: `ITEM_RE`/`PROMOTE_RE` in `scripts/cowork_relay.py`
        anchor on a leading `#<n> — `, so a reply that gains a bold run, an
        emoji or an embedded link is an approval that cannot land."""
        blocks = self._blocks("slack-reply")
        assert blocks, "the parsed reply contracts are no longer shown as worked examples"
        for key, body in sorted(blocks.items()):
            for line in body.split("\n"):
                assert re.match(r"^#(\d+|<issue(?:-number)?>)\s+—\s", line), f"{key}: must lead with the number: {line}"
                assert "**" not in line, f"{key}: no bold in a parsed reply: {line}"
                assert "](" not in line, f"{key}: no embedded link in a parsed reply: {line}"
                assert not [c for c in line if unicodedata.category(c) == "So"], f"{key}: no emoji: {line}"

    def test_an_ack_never_leads_with_the_issue_number(self):
        """The relay posts as the human, so its own acks come back on the next
        hourly read looking exactly like human input. `ITEM_RE` is anchored, and
        that anchor is the only thing keeping the routine from answering itself
        — so an ack states the verb first and the number inside the sentence.

        Checked with the real regex rather than a copy of it, against the
        examples the routine actually shows, so the two cannot drift apart.
        """
        relay_spec = importlib.util.spec_from_file_location("cowork_relay", ROOT / "scripts" / "cowork_relay.py")
        relay_module = importlib.util.module_from_spec(relay_spec)
        relay_spec.loader.exec_module(relay_module)
        relay = (setup.ROUTINES_DIR / "cron" / "slack-relay.md").read_text(encoding="utf-8")
        examples = re.search(r"for an action, exactly what was done \((.*?)\), one line", relay, re.S)
        assert examples, "cron/slack-relay.md no longer shows what an ack looks like"
        quoted = re.findall(r'"([^"]+)"', examples.group(1))
        assert len(quoted) >= 2, f"too few ack examples to check: {quoted}"
        for ack in quoted:
            assert not relay_module.ITEM_RE.match(ack), (
                f"this ack parses as a digest item reply, so the relay would answer itself: {ack!r}"
            )


class TestProposalSlots:
    """The proposal cap, which is the only thing bounding the propose lane.

    Before it, a scout returning ten finds could open nine issues in one morning
    and nothing looked at how many were already there — the fleet's queue reached
    forty-one, behind a digest whose whole job is to put a short list in front of
    a human. The arithmetic lives in Python for the reason the trigger reconcile
    does: a routine asked to count sixteen queues by eye will miscount one, and
    nothing downstream would notice.
    """

    NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    @staticmethod
    def _issue(number: int, days_old: int = 1, **extra) -> dict:
        created = TestProposalSlots.NOW - timedelta(days=days_old, hours=1)
        return {
            "number": number,
            "title": f"[bug][platform] finding {number}",
            "created_at": created.isoformat().replace("+00:00", "Z"),
            **extra,
        }

    def _serve(self, monkeypatch, items, ok=True, error=""):
        """Answer the REST read with a fixed page, over the API transport."""
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setenv("GH_TOKEN", "t")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        seen: list[str] = []

        def paged(path, key=None):
            seen.append(path)
            return setup.ApiResult(ok, items, error)

        monkeypatch.setattr(setup.transport, "api_paged", paged)
        return seen

    @pytest.mark.parametrize(("open_count", "slots"), [(0, 2), (1, 1), (2, 0), (3, 0), (10, 0)])
    def test_the_cap_arithmetic(self, monkeypatch, open_count, slots):
        # Clamped at zero above the cap rather than going negative: a queue can
        # legitimately exceed it — a human filing by hand, or the cap lowered
        # later — and a negative allowance is a number arithmetic downstream
        # could turn back into room.
        self._serve(monkeypatch, [self._issue(n) for n in range(open_count)])
        answer = setup.proposal_slots("platform", now=self.NOW)
        assert (answer["open"], answer["slots"], answer["cap"]) == (open_count, slots, setup.PROPOSAL_CAP)

    def test_pull_requests_do_not_eat_a_slot(self, monkeypatch):
        """GitHub models a PR as an issue, and `/issues` returns both. A cowork PR
        carries `workstream:<name>` by house rule, so counting it would let the
        one-open-PR guard charge twice for the same piece of work."""
        self._serve(
            monkeypatch,
            [self._issue(1), self._issue(2, pull_request={"url": "…"}), self._issue(3, pull_request={})],
        )
        answer = setup.proposal_slots("platform", now=self.NOW)
        assert answer["open"] == 1
        assert [item["number"] for item in answer["blocking"]] == [1]

    def test_an_unreadable_count_is_null_and_never_zero(self, monkeypatch):
        """The distinction the sweep depends on. `slots: 0` is a full queue and
        `slots: None` is a failed query, and the sweep answers them differently —
        the second files criticals and says the read failed. Reporting a refused
        query as an empty queue would open the gate on exactly the unattended
        runs it exists to bound."""
        self._serve(monkeypatch, None, ok=False, error="HTTP 403")
        answer = setup.proposal_slots("platform", now=self.NOW)
        assert answer["slots"] is None
        assert answer["open"] is None
        assert answer["error"] == "HTTP 403"
        assert answer["blocking"] == []

    def test_a_non_list_body_is_unreadable_too(self, monkeypatch):
        """What an egress proxy's error page looks like once it has been parsed:
        a 2xx carrying something that is not the collection."""
        self._serve(monkeypatch, {"message": "Not Found"})
        assert setup.proposal_slots("platform", now=self.NOW)["slots"] is None

    def test_no_slug_is_unreadable_rather_than_empty(self, monkeypatch):
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setattr(setup, "repo_slug", lambda: None)
        assert setup.proposal_slots("platform", now=self.NOW)["slots"] is None

    def test_the_blocking_list_names_the_issues_to_answer(self, monkeypatch):
        """Oldest first, because answering one is what reopens a slot and the
        one that has waited longest is the one to answer."""
        self._serve(monkeypatch, [self._issue(180, days_old=2), self._issue(146, days_old=7)])
        blocking = setup.proposal_slots("platform", now=self.NOW)["blocking"]
        assert [(item["number"], item["age_days"]) for item in blocking] == [(146, 7), (180, 2)]
        assert all(item["title"] for item in blocking)

    def test_an_unparseable_timestamp_does_not_break_the_row(self, monkeypatch):
        """A missing age is a missing age. The row still names the issue, because
        the number is what a human needs and the age is decoration on it."""
        self._serve(monkeypatch, [{"number": 5, "title": "t", "created_at": "not a date"}])
        blocking = setup.proposal_slots("platform", now=self.NOW)["blocking"]
        assert blocking == [{"number": 5, "title": "t", "age_days": None}]

    def test_the_read_is_rest_on_both_transports(self, monkeypatch):
        """The one that matters most. Every other read in the script branches to
        `gh <verb> --json` when `gh` is there, and that is right for labels. It
        is wrong here: `gh issue list --json` is GraphQL underneath, and
        ``cowork_github_access_live.json`` records a routine session where `gh`
        was installed, GH_TOKEN was set, and the GraphQL POST came back 403 from
        the egress proxy anyway. A gate built on the refused call fails on the
        unattended runs it exists to bound."""
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        calls: list[tuple[str, ...]] = []

        def fake_gh(*args):
            calls.append(args)
            return subprocess.CompletedProcess(list(args), 0, json.dumps([self._issue(1)]), "")

        monkeypatch.setattr(setup.transport, "gh", fake_gh)
        assert setup.proposal_slots("platform", now=self.NOW)["open"] == 1
        assert calls, "the gh branch made no call"
        assert calls[0][0] == "api", f"the gh branch must ask REST, not {calls[0]}"
        asked = " ".join(calls[0])
        assert "issue" not in asked.split("?")[0].split("/")[0]
        assert "labels=cowork:proposal,workstream:platform" in asked
        # Paged, not a bare `gh api`. Without this the call stops at 30 and reads
        # back as a short list rather than as an error — which left fifteen of
        # forty-five issues unclassified while the plan looked clean over them.
        assert "--paginate" in calls[0], calls[0]

    def test_a_gh_html_error_page_is_unreadable(self, monkeypatch):
        """`gh api` exiting 0 with something that will not parse is the same
        proxy failure the REST half handles, arriving through the other door."""
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            setup.transport, "gh", lambda *a: subprocess.CompletedProcess(list(a), 0, "<html>nope</html>", "")
        )
        assert setup.proposal_slots("platform", now=self.NOW)["slots"] is None

    def test_the_fleet_report_covers_every_workstream(self, monkeypatch, capsys):
        self._serve(monkeypatch, [])
        assert setup.report_proposal_slots(None, now=self.NOW) == 0
        rows = json.loads(capsys.readouterr().out)
        assert [row["workstream"] for row in rows] == setup.parse_workstreams()

    def test_one_workstream_reports_an_object_not_a_list(self, monkeypatch, capsys):
        self._serve(monkeypatch, [self._issue(1)])
        assert setup.report_proposal_slots("platform", now=self.NOW) == 0
        assert json.loads(capsys.readouterr().out)["workstream"] == "platform"

    def test_an_unknown_workstream_is_an_error_not_an_empty_queue(self, monkeypatch, capsys):
        """A typo must not read as "nothing open, file away" — that is the one
        wrong answer this command can give."""
        self._serve(monkeypatch, [])
        assert setup.report_proposal_slots("web-ui", now=self.NOW) == 2
        assert "unknown workstream" in capsys.readouterr().err

    def test_a_full_queue_still_exits_zero(self, monkeypatch, capsys):
        """A full queue is a normal outcome, not a failure. A routine branching
        on the exit status would read a healthy pause as a broken command."""
        self._serve(monkeypatch, [self._issue(1), self._issue(2)])
        assert setup.report_proposal_slots("platform", now=self.NOW) == 0
        assert json.loads(capsys.readouterr().out)["slots"] == 0

    def test_the_cap_is_the_number_the_house_rules_state(self):
        """The rule is written in two places — prose a routine reads, and the
        constant it obeys — so they are pinned together."""
        rules = (setup.COWORK / "house-rules.md").read_text(encoding="utf-8")
        assert f"`PROPOSAL_CAP = {setup.PROPOSAL_CAP}`" in rules, (
            f"house-rules.md no longer states a cap of {setup.PROPOSAL_CAP}"
        )


class TestTheQueueSplitsTheBacklog:
    """`cowork:queued` — a work item waiting on the fleet, not a question waiting
    on a human.

    The failure this ends: the propose lane was the only lane with an exit, and
    the exit was a human verb nobody ever used. Forty-two proposals accumulated
    against a cap of two per workstream, and because both dedupe passes were
    lane-blind, a bug that the auto lane could have shipped *that morning* was
    dropped for up to fourteen days on the strength of an unanswered question
    about the same bug — then closed on the age-out timer, which both passes read
    as a rejection and suppressed permanently.

    Every assertion below fails silently at run time. A queued issue counted
    against the cap is a work item holding a slot that only a human verb can
    release, on an issue no human will ever be shown; the number looks perfectly
    reasonable either way.
    """

    NOW = TestProposalSlots.NOW
    _issue = staticmethod(TestProposalSlots._issue)
    _serve = TestProposalSlots._serve

    def _serve_split(self, monkeypatch, *, proposals, queued):
        """Answer the two REST reads differently, keyed on the label in the path.

        `proposal_slots` makes both calls now, and the whole point of the split is
        that they return different sets — a fixture that serves one page to both
        cannot tell a passing filter from an absent one.
        """
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setenv("GH_TOKEN", "t")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        seen: list[str] = []

        def paged(path, key=None):
            seen.append(path)
            wanted = queued if f"labels={setup.QUEUE_LABEL}," in path else proposals
            return setup.ApiResult(True, wanted, "")

        monkeypatch.setattr(setup.transport, "api_paged", paged)
        return seen

    def test_the_label_exists_so_cowork_setup_creates_it(self):
        """A label the routines apply but `make cowork-setup` never creates is a
        reclassification that 404s on the first sweep to try it."""
        assert setup.QUEUE_LABEL in {label.name for label in setup.expected_labels()}

    def test_the_two_labels_are_different_labels(self):
        """Reusing `claude-implement` would have been shorter and wrong: that one
        fires a 110-turn `claude.yml` job, and the closed list of labels a machine
        may apply must not widen just to let a sweep pick up its own backlog."""
        assert setup.QUEUE_LABEL != setup.PROPOSAL_LABEL != "claude-implement"

    def test_a_queued_issue_does_not_eat_a_proposal_slot(self, monkeypatch):
        """The whole point. A find the auto lane owns is work, not a question, so
        it must not hold a slot that only a human verb can release."""
        self._serve_split(monkeypatch, proposals=[self._issue(1)], queued=[self._issue(2), self._issue(3)])
        answer = setup.proposal_slots("platform", now=self.NOW)
        assert (answer["open"], answer["slots"], answer["queued"]) == (1, 1, 2)
        assert [item["number"] for item in answer["blocking"]] == [1]

    def test_both_labels_reads_as_queued_never_as_proposed(self, monkeypatch):
        """The crash window. Reclassification is add-then-remove, so a run that
        dies between the two calls leaves both labels on. Counted as a proposal it
        holds a slot no verb can release; counted as queued it costs one line in
        one digest. The safe direction is written down here rather than left to
        whichever filter happened to run first."""
        both = self._issue(9, labels=[{"name": setup.PROPOSAL_LABEL}, {"name": setup.QUEUE_LABEL}])
        self._serve_split(monkeypatch, proposals=[both], queued=[both])
        assert setup.proposal_slots("platform", now=self.NOW)["slots"] == setup.PROPOSAL_CAP

    def test_a_string_label_list_is_read_too(self, monkeypatch):
        """`labels` is a list of objects on this endpoint and a list of bare
        strings in one or two places GitHub still emits the older shape. Reading
        only the first would silently count every queued issue against the cap
        again."""
        self._serve_split(monkeypatch, proposals=[self._issue(9, labels=[setup.QUEUE_LABEL])], queued=[])
        assert setup.proposal_slots("platform", now=self.NOW)["open"] == 0

    def test_an_approved_issue_is_never_offered_to_a_sweep(self, monkeypatch):
        """A human can add `claude-implement` to anything at any moment, and
        `claude.yml` then fires an implement job on it. Offering the same issue to
        a sweep races two builders at one item and opens two PRs for it. The
        digest already watches these under "Approved, no PR yet" — they have a
        different owner, not no owner."""
        self._serve_split(
            monkeypatch,
            proposals=[],
            queued=[self._issue(7), self._issue(8, labels=[{"name": "claude-implement"}])],
        )
        assert [item["number"] for item in setup.queue_report("platform", now=self.NOW)["items"]] == [7]

    def test_an_unreadable_queue_is_null_and_never_zero(self, monkeypatch):
        """Same rule `slots` follows. A queue reported as empty when it could not
        be asked is a workstream that looks finished rather than blind, and the
        digest's "queued but nothing merged in 21 days" alarm never fires."""
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setenv("GH_TOKEN", "t")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.transport, "api_paged", lambda *a, **k: setup.ApiResult(False, None, "403"))
        assert setup.queue_report("platform", now=self.NOW)["queued"] is None
        assert setup.proposal_slots("platform", now=self.NOW)["queued"] is None

    def test_the_queue_read_is_rest_on_both_transports(self, monkeypatch):
        """The same argument `open_proposals` makes at length: `gh issue list
        --json` is GraphQL underneath and comes back 403 from the routine
        sessions' egress proxy, while `GET /repos/{slug}/issues` is served. A
        queue a sweep cannot read is a drain that silently stops."""
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        calls: list[tuple[str, ...]] = []

        def fake_gh(*args):
            calls.append(args)
            return subprocess.CompletedProcess(list(args), 0, json.dumps([self._issue(1)]), "")

        monkeypatch.setattr(setup.transport, "gh", fake_gh)
        assert setup.queue_report("platform", now=self.NOW)["queued"] == 1
        assert calls[0][0] == "api", f"the gh branch must ask REST, not {calls[0]}"
        assert f"labels={setup.QUEUE_LABEL},workstream:platform" in " ".join(calls[0])
        assert "--paginate" in calls[0], calls[0]

    def test_a_pull_request_is_not_a_queued_item(self, monkeypatch):
        """GitHub models a PR as an issue and `/issues` returns both. A cowork PR
        carries `workstream:<name>` by house rule, so an open PR would read as
        work still waiting to be built — the item it *is* the build of."""
        self._serve_split(
            monkeypatch,
            proposals=[],
            queued=[self._issue(7), self._issue(8, pull_request={"url": "…"})],
        )
        assert [item["number"] for item in setup.queue_report("platform", now=self.NOW)["items"]] == [7]

    def test_the_queue_is_reported_in_build_order(self, monkeypatch):
        """Highest impact first, ties to lower risk, ties to the oldest — the same
        key the auto lane has always sorted on, with age added so the queue
        drains. Computed here because a routine asked to sort fifteen queues by
        eye will get one wrong and nothing downstream would notice."""
        # The scribe's template verbatim, `cowork-scribe.md:66` — impact a number
        # and risk a WORD. The first version of this test wrote `**Risk** 1`, a
        # shape no issue in the repo contains, and the regex under it matched
        # nothing real: every queued item ranked as a neutral 3 and the queue
        # degenerated to oldest-first, with CI fully green over it.
        body = "**Impact** {i} · **Effort** M · **Risk** {r}"
        self._serve_split(
            monkeypatch,
            proposals=[],
            queued=[
                self._issue(1, days_old=1, body=body.format(i=3, r="low")),
                self._issue(2, days_old=1, body=body.format(i=5, r="high")),
                self._issue(3, days_old=1, body=body.format(i=5, r="low")),
                self._issue(4, days_old=9, body=body.format(i=3, r="low")),
            ],
        )
        items = setup.queue_report("platform", now=self.NOW)["items"]
        assert [item["number"] for item in items] == [3, 2, 4, 1]

    def test_a_body_with_no_score_line_ranks_in_the_middle(self, monkeypatch):
        """An unparsed line is a missing fact, not a low score. Ranking it last
        would quietly bury every find the scribe wrote before that template
        existed — which is most of the backlog this queue was seeded from."""
        self._serve_split(
            monkeypatch,
            proposals=[],
            queued=[
                self._issue(1, body="no scores here"),
                self._issue(2, body="**Impact** 5 · **Effort** M · **Risk** low"),
                self._issue(3, body="**Impact** 1 · **Effort** M · **Risk** low"),
            ],
        )
        items = setup.queue_report("platform", now=self.NOW)["items"]
        assert [item["number"] for item in items] == [2, 1, 3]
        assert items[1]["impact"] is None, "a missing score must be reported as missing, not as 3"

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("**Impact** 4 · **Effort** S · **Risk** low", (4, 1)),
            ("**Impact** 2 · **Effort** M · **Risk** med", (2, 2)),
            ("**Impact** 5 · **Effort** L · **Risk** high", (5, 3)),
            ("**Impact** 5 · **Effort** L · **Risk** medium", (5, 2)),
            ("**Impact** 3 · **Effort** M · **Risk** 1", (3, 1)),
            ("**Impact** 3 with no risk at all", (3, None)),
            ("**Risk** high with no impact at all", (None, 3)),
            ("neither", (None, None)),
        ],
    )
    def test_the_score_line_parses_in_the_shape_the_scribe_writes_it(self, line, expected):
        """`cowork-scribe.md:66` writes impact as a number and **risk as a word**.

        The first version required `\\d+` for both, in one pattern — so it matched
        no issue anybody has ever filed, and because the two groups shared a match
        the unparsed risk discarded the impact beside it. Every queued item then
        ranked as a neutral 3 and the queue degenerated to oldest-first, which is
        precisely the silent miscount `_queue_rank` exists to prevent. The numeric
        form stays accepted because a hand-written issue may use it.
        """
        assert setup._scores(line) == expected

    def test_half_a_score_line_still_yields_the_half_that_is_there(self):
        """An older or hand-written issue may carry one and not the other. Losing a
        fact that is present because a different one is missing is the same class
        of bug as not parsing it at all."""
        assert setup._scores("**Impact** 4 and nothing else") == (4, None)

    def test_the_type_label_rides_along(self, monkeypatch):
        """The sweep needs it to apply the right allowlist condition — a `type:bug`
        owes a regression test that a `type:docs` does not."""
        self._serve_split(
            monkeypatch,
            proposals=[],
            queued=[self._issue(1, labels=[{"name": "type:bug"}, {"name": "workstream:platform"}])],
        )
        assert setup.queue_report("platform", now=self.NOW)["items"][0]["type"] == "bug"

    def test_an_unknown_workstream_is_an_error_not_an_empty_queue(self, monkeypatch, capsys):
        """A typo must not read as "nothing to build" — the one wrong answer this
        command can give, because it is indistinguishable from a finished drain."""
        self._serve_split(monkeypatch, proposals=[], queued=[])
        assert setup.report_queued("web-ui", now=self.NOW) == 2
        assert "unknown workstream" in capsys.readouterr().err

    def test_the_fleet_report_covers_every_workstream(self, monkeypatch, capsys):
        self._serve_split(monkeypatch, proposals=[], queued=[])
        assert setup.report_queued(None, now=self.NOW) == 0
        rows = json.loads(capsys.readouterr().out)
        assert [row["workstream"] for row in rows] == setup.parse_workstreams()

    def test_an_empty_queue_still_exits_zero(self, monkeypatch, capsys):
        """An empty queue is the goal state, not a failure."""
        self._serve_split(monkeypatch, proposals=[], queued=[])
        assert setup.report_queued("platform", now=self.NOW) == 0
        assert json.loads(capsys.readouterr().out)["queued"] == 0


class TestTheQueueContract:
    """The half of `cowork:queued` that lives in markdown, pinned.

    Every rule below fails **silently** at run time, and each one individually
    restores the failure the queue exists to end:

    - a digest that ages a queued issue out destroys the write-up *and*, because
      both dedupe passes read a closing as a human's rejection, suppresses the
      find permanently;
    - a sweep that adds the queue label without removing the proposal label
      leaves the issue counted, listed and aged out exactly as before;
    - a scout that still drops an `auto` find restating an open issue leaves the
      whole mechanism dead with nothing in any log to say so;
    - a builder that omits `Closes #<n>` makes a queue that only grows.

    None of it is observable from a run log, which is why it is asserted here.
    """

    def _read(self, path: pathlib.Path) -> str:
        return path.read_text(encoding="utf-8")

    @property
    def sweep(self) -> str:
        return self._read(setup.COWORK / "sweep-procedure.md")

    @property
    def digest(self) -> str:
        return self._read(setup.ROUTINES_DIR / "cron" / "digest.md")

    def test_the_house_rules_name_the_label(self):
        """The rule is written in two places — prose a routine reads and the
        constant it obeys — so they are pinned together, exactly as the cap is."""
        rules = self._read(setup.COWORK / "house-rules.md")
        assert f"`{setup.QUEUE_LABEL}`" in rules
        assert "## The queue" in rules

    def test_the_sweep_swaps_the_label_rather_than_adding_it(self):
        """The two labels are mutually exclusive because every consumer queries
        `cowork:proposal` through GitHub's AND-only `labels=` filter, which cannot
        express "and not queued". A sweep that only adds leaves the issue counted,
        listed and aged out exactly as before — and nothing would report it."""
        assert f"--add-label {setup.QUEUE_LABEL}" in self.sweep
        assert f"--remove-label {setup.PROPOSAL_LABEL}" in self.sweep

    def test_the_sweep_never_replaces_a_label_set(self):
        """`gh api ... PUT /issues/{n}/labels` replaces the whole set. That is how
        #172 lost `cowork:proposal`, `workstream:web-ux` and `type:security` in one
        call and then ran an implement job with no charter at all.

        Asserted over the fenced commands rather than the whole file, because the
        prose has to be free to *name* the verb in order to forbid it."""
        blocks = re.findall(r"```(?:bash|sh)?\n(.*?)```", self.sweep, re.DOTALL)
        assert blocks, "the sweep has no fenced commands left to check"
        for block in blocks:
            assert not re.search(r"(?i)\bPUT\b.*/labels", block), (
                f"a PUT on labels replaces the whole set — see issue #172:\n{block}"
            )
        assert "PUT" in self.sweep, "the prohibition itself must stay written down"

    def test_the_sweep_can_bounce_a_queued_item_back(self):
        """`cowork:queued` grants nothing — it is a hypothesis step 5 confirms.
        Without a written bounce path, an item the backfill queued wrongly has only
        two outcomes and both are wrong: built anyway, or skipped forever."""
        assert f"--remove-label {setup.QUEUE_LABEL}" in self.sweep
        assert f"--add-label {setup.PROPOSAL_LABEL}" in self.sweep

    def test_the_sweep_reads_the_queue_before_it_picks(self):
        """A sweep that never asks builds only what it found this morning, and the
        backlog it was seeded to drain sits there looking like a clean codebase."""
        assert "--queued" in self.sweep

    def test_the_digest_never_ages_out_a_queued_issue(self):
        """The single most destructive thing in this design if it goes wrong: the
        age-out closes the issue, both dedupe passes read that as a rejection, and
        the find is suppressed permanently with its write-up gone."""
        step = self.digest.partition("4. **Age out**")[2].split("\n5.")[0]
        assert step, "the age-out step is no longer findable by its heading"
        assert setup.QUEUE_LABEL in step
        assert "claude-implement" in step

    def test_the_digest_reports_the_queue_it_no_longer_lists(self):
        """The risk this design creates. Four structurally-empty type sections mean
        "nothing today" becomes the normal morning; without a Queued section a
        fleet with thirty-nine unbuilt items is indistinguishable from a clean one."""
        assert "🛠️" in self.digest
        assert "**Queued**" in self.digest

    def test_the_scout_returns_an_auto_find_that_restates_an_open_issue(self):
        """The original bug, pinned. The scout dropped these, so the sweep never
        saw them, so a stale proposal filed before the unattended lane existed
        suppressed the very work that would have cleared it."""
        scout = self._read(setup.SCOUT_AGENT)
        assert '"restates"' in scout, "the scout's schema carries no way to report a match"
        deduped = scout.partition("Deduplicate")[2]
        assert "restates" in deduped[:1500], "the dedupe step never mentions the field it must set"

    def test_a_queued_item_closes_on_merge(self):
        """Nothing else closes one. Without this the queue only grows, and the
        drain that is the whole point of the split never terminates."""
        builder = self._read(setup.REPO_ROOT / ".claude" / "agents" / "cowork-builder.md")
        assert "Closes #" in builder
        assert "Closes #" in self.sweep

    def test_the_queue_is_drain_only(self):
        """It is seeded once and only shrinks. If a sweep could *file* into it, it
        would become a second unbounded backlog hidden behind the first — with no
        cap on it at all, since the cap counts proposals."""
        rules = self._read(setup.COWORK / "house-rules.md")
        assert "drain-only" in rules


class TestMigrateProposals:
    """The one-time backfill of a proposal backlog into the build queue.

    It exists because the split in `TestTheQueueSplitsTheBacklog` is forward-only:
    a sweep reclassifies one issue at a time as it re-finds it, which would leave
    forty-five already-filed issues sitting in the digest for the fourteen days it
    takes the age-out timer to destroy them.

    **The classification is allowed to be wrong**, and every test here is written
    on that premise. `cowork:queued` grants nothing — `sweep-procedure.md` step 5
    re-checks the full allowlist and bounces what fails, at a cost of one comment.
    What is *not* allowed to be wrong is the set of verbs it uses, because those
    are the ones that can destroy a write-up.
    """

    WORKSTREAMS = ("platform", "web-ux")

    @staticmethod
    def _issue(number: int, *, labels=("cowork:proposal", "workstream:platform", "type:bug"), body=None, title="t"):
        return {
            "number": number,
            "title": title,
            "body": "**Evidence** src/x.py:1" if body is None else body,
            "labels": [{"name": name} for name in labels],
        }

    def _plan(self, *issues):
        return setup.migration_plan(issues, workstreams=self.WORKSTREAMS)

    def _actions(self, *issues):
        return [row["action"] for row in self._plan(*issues)["planned"]]

    def test_a_clean_proposal_is_queued(self):
        assert self._actions(self._issue(1)) == ["queue"]

    def test_an_approved_issue_is_never_touched(self):
        """`claude.yml` already owns it. Stripping its proposal label would be
        harmless; queuing it would race a 110-turn implement job against a sweep."""
        labels = ("cowork:proposal", "workstream:platform", "type:bug", "claude-implement")
        assert self._actions(self._issue(1, labels=labels)) == ["skip"]

    def test_a_campaign_candidate_is_never_touched(self):
        """The campaign lane is approved by provider, not by find, and describes a
        week of work across six workstreams' files."""
        labels = ("cowork:proposal", "workstream:platform", "integration:candidate")
        assert self._actions(self._issue(1, labels=labels)) == ["skip"]

    def test_an_issue_with_both_labels_is_repaired_not_added_to(self):
        """The crash-recovery case, and the only state a second run can re-plan:
        an earlier run stopped between its two calls."""
        labels = ("cowork:proposal", "cowork:queued", "workstream:platform", "type:bug")
        assert self._actions(self._issue(1, labels=labels)) == ["repair"]

    def test_an_issue_with_no_charter_is_held(self):
        """No `workstream:` label means no charter, which means no `Owns` paths —
        there is nothing to tell a builder where it may edit."""
        assert self._actions(self._issue(1, labels=("cowork:proposal", "type:bug"))) == ["hold"]
        unknown = ("cowork:proposal", "workstream:retired", "type:bug")
        assert self._actions(self._issue(1, labels=unknown)) == ["hold"]

    def test_a_feature_or_improvement_is_held(self):
        """No sweep can produce these at all, so one on an issue means a human
        wrote it or it predates the four-word vocabulary. Either way it is a
        question."""
        for kind in ("feature", "improvement", "other"):
            labels = ("cowork:proposal", "workstream:platform", f"type:{kind}")
            assert self._actions(self._issue(1, labels=labels)) == ["hold"], kind

    def test_the_codeql_carve_out_exists_in_the_recurring_path_too(self):
        """The backfill is not the only door into the queue — `sweep-procedure.md`
        step 4 reclassifies in place on every run, and a carve-out that exists in
        one and not the other is a hole that opens a week later rather than never.

        `codeql-triage.yml` dedupes per rule against the issues it fetches, and
        once an issue carries `cowork:queued` a narrower label does not return it:
        next week's run opens a second **public** issue re-asking a decision
        `triage-policy.yml` already records. This is the one consumer for which
        the two labels being mutually exclusive is a hazard rather than a
        convenience.

        The matching itself moved into `scripts/codeql_triage.py` when a
        rejection stopped being rule-scoped, so what is asserted here is the
        *fetch*: the broad label, and every state. Getting either wrong puts the
        issue outside the classifier's reach, where no test of the classifier can
        see the gap.
        """
        sweep = (setup.COWORK / "sweep-procedure.md").read_text(encoding="utf-8")
        rules = (setup.COWORK / "house-rules.md").read_text(encoding="utf-8")
        assert "codeql" in sweep.lower(), "step 4 can reclassify a codeql proposal with nothing to stop it"
        assert "codeql" in rules.lower()
        workflow = setup.REPO_ROOT / ".github" / "workflows" / "codeql-triage.yml"
        dedupe = workflow.read_text(encoding="utf-8")
        assert "gh issue list --label cowork --state all" in dedupe, (
            "the workflow must fetch by the broad `cowork` label and every state — narrowing to "
            "`cowork:proposal` loses a reclassified issue, and dropping `--state all` loses a decided one"
        )

    def test_a_codeql_proposal_is_held(self):
        """`codeql-triage.yml` opens one only for a rule whose `propose` entry in
        `triage-policy.yml` records why a human must decide it. Queuing one hands a
        recorded human decision back to a machine to re-make. Identified by title
        because its three labels are the same ones any security find carries."""
        labels = ("cowork:proposal", "workstream:security", "type:security")
        issue = self._issue(1, labels=labels, title="[security][security] codeql: actions/untrusted-checkout")
        assert setup.migration_plan([issue], workstreams=("security",))["planned"][0]["action"] == "hold"

    def test_an_issue_with_no_evidence_at_all_is_held(self):
        assert self._actions(self._issue(1, body="I think this feels slow.")) == ["hold"]

    def test_evidence_is_recognised_in_every_spelling_it_is_written_in(self):
        """The scribe writes `**Evidence**`; older issues and the CodeQL job write
        `## Evidence`. Matching only the first held #140 — which carries two
        `file:line` references under an H2 — as though it had none. A format check
        standing in for a substance check may at least not be wrong about
        punctuation."""
        for body in ("**Evidence** src/x.py:1", "## Evidence\n- src/x.py:1", "### What\nthe thing breaks"):
            assert self._actions(self._issue(1, body=body)) == ["queue"], body

    def test_a_pull_request_is_not_a_backlog_item(self):
        """`/issues` returns PRs too, and a cowork PR carries `workstream:<name>`."""
        pr = self._issue(1)
        pr["pull_request"] = {"url": "…"}
        assert self._plan(pr)["planned"] == []

    def test_a_bug_is_flagged_as_still_owing_a_reproduction(self):
        """A flag on the row, not a different action. `house-rules.md` admits a bug
        on a failing test rather than on an argument, and the sweep makes that
        judgement at build time — the backfill only records that it is owed."""
        docs = ("cowork:proposal", "workstream:platform", "type:docs")
        rows = self._plan(self._issue(1), self._issue(2, labels=docs))
        assert [row["needs_repro"] for row in rows["planned"]] == [True, False]

    def test_the_action_vocabulary_is_closed(self):
        """Four verbs, and none of them closes an issue or edits a body. A closing
        would be read by both dedupe passes as a human's rejection."""
        rows = self._plan(self._issue(1), self._issue(2, labels=("cowork:proposal", "type:bug")))
        assert {row["action"] for row in rows["planned"]} <= {"queue", "hold", "skip", "repair"}

    def test_a_second_run_plans_nothing(self):
        """Idempotent by construction: the read only returns `cowork:proposal`
        issues, and a reclassified one no longer carries that label."""
        assert self._plan()["planned"] == []

    def test_the_counts_add_up_to_the_backlog(self):
        rows = self._plan(
            self._issue(1),
            self._issue(2, labels=("cowork:proposal", "workstream:platform", "type:feature")),
            self._issue(3, labels=("cowork:proposal", "workstream:platform", "type:bug", "claude-implement")),
        )
        assert rows["counts"] == {"queue": 1, "hold": 1, "skip": 1, "repair": 0}
        assert sum(rows["counts"].values()) == len(rows["planned"])


class TestMigrateProposalsApply:
    """The verbs. This is the half that is not allowed to be wrong."""

    def _record(self, monkeypatch):
        """Pin the REST branch explicitly. `TRANSPORT` defaults to `"gh"`, so a test
        that stubs only `_api` and asserts on the calls does not exercise the path it
        thinks it does — it exercises the real `gh` CLI. That is not a hypothetical
        either: it is how `gh issue edit 7 --add-label cowork:queued` and four
        comments landed on a merged PR and had to be undone by hand."""
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        calls: list[tuple[str, str]] = []

        def fake_api(method, path, body=None):
            calls.append((method, path))
            return setup.ApiResult(True, {}, "")

        monkeypatch.setattr(setup, "_api", fake_api)
        return calls

    def test_it_never_replaces_a_label_set(self, monkeypatch):
        """`PUT /issues/{n}/labels` replaces the whole set. That is how #172 lost
        `cowork:proposal`, `workstream:web-ux` and `type:security` in one call and
        then ran an implement job with no charter. "Keep every write-up" is
        enforced by the absence of the verb, not by intent."""
        calls = self._record(monkeypatch)
        setup._reclassify(7, "platform", repair=False)
        assert not any(method == "PUT" for method, _ in calls), calls

    def test_it_adds_before_it_removes(self, monkeypatch):
        """A run that dies in between leaves both labels on, which every reader
        resolves as queued — the harmless direction. Removing first would leave the
        issue in neither queue: invisible to the digest and to every sweep."""
        calls = self._record(monkeypatch)
        setup._reclassify(7, "platform", repair=False)
        verbs = [method for method, path in calls if "/labels" in path]
        assert verbs == ["POST", "DELETE"], calls

    def test_it_never_closes_an_issue_or_edits_a_body(self, monkeypatch):
        """Closing is a rejection to both dedupe passes; a PATCH would rewrite the
        write-up the reclassification exists to preserve."""
        calls = self._record(monkeypatch)
        setup._reclassify(7, "platform", repair=False)
        assert {method for method, _ in calls} <= {"POST", "DELETE"}
        assert all("/comments" in path or "/labels" in path for _, path in calls), calls

    def test_it_leaves_a_comment_naming_what_happened(self, monkeypatch):
        """Fixed wording so it is greppable, and so a bounced item's history reads
        straight afterwards."""
        calls = self._record(monkeypatch)
        setup._reclassify(7, "platform", repair=False)
        assert any(path.endswith("/comments") for _, path in calls)
        assert "Reclassified in place" in setup.MIGRATION_NOTE
        assert "Nothing above has changed" in setup.MIGRATION_NOTE

    def test_a_repair_only_removes(self, monkeypatch):
        """The issue already carries the queue label and already has its comment.
        Adding both again would be a duplicate comment on every retry."""
        calls = self._record(monkeypatch)
        setup._reclassify(7, "platform", repair=True)
        assert [method for method, _ in calls] == ["DELETE"], calls

    def test_it_writes_over_gh_too_not_only_rest(self, monkeypatch):
        """The read half asks REST on both transports deliberately; the write half
        had only the REST branch.

        So on a laptop with `gh` logged in and no `GH_TOKEN` exported — the
        ordinary way a human runs this, and the only way it is *allowed* to be run —
        all forty-five issues read back fine and every single write failed with "no
        GH_TOKEN in the environment". The command reported a plan it had not
        applied.
        """
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        calls: list[tuple[str, ...]] = []

        def fake_gh(*args):
            calls.append(args)
            return subprocess.CompletedProcess(list(args), 0, "", "")

        monkeypatch.setattr(setup.transport, "gh", fake_gh)
        ok, _ = setup._reclassify(7, "platform", repair=False)
        assert ok
        verbs = [args[1] for args in calls]
        assert verbs == ["edit", "comment", "edit"], calls
        assert "--add-label" in calls[0] and "--remove-label" in calls[-1]
        assert not any("api" in args for args in calls), "the gh branch must not reach for gh api"

    def test_the_gh_branch_adds_before_it_removes(self, monkeypatch):
        """Same ordering invariant as the REST branch, and for the same reason: a
        crash between the two leaves both labels on, which every reader resolves as
        queued. Removing first leaves the issue in neither queue."""
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            setup.transport,
            "gh",
            lambda *a: (calls.append(a), subprocess.CompletedProcess(list(a), 0, "", ""))[1],
        )
        setup._reclassify(7, "platform", repair=False)
        flags = [
            flag
            for args in calls
            for flag in args
            if flag.startswith("--add-label") or flag.startswith("--remove-label")
        ]
        assert flags == ["--add-label", "--remove-label"], calls

    def test_a_strict_run_refuses_to_apply(self):
        """Reclassifying forty issues on a mechanical rule is a judgement about a
        backlog. The fleet reclassifies one at a time, having read it. `--strict`
        is the flag no human passes and every unattended caller does."""
        assert setup.main(["--migrate-proposals", "--yes", "--strict"]) == 2


class TestBlockedReport:
    """Whether a standing fault has already been reported.

    This exists because `cd-deploy`'s say-it-once rule read the last 24 hours of
    Slack, and a merge fires the routine twice: GitHub sends a `push` event for a
    branch deletion as well as for a commit, every PR deletes its head branch on
    merge, and the webhook filters neither out. Both sessions read the channel
    before either has posted, both see nothing, and both post. Channel history
    cannot dedup concurrent runs — an issue, which exists before either session
    looks, can.
    """

    MARKER = "cd-deploy: RemoteTrigger absent from the routine session"

    # Every marker `cd-deploy.md` is allowed to use. Exact whole-title equality is
    # what the query does, so a marker reworded in the prose is a fault that posts
    # and files all over again — these strings are the contract.
    MARKERS = (
        "cd-deploy: RemoteTrigger absent from the routine session",
        "cd-deploy: repo variables refused by the egress proxy",
    )

    @staticmethod
    def _issue(number: int, title: str, **extra) -> dict:
        return {"number": number, "title": title, "html_url": f"https://gh/i/{number}", **extra}

    def _serve(self, monkeypatch, items, ok=True, error=""):
        monkeypatch.setattr(setup, "TRANSPORT", "api")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        seen: list[str] = []

        def paged(path, key=None):
            seen.append(path)
            return setup.ApiResult(ok, items, error)

        monkeypatch.setattr(setup.transport, "api_paged", paged)
        return seen

    def _titled(self, number: int = 7, **extra) -> dict:
        return self._issue(number, f"{setup.BLOCKED_TITLE}{self.MARKER}", **extra)

    def test_an_open_report_silences_the_routine(self, monkeypatch):
        self._serve(monkeypatch, [self._titled()])
        answer = setup.blocked_report(self.MARKER)
        assert answer["reported"] is True
        assert answer["issue"]["number"] == 7

    def test_nothing_open_is_the_one_answer_that_posts(self, monkeypatch):
        self._serve(monkeypatch, [self._issue(1, "[bug][platform] something else")])
        answer = setup.blocked_report(self.MARKER)
        assert answer["reported"] is False
        assert answer["issue"] is None

    def test_an_unreadable_query_is_null_and_never_false(self, monkeypatch):
        """The failure that matters. `False` means "nobody has said it" and
        produces a post; turning "could not ask" into it is how a standing fault
        posts once per push forever — the exact bug this replaced."""
        self._serve(monkeypatch, None, ok=False, error="403 from the egress proxy")
        answer = setup.blocked_report(self.MARKER)
        assert answer["reported"] is None
        assert answer["reported"] is not False
        assert "403" in answer["error"]

    def test_a_pull_request_is_not_a_report(self, monkeypatch):
        """`/issues` returns PRs too. A PR titled after the fault it fixes would
        silence the routine from the moment somebody starts fixing it until they
        merge — which is precisely when the fault is still happening."""
        self._serve(monkeypatch, [self._titled(pull_request={"url": "…"})])
        assert setup.blocked_report(self.MARKER)["reported"] is False

    def test_a_different_marker_is_a_different_fault(self, monkeypatch):
        self._serve(monkeypatch, [self._titled()])
        assert setup.blocked_report("cd-deploy: something else entirely")["reported"] is False

    def test_the_oldest_report_is_the_one_named(self, monkeypatch):
        """Two reports means the dedup already leaked once; naming the newest
        would send a reader to the duplicate rather than the thread with the
        history on it."""
        self._serve(monkeypatch, [self._titled(41), self._titled(12)])
        assert setup.blocked_report(self.MARKER)["issue"]["number"] == 12

    def test_no_slug_cannot_be_read_as_unreported(self, monkeypatch):
        monkeypatch.setattr(setup, "repo_slug", lambda: "")
        assert setup.blocked_report(self.MARKER)["reported"] is None

    def test_a_proxy_html_page_over_gh_is_null(self, monkeypatch):
        """The routine sessions' failure shape: `gh` is installed and answers,
        but an egress proxy returns HTML instead of JSON."""
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            setup.transport, "gh", lambda *a: subprocess.CompletedProcess(list(a), 0, "<html>nope</html>", "")
        )
        assert setup.blocked_report(self.MARKER)["reported"] is None

    def test_every_state_exits_zero(self, monkeypatch, capsys):
        """Already-reported is the common and *desired* outcome. A routine
        branching on the exit status would read a working silence as a failure."""
        for items, expected in ((([self._titled()]), True), ([], False)):
            self._serve(monkeypatch, items)
            assert setup.report_blocked(self.MARKER) == 0
            assert json.loads(capsys.readouterr().out)["reported"] is expected

    def test_the_gh_transport_reads_past_the_first_page(self, monkeypatch):
        """The bug that would have shipped. `gh api` stops at thirty items and
        this query has no label filter to bound it, so an open report on page two
        reads back as "nobody has said it" — the routine then posts *and* files a
        duplicate, which is this whole gate failing in the only direction that
        matters. `open_proposals` gets away with one page because two labels bound
        it to a handful; this one is bounded only by the repo's open-issue count.
        """
        monkeypatch.setattr(setup, "TRANSPORT", "gh")
        monkeypatch.setattr(setup, "repo_slug", lambda: "o/r")
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
        calls: list[tuple[str, ...]] = []

        def gh(*args):
            calls.append(args)
            return subprocess.CompletedProcess(list(args), 0, json.dumps([self._titled(9)]), "")

        monkeypatch.setattr(setup.transport, "gh", gh)
        assert setup.blocked_report(self.MARKER)["reported"] is True
        assert "--paginate" in calls[0], f"the gh read is capped at one page: {calls[0]}"

    def test_an_empty_marker_is_refused_not_asked(self, monkeypatch, capsys):
        """`[blocked] ` alone matches nothing, so it answers `false` forever and
        posts forever while looking like a working call."""
        self._serve(monkeypatch, [])
        assert setup.report_blocked("   ") == 2
        assert "needs a marker" in capsys.readouterr().err

    def test_the_cli_flag_reaches_the_report(self, monkeypatch, capsys):
        """Covers the argparse wiring and the dispatch order — an `if` on
        truthiness rather than `is not None` sends `--blocked-report ""` into the
        full setup run instead of erroring."""
        self._serve(monkeypatch, [self._titled()])

        def ready():
            # `main()` resets TRANSPORT before dispatching, so the stub has to
            # pick one the way the real `github_ready()` does — otherwise this
            # asserts against the `gh` branch by accident.
            setup.TRANSPORT = "api"
            return True

        monkeypatch.setattr(setup, "github_ready", ready)
        assert setup.main(["--blocked-report", self.MARKER]) == 0
        assert json.loads(capsys.readouterr().out)["reported"] is True

    def test_the_routine_names_every_marker_and_no_others(self):
        """The prose and the query are pinned together: a reworded marker in
        `cd-deploy.md` is a new fault that starts posting again. Both directions
        are checked — a marker dropped from the routine, and one invented there
        without being recorded here."""
        routine = (setup.COWORK / "routines" / "cron" / "cd-deploy.md").read_text(encoding="utf-8")
        assert "--blocked-report" in routine, "cd-deploy.md no longer asks the dedup question"
        for marker in self.MARKERS:
            assert marker in routine, f"cd-deploy.md no longer names the marker {marker!r}"
        found = set(re.findall(r"`(cd-deploy: [^`]+)`", routine))
        assert found == set(self.MARKERS), f"cd-deploy.md's marker vocabulary drifted: {found}"

    def test_the_report_is_not_labelled_as_a_proposal(self):
        """`cowork:proposal` is what `open_proposals` counts against
        `PROPOSAL_CAP`, so labelling a standing fault with it would park one of
        the platform workstream's two slots forever — and its approval verb is
        `claude-implement`, which fires an implement job on an issue no code
        change resolves."""
        routine = (setup.COWORK / "routines" / "cron" / "cd-deploy.md").read_text(encoding="utf-8")
        gate = routine[routine.index("Say it once") : routine.index("## If `RemoteTrigger`")]
        assert "`type:bug`" in gate and "`workstream:platform`" in gate
        assert "Not `cowork:proposal`" in gate, "the gate no longer says which label it must not use"


class TestLinearLabels:
    """What `/cowork deploy` creates on the Linear team, derived rather than typed.

    The proposal queue's labels are GitHub-only, and shipping them to Linear would
    put `claude-implement` on a board where nothing reads it. The one non-workstream
    label that does belong there is the disclosure approval: a disclosure-class find
    has no GitHub issue by construction, so `claude-implement` cannot be its verb.
    """

    def test_it_carries_every_workstream_and_nothing_from_the_queue(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)  # no gh call
        names = setup.manifest()["linear_labels"]
        workstreams = {label.name for label in setup.expected_labels() if label.name.startswith("workstream:")}
        assert workstreams <= set(names)
        for queue_only in ("cowork", "cowork:proposal", "claude-implement"):
            assert queue_only not in names, f"{queue_only} is GitHub-only — Linear has no proposal queue"
        assert not [n for n in names if n.startswith("type:")]

    def test_the_disclosure_approval_is_there(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)  # no gh call
        assert "security:approved" in setup.manifest()["linear_labels"]
        assert setup.LINEAR_ONLY_LABELS == ("security:approved",)

    def test_the_relay_and_the_setup_agree_on_its_name(self):
        """Two files apply and drain this label; a typo in either is a silent no-op."""
        relay_src = (setup.REPO_ROOT / "scripts" / "cowork_relay.py").read_text(encoding="utf-8")
        assert 'SECURITY_APPROVED_LABEL = "security:approved"' in relay_src
        sweep = (setup.ROUTINES_DIR / "cron" / "security-sweep.md").read_text(encoding="utf-8")
        assert "security:approved" in sweep, "the sweep must drain the label the relay applies"


class TestTheAgentsExampleCannotBeCopied:
    """The 2026-08-13 digest, made unrepeatable.

    `cron/agents-standup.md` showed a fully-worked example message. Its footer read
    "Local-session coverage is partial here: this run saw trackers only." — an
    illustrative sentence, not engine output. The scribe copied it verbatim into a
    real post that had just reported one session, so the message contradicted
    itself two lines apart, and the string existed exactly once in the whole repo:
    in that example.

    The fix is that no value in the shape is plausible enough to reuse. This test
    is what keeps it that way, because the next person to make the example
    "clearer" will make it copyable again.
    """

    ROUTINE = setup.ROUTINES_DIR / "cron" / "agents-standup.md"
    DIVIDER = "─"

    def _slack_lines(self) -> list[str]:
        import re

        blocks = re.findall(r"```slack\n(.*?)```", self.ROUTINE.read_text(encoding="utf-8"), re.S)
        assert blocks, "agents-standup.md shows no message shape at all"
        return [line.strip() for block in blocks for line in block.splitlines() if line.strip()]

    def test_every_value_in_the_shape_is_a_placeholder(self):
        for line in self._slack_lines():
            if set(line) <= {self.DIVIDER}:
                continue
            assert "<" in line, f"this line could be posted verbatim as if it were data: {line}"

    def test_the_sentence_that_leaked_is_not_in_the_shape(self):
        """Scoped to the ```slack blocks on purpose. The prose above them quotes the
        sentence while explaining what it did, which is documentation; the failure
        was that it sat somewhere a model reads as something to write."""
        for line in self._slack_lines():
            assert "coverage is partial" not in line
            assert "saw trackers only" not in line

    def test_the_footer_is_pinned_to_the_engine_field(self):
        text = self.ROUTINE.read_text(encoding="utf-8")
        assert "digest.coverage_notes" in text
        assert "omitted" in text and "empty" in text, "an empty coverage array must produce no footer at all"

    def test_the_run_is_tracker_only_and_says_why(self):
        text = self.ROUTINE.read_text(encoding="utf-8")
        assert "--no-local-sessions" in text
        assert "sessions_worked" in text, "the routine must say the zero is by construction"

    def test_warnings_are_required_to_reach_the_post(self):
        assert "digest.warnings" in self.ROUTINE.read_text(encoding="utf-8")


class TestEveryRunChecksIn:
    """`cowork/check-in.md` is the last step of every routine, and a routine that
    quietly stops carrying it fails in the most misleading way available: the
    check-in is what says the run happened, so its absence reads as the run having
    *died*, and `cron/shipped-standup.md` names it a no-show every evening for
    what is really a missing line in a markdown file. Nothing at run time would
    say otherwise, which is why the totality check is the feature.
    """

    def test_no_routine_is_missing_its_check_in(self):
        missing = setup.routines_without_check_in()
        assert missing == [], (
            "these routines never check in — add the final step: "
            f"follow cowork/{setup.CHECK_IN_DOC}. Missing: {missing}"
        )

    def test_a_routine_that_drops_the_step_is_caught(self, tmp_path, monkeypatch):
        """The negative half. A check that cannot fail is not a check."""
        routines = tmp_path / "routines" / "cron"
        routines.mkdir(parents=True)
        (routines / "made-up.md").write_text("# made up\n\n## Run\n\n1. Do a thing.\n", encoding="utf-8")
        monkeypatch.setattr(setup, "ROUTINES_DIR", tmp_path / "routines")
        assert setup.routines_without_check_in() == ["cron/made-up.md"]

    def test_citing_the_sweep_procedure_is_not_delegating_to_it(self, tmp_path, monkeypatch):
        """`cron/digest.md` and `cron/integrations-campaign.md` both mention
        sweep-procedure.md while running their own steps. A rule that let a
        citation stand in for delegation would exempt them, silently."""
        routines = tmp_path / "routines" / "cron"
        routines.mkdir(parents=True)
        (routines / "citer.md").write_text(
            "# citer\n\nUnlike sweep-procedure.md, this one is different.\n\n## Run\n\n1. Do a thing.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(setup, "ROUTINES_DIR", tmp_path / "routines")
        assert setup.routines_without_check_in() == ["cron/citer.md"]

    def test_a_sweep_inherits_the_step_from_the_shared_procedure(self, tmp_path, monkeypatch):
        """The thirteen sweeps have no `## Run` of their own — one step in
        sweep-procedure.md covers all of them."""
        routines = tmp_path / "routines" / "cron"
        routines.mkdir(parents=True)
        (routines / "x-sweep.md").write_text(
            "# x sweep\n\nFollow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = x`.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(setup, "ROUTINES_DIR", tmp_path / "routines")
        assert setup.routines_without_check_in() == []

    def test_the_shared_procedure_is_what_they_inherit(self):
        """If sweep-procedure.md stops checking in, thirteen routines stop with it
        and none of their own files changes."""
        assert setup.CHECK_IN_DOC in (setup.COWORK / setup.SWEEP_DOC).read_text(encoding="utf-8")

    def test_the_contract_it_points_at_exists(self):
        assert (setup.COWORK / setup.CHECK_IN_DOC).exists()


class TestRunReport:
    """`--runs` renders a `RemoteTrigger list_runs` response. It is the only thing
    in the fleet that reads *runs* rather than routines, and it is what answers
    "did Thursday's sweep fire at all" — a question that had no answer before.
    """

    @staticmethod
    def _run(run_id: str, routine: str = "security-sweep", **over) -> dict:
        entry = {
            "id": run_id,
            "title": f"⚡ cowork: {routine}",
            "status": "active",
            "worker_status": "idle",
            "created_at": "2026-08-13T06:05:28.192854Z",
            "last_event_at": "2026-08-13T06:22:05.292981Z",
            "url": f"https://claude.ai/code/session_{run_id[len('cse_') :]}",
        }
        entry.update(over)
        return entry

    def test_reads_a_real_recorded_response(self):
        """The one input in this class the test did not invent.

        Everything else here is built from the same assumption the parser makes,
        so a field named differently in the real API would leave those green while
        `/cowork runs` printed "No runs" — which reads as "this routine never
        fired", the wrong diagnosis said loudly.
        """
        live = json.loads((ROOT / "tests" / "fixtures" / "cowork_runs_live.json").read_text())
        report = setup.run_report([live])
        assert len(report["runs"]) == 3
        assert {row["routine"] for row in report["runs"]} == {"security-sweep"}
        for row in report["runs"]:
            assert row["url"] == f"https://claude.ai/code/session_{row['id'][len('cse_') :]}", (
                "cowork/check-in.md builds this run's link from CLAUDE_CODE_REMOTE_SESSION_ID by "
                "this exact rule — if the API stops agreeing, every check-in links nowhere"
            )
        # 06:05:28.192854 → 06:22:05.292981
        assert report["runs"][0]["duration_seconds"] == 997

    def test_reads_the_live_envelope(self):
        report = setup.run_report([{"data": [self._run("cse_A")]}])
        assert [row["routine"] for row in report["runs"]] == ["security-sweep"]
        assert report["runs"][0]["duration_seconds"] == 997
        assert report["runs"][0]["url"].endswith("session_A")

    def test_dedupes_a_run_read_twice(self):
        """A caller saves one file per routine, and asking for one twice is normal.

        Keyed on run id, because the pages are independent reads and position
        proves nothing about identity.
        """
        report = setup.run_report([{"data": [self._run("cse_A")]}, {"data": [self._run("cse_A")]}])
        assert len(report["runs"]) == 1

    def test_filters_to_one_routine(self):
        payload = {"data": [self._run("cse_A"), self._run("cse_B", routine="poker-sweep")]}
        assert [r["routine"] for r in setup.run_report([payload], name="poker-sweep")["runs"]] == ["poker-sweep"]

    def test_an_empty_history_does_not_read_as_proof(self):
        """A fire refused before a session existed leaves no row, so "no runs" is
        equally what a paused or unregistered routine looks like. The line has to
        say so — reporting it as "never ran" is the wrong diagnosis, loudly."""
        lines = setup.run_report([{"data": []}])["lines"]
        assert len(lines) == 1
        assert "paused or unregistered" in lines[0]

    def test_unreadable_timestamps_do_not_crash_the_report(self):
        """A field this has never seen should read as unknown, not raise. A doctor
        that dies on an unfamiliar payload reports nothing about the rest of it."""
        report = setup.run_report([{"data": [self._run("cse_A", created_at="", last_event_at="")]}])
        assert report["runs"][0]["duration_seconds"] == 0
        assert report["lines"][0].startswith("?")

    def test_a_bare_array_is_accepted_too(self):
        assert len(setup.run_report([[self._run("cse_A")]])["runs"]) == 1

    def test_times_render_in_the_display_zone(self):
        """Same zone as the agenda: a run at 06:05 UTC is 07:05 in London in August,
        and two renderings of one instant are two runs to anybody reading."""
        line = setup.run_report([{"data": [self._run("cse_A")]}])["lines"][0]
        assert "07:05" in line


class TestAScopedShellCanStillCheckIn:
    """The check-in command is named in `cowork/check-in.md`, never in a routine's
    own file — so a scoped `Bash` grant that omits it reads as complete right up
    until the run tries it, and `check-in.md` makes that failure silent by design.
    `slack-relay` would have been reported as a no-show seventeen times a day for
    a missing string in a tuple.
    """

    def test_every_scoped_routine_is_granted_the_check_in_script(self):
        offenders = []
        for routine in ROUTINES:
            tools = set(setup.routine_tools(routine.name))
            scoped = {tool for tool in tools if tool.startswith("Bash(")}
            if scoped and not any(setup.CHECK_IN_SCRIPT in tool for tool in scoped):
                offenders.append(routine.name)
        assert offenders == [], f"scoped shells that cannot run the check-in: {offenders}"

    def test_the_doctor_catches_one_that_is_not(self, monkeypatch):
        """The negative half, against the real `check_grants`."""
        monkeypatch.setitem(setup.TOOL_OVERRIDES, "slack-relay", ("Bash(gh issue view:*)", "Read"))
        report = setup.Report()
        setup.check_grants(report, [r for r in ROUTINES if r.name == "slack-relay"])
        assert any("cannot run the check-in" in problem for problem in report.problems)

    def test_a_bare_shell_needs_no_grant(self, monkeypatch):
        """`Bash` already covers everything; only a scoped grant has to enumerate."""
        monkeypatch.setitem(setup.TOOL_OVERRIDES, "cd-deploy", ("Bash", "Read"))
        report = setup.Report()
        setup.check_grants(report, [r for r in ROUTINES if r.name == "cd-deploy"])
        assert not any("cannot run the check-in" in problem for problem in report.problems)
