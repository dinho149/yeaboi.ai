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
and ``--check --local`` skips the remote half by design.
"""

from __future__ import annotations

import calendar
import importlib.util
import json
import re
import shutil
import subprocess
import sys
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
        """The six non-sweeps carry their own ``**Model**`` line; it must agree.

        The fourteen sweeps deliberately have none — they take their tier from
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
                "cron/marketing-weekly.md",
                "cron/slack-relay.md",
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
    """The payload and the rendered message the day-ahead routine posts."""

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

    def test_the_posting_routine_is_in_the_payload_and_not_in_the_message(self):
        """A line announcing the message you are holding is the one line in a
        four-line post that says nothing. It stays in the payload, which is the
        fleet's schedule and does include it."""
        payload = setup.agenda(date(2026, 8, 16))
        assert setup.MESSENGER in {entry["name"] for entry in payload["today"]}
        assert setup.MESSENGER in payload["daily"]
        blob = "\n".join(payload["lines"])
        assert setup.MESSENGER not in blob

    def test_the_payload_is_json_serialisable(self):
        assert json.loads(json.dumps(setup.agenda(date(2026, 8, 13))))

    def test_the_lines_meet_the_scribes_format_contract(self):
        """`.claude/agents/cowork-scribe.md`: no emoji headers, no bare URLs."""
        lines = setup.agenda(date(2026, 8, 13))["lines"]
        blob = "\n".join(lines)
        assert "http" not in blob, "the schedule links to nothing; a bare URL would be the scribe's rule broken"
        assert not re.search(r"[\U0001F300-\U0001FAFF✀-➿☀-⛿]", blob), "no emoji"
        assert lines[0].startswith("*Today* —"), lines[0]

    def test_every_listed_routine_reaches_the_rendered_lines(self):
        """A payload entry the renderer drops is a routine that runs unannounced."""
        payload = setup.agenda(date(2026, 8, 13))
        blob = "\n".join(payload["lines"])
        for entry in payload["today"] + payload["background"]:
            if entry["name"] == setup.MESSENGER:
                continue  # the one documented omission — see the test above
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
        assert any(line.startswith("Tue 1 Sep") for line in lines), lines


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
        assert result.stdout.startswith("*Today* —")

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
            f"{routine.path} has no `**Summary** — ...` line, so the day-ahead post "
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
        template = setup.PROMPT_TEMPLATE.format(name="<name>", path="cron/<file>.md")
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
            {"cowork", "cowork:proposal", "claude-implement", "feedback-override"}
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
        for kind in setup.PROPOSAL_TYPES:
            if kind == "other":
                continue
            assert {kind, f"{kind}s"} & sections, f"digest.md's section order omits type:{kind}"

    def test_there_are_fifteen_workstreams(self):
        # The count is load-bearing: CLAUDE.md, cowork/README.md and the digest's
        # health check all say fifteen.
        assert len(WORKSTREAMS) == 15

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
            "variables",
            "routines",
        }
        assert len(payload["routines"]) == len(ROUTINES)
        assert payload["connectors"] == ["Linear", "Slack", "Notion"]
        assert "Task" in payload["default_allowed_tools"], "a sweep spawns the crew agents"

    def test_every_cron_routine_carries_what_registration_needs(self, monkeypatch):
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        for routine in setup.manifest()["routines"]:
            if routine["kind"] != "cron":
                continue
            assert routine["cron"] and routine["model"] and routine["prompt"] and routine["trigger_name"]


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
    """What a fleet that exactly matches the repo would look like on the wire."""
    routines = [r for r in (routines or ROUTINES) if r.kind == "cron"]
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
        assert sorted(plan.ok) == sorted(r.name for r in CRON_ROUTINES)
        assert plan.create == [] and plan.update == [] and plan.orphans == []

    def test_every_registered_routine_yields_a_url(self):
        plan = setup.trigger_plan(_perfect_snapshot())
        assert len(plan.urls) == len(CRON_ROUTINES)
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
        assert len(plan.update) == len(CRON_ROUTINES)
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
        assert len(plan.create) == len(CRON_ROUTINES)
        assert plan.ok == [] and plan.urls == {} and plan.needs == []

    def test_a_first_deploy_names_what_only_the_account_can_supply(self):
        """The API accepts an empty string, so nothing downstream would notice.

        Eighteen routines register pointing at no repository, on no environment,
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

    def test_the_event_routines_are_never_planned(self):
        """RemoteTrigger takes a cron expression only — there is no event field."""
        planned = {action.name for action in setup.trigger_plan([]).create}
        assert not planned & {r.name for r in ROUTINES if r.kind == "event"}

    def test_the_plan_is_json_serialisable(self):
        snapshot = _perfect_snapshot()
        _by_name(snapshot, "retro-sweep")["cron_expression"] = "0 0 * * 0"
        payload = json.loads(json.dumps(setup.trigger_plan(snapshot).as_dict()))
        assert payload["update"][0]["fields"]["cron"]["wanted"] == "30 7 5,19 * *"

    def test_a_bare_array_snapshot_is_accepted(self):
        """Captured either way depending on how /cowork saved the response."""
        entries = _perfect_snapshot()
        assert setup.snapshot({"data": entries}) == setup.snapshot(entries) == entries


class TestToolOverrides:
    """slack-relay is the one routine registered with RemoteTrigger.

    The relay drives pause/resume/run from inside its own session, which needs
    the tool; a *sweep* that could reach the routines API would be a sweep that
    can un-pause the fleet, so the override is per-routine and the plan treats
    any deviation — in either direction — as drift.
    """

    def _tools_of(self, body: dict) -> list[str]:
        return body["job_config"]["ccr"]["session_context"]["allowed_tools"]

    def test_the_relay_registers_with_remote_trigger(self):
        relay = next(r for r in ROUTINES if r.name == "slack-relay")
        body = setup.desired_trigger(relay, REPO_URL, ENVIRONMENT, CONNECTORS)
        assert "RemoteTrigger" in self._tools_of(body)

    def test_no_other_routine_registers_with_remote_trigger(self):
        for routine in CRON_ROUTINES:
            if routine.name == "slack-relay":
                continue
            body = setup.desired_trigger(routine, REPO_URL, ENVIRONMENT, CONNECTORS)
            assert "RemoteTrigger" not in self._tools_of(body), (
                f"{routine.path} would register with RemoteTrigger — only the relay carries it"
            )

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

    def test_the_day_ahead_routine_cannot_write(self):
        """`cron/day-ahead.md` states this as a safety property — it runs one script
        and posts one message — so it is pinned rather than left as prose. Task
        stays: unlike the relay, it spawns the scribe."""
        tools = setup.routine_tools("day-ahead")
        assert not {"Write", "Edit"} & set(tools)
        assert "Task" in tools and "Bash" in tools

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
        assert len(setup.missing_urls(blank_readme)) == len(CRON_ROUTINES)

    def test_every_cron_row_gets_its_url(self, blank_readme: str):
        plan = setup.trigger_plan(_perfect_snapshot())
        filled = setup.readme_with_urls(blank_readme, plan.urls)
        assert setup.missing_urls(filled) == []
        assert f"https://claude.ai/code/routines/{_trigger_id(0)} |" in filled

    def test_the_event_rows_stay_blank(self, blank_readme: str):
        """They cannot be registered, so a URL there would be a claim, not a record."""
        filled = setup.readme_with_urls(blank_readme, setup.trigger_plan(_perfect_snapshot()).urls)
        for line in filled.splitlines():
            if line.startswith("| `events/"):
                assert line.rstrip().endswith("|  |") or line.rstrip().endswith("| |")

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
        gate. Removing either with the fleet would break a live gate, and the
        breakage is silent: applying a label that does not exist does nothing.
        """
        assert setup.KEEP_LABELS == {"claude-implement", "feedback-override"}
        assert not (setup.KEEP_LABELS & {label.name for label in setup.teardown_labels()})

    def test_everything_else_cowork_creates_is_in_scope(self):
        expected = {
            label.name
            for label in setup.expected_labels()
            if label.name not in setup.KEEP_LABELS and not label.name.startswith("type:")
        }
        assert {label.name for label in setup.teardown_labels()} == expected
        assert len(expected) == len(WORKSTREAMS) + 2

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

    ``_gh`` is the single seam every GitHub call goes through, so all of this is
    reachable with one monkeypatch and none of it touches the network.
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
        assert len(json.loads(result.stdout)["ok"]) == len(CRON_ROUTINES)

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
