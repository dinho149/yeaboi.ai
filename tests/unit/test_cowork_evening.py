"""Tests for scripts/cowork_evening.py — the per-area evening Slack renderer.

``cron/shipped-standup.md`` posts every ``posts[].lines`` block verbatim, one
channel message each, so every judgement a reader could act on is asserted here:
which areas post at all, what a post says, and — the property the fan-out
introduced — **how many messages a single run produces**. Nothing at post time
would notice a drift, and the failure modes are asymmetric: an area silently
dropped is invisible, and an area posted twice arrives as two changes that never
happened.

The evening post used to be one message grouped by *type*. Twelve workstreams
have no other voice in the channel — the maintenance sweeps post nothing, ever —
so that roll-up was the only place they were heard, and it was the place they
were least legible. That is what these tests are holding in place.

No test here touches the network: ``collect`` and ``build`` are exercised through
stubbed transport seams, and the pure renderers over hand-built data.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

# scripts/ is not a package, so load the module straight from its file path. The
# module is registered in sys.modules first: `cowork_setup` imports resolve
# through the sys.path entry above, and a dataclass in a module that is not there
# raises on definition.
_MODULE_PATH = ROOT / "scripts" / "cowork_evening.py"
_spec = importlib.util.spec_from_file_location("cowork_evening", _MODULE_PATH)
evening = importlib.util.module_from_spec(_spec)
sys.modules["cowork_evening"] = evening
_spec.loader.exec_module(evening)

import cowork_setup as setup  # noqa: E402

# The same title shape `TestSlackTemplates` pins for every routine template.
TITLE = re.compile(r"^(?P<emoji>\S+) \*\*(?P<name>[^*]+)\*\* — .+")
HEADING = re.compile(r"^\S+ \*\*[^*]+\*\*\s*\(")
DIVIDER = "───────────────────────────"

NOW = datetime(2026, 8, 16, 18, 0, tzinfo=UTC)
SINCE = NOW - timedelta(hours=24)


def pr(number: int, /, **overrides) -> dict:
    """A PR as the REST pulls endpoint returns it, trimmed to what is read."""
    labels = overrides.pop("labels", ["cowork", "workstream:analysis", "type:bug"])
    base = {
        "number": number,
        "title": f"pr {number}",
        "html_url": f"https://github.com/o/r/pull/{number}",
        "body": "",
        "labels": [{"name": name} for name in labels],
        "created_at": "2026-08-16T09:00:00Z",
        "updated_at": "2026-08-16T17:00:00Z",
        "merged_at": None,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Every read stubbed off by default. A test that wants data opts in.

    Belt and braces over the suite's own `gh` guard: this module's `_get` has two
    branches, and a test that stubbed only one would reach the network through
    the other on a machine where `gh` happens to be installed.
    """
    monkeypatch.setattr(evening, "_get", lambda path: None)
    monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")
    monkeypatch.setattr(evening, "_installable", lambda: ("3.9.0rc14", ""))
    monkeypatch.setattr(evening, "review_verdict", lambda number: ("", None))
    monkeypatch.setattr(evening, "ci_state", lambda sha: "")


def _serve(monkeypatch, *, closed=(), opened=()):
    monkeypatch.setattr(evening, "fetch_closed_since", lambda since: list(closed))
    monkeypatch.setattr(evening, "fetch_open", lambda: list(opened))


class TestGrouping:
    """The label is the join key, and the whole point of the change."""

    def test_each_area_gets_its_own_post(self, monkeypatch):
        _serve(
            monkeypatch,
            closed=[
                pr(1, labels=["cowork", "workstream:analysis", "type:bug"], merged_at="2026-08-16T14:02:00Z"),
                pr(2, labels=["cowork", "workstream:platform", "type:chore"], merged_at="2026-08-16T15:00:00Z"),
            ],
        )
        posts = evening.build(SINCE, NOW)["posts"]
        assert [post["workstream"] for post in posts] == ["analysis", "platform"]

    def test_two_merges_in_one_area_are_one_post(self, monkeypatch):
        """The failure this replaced was one post for everything. The failure it
        must not introduce is one post per PR."""
        _serve(
            monkeypatch,
            closed=[
                pr(1, merged_at="2026-08-16T14:02:00Z"),
                pr(2, merged_at="2026-08-16T15:02:00Z"),
            ],
        )
        posts = evening.build(SINCE, NOW)["posts"]
        assert len(posts) == 1
        assert posts[0]["workstream"] == "analysis"

    def test_areas_are_posted_in_a_stable_order(self, monkeypatch):
        """GitHub's page order is not stable; two runs over one day must agree."""
        merges = [
            pr(1, labels=["cowork", "workstream:web-ux"], merged_at="2026-08-16T14:00:00Z"),
            pr(2, labels=["cowork", "workstream:analysis"], merged_at="2026-08-16T15:00:00Z"),
            pr(3, labels=["cowork", "workstream:poker"], merged_at="2026-08-16T16:00:00Z"),
        ]
        _serve(monkeypatch, closed=merges)
        first = [post["workstream"] for post in evening.build(SINCE, NOW)["posts"]]
        _serve(monkeypatch, closed=list(reversed(merges)))
        assert [post["workstream"] for post in evening.build(SINCE, NOW)["posts"]] == first

    def test_a_pr_with_no_workstream_label_is_reported_not_dropped(self, monkeypatch):
        """An untagged PR is a convention miss somebody should see."""
        _serve(monkeypatch, closed=[pr(1, labels=["cowork", "type:chore"], merged_at="2026-08-16T14:00:00Z")])
        posts = evening.build(SINCE, NOW)["posts"]
        assert [post["workstream"] for post in posts] == [evening.UNCLAIMED_AREA]
        assert any("untagged, not unowned" in line for line in posts[0]["lines"])

    def test_a_ci_sentinel_merge_is_in_the_lane(self, monkeypatch):
        """`ci-sentinel.yml` labels its unattended red-`main` fixes only
        `ci-sentinel`; a `cowork`-only filter drops exactly the merges nobody
        watched."""
        _serve(monkeypatch, closed=[pr(1, labels=["ci-sentinel"], merged_at="2026-08-16T14:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"]

    def test_a_pr_outside_the_lane_is_ignored(self, monkeypatch):
        """A human's PR is not the fleet reporting on itself."""
        _serve(monkeypatch, closed=[pr(1, labels=["enhancement"], merged_at="2026-08-16T14:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"] == []

    def test_a_merge_before_the_window_is_ignored(self, monkeypatch):
        """A double-reported merge reads as a second change that never happened."""
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-10T14:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"] == []

    def test_a_closed_unmerged_pr_is_ignored(self, monkeypatch):
        _serve(monkeypatch, closed=[pr(1, merged_at=None)])
        assert evening.build(SINCE, NOW)["posts"] == []


class TestWhatFiresAPost:
    """A change fires a post; a state does not.

    Without this an open PR re-announces itself every evening until it merges,
    which is how a channel gets muted — and a muted channel is worse than no
    channel, because the one day it matters nobody looks.
    """

    def test_a_merge_fires(self, monkeypatch):
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"]

    def test_a_pr_opened_today_fires(self, monkeypatch):
        _serve(monkeypatch, opened=[pr(1, created_at="2026-08-16T09:00:00Z")])
        posts = evening.build(SINCE, NOW)["posts"]
        assert posts and "🔨 **Building** (1)" in posts[0]["lines"]

    def test_a_pr_quietly_building_does_not_fire(self, monkeypatch):
        _serve(monkeypatch, opened=[pr(1, created_at="2026-08-13T09:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"] == []

    def test_crossing_into_stuck_fires_once(self, monkeypatch):
        """On the day it crosses, and not on the days after it."""
        crossing = NOW - timedelta(days=evening.STUCK_DAYS)
        _serve(monkeypatch, opened=[pr(1, created_at=crossing.isoformat())])
        assert evening.build(SINCE, NOW)["posts"]

        older = NOW - timedelta(days=evening.STUCK_DAYS + 3)
        _serve(monkeypatch, opened=[pr(1, created_at=older.isoformat())])
        assert evening.build(SINCE, NOW)["posts"] == []

    def test_a_quietly_building_pr_still_shows_inside_a_post_that_fired(self, monkeypatch):
        """It does not fire one; it is not hidden from one."""
        _serve(
            monkeypatch,
            closed=[pr(1, merged_at="2026-08-16T14:00:00Z")],
            opened=[pr(2, created_at="2026-08-13T09:00:00Z")],
        )
        lines = evening.build(SINCE, NOW)["posts"][0]["lines"]
        assert "🔨 **Building** (1)" in lines

    def test_a_stuck_pr_is_not_reported_as_building(self, monkeypatch):
        older = NOW - timedelta(days=evening.STUCK_DAYS + 3)
        _serve(
            monkeypatch,
            closed=[pr(1, merged_at="2026-08-16T14:00:00Z")],
            opened=[pr(2, created_at=older.isoformat())],
        )
        lines = evening.build(SINCE, NOW)["posts"][0]["lines"]
        assert "🚧 **Stuck** (1)" in lines
        assert not any(line.startswith("🔨") for line in lines)


class TestTheRenderedBlock:
    """Every rule the Slack dialect imposes, checked on the real output."""

    def _lines(self, monkeypatch, **kwargs) -> list[str]:
        _serve(monkeypatch, **kwargs)
        return evening.build(SINCE, NOW)["posts"][0]["lines"]

    def test_the_first_line_is_a_title_line(self, monkeypatch):
        lines = self._lines(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert TITLE.match(lines[0])
        assert not HEADING.match(lines[0]), "a title line must not wear a section heading's (n)"

    def test_the_title_carries_the_areas_glyph_and_display_name(self, monkeypatch):
        lines = self._lines(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert lines[0].startswith(f"{setup.parse_workstream_glyphs()['analysis']} **Analysis** — ")

    def test_the_display_name_is_the_tables_not_the_slug(self, monkeypatch):
        """Title-casing `tui-ux` gives "Tui Ux", which is what a reader would
        have met in the channel every week."""
        lines = self._lines(
            monkeypatch,
            closed=[pr(1, labels=["cowork", "workstream:tui-ux"], merged_at="2026-08-16T14:00:00Z")],
        )
        assert "**Terminal UI**" in lines[0]

    def test_no_line_carries_slack_mrkdwn_emphasis(self, monkeypatch):
        """The dialect is standard Markdown — `*bold*` renders as italics."""
        lines = self._lines(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        for line in lines:
            assert not re.search(r"(?<!\*)\*(?!\*)", line), line

    def test_sections_are_separated_by_a_divider_and_never_trailed_by_one(self, monkeypatch):
        """Slack eats the blank line that ends a `1.` list, so a heading written
        after one arrives glued to the final item. A divider is not blank and
        survives — and a trailing one is a rule under the last line."""
        lines = self._lines(
            monkeypatch,
            closed=[pr(1, merged_at="2026-08-16T14:00:00Z")],
            opened=[pr(2, created_at="2026-08-16T09:00:00Z")],
        )
        assert DIVIDER in lines
        assert lines[-1] != DIVIDER

    def test_an_empty_section_is_omitted_heading_and_all(self, monkeypatch):
        lines = self._lines(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert not any("Building" in line or "Stuck" in line for line in lines)

    def test_the_footer_installs_the_tag_backed_prerelease(self, monkeypatch):
        lines = self._lines(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert lines[-1] == "`pip install --pre yeaboi==3.9.0rc14`"
        assert "→ `3.9.0rc14`" in lines[0]

    def test_nothing_published_says_so_rather_than_naming_a_stale_version(self, monkeypatch):
        monkeypatch.setattr(evening, "_installable", lambda: ("", ""))
        lines = self._lines(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert lines[0].endswith("→ no new pre-release")
        assert not any(line.startswith("`pip install") for line in lines)

    def test_a_time_that_cannot_be_read_renders_as_no_clause(self, monkeypatch):
        assert evening._clock("not-a-timestamp", None) == ""
        assert evening._clock(None, None) == ""

    def test_a_regression_run_is_claimed_only_for_a_bug_that_shows_one(self, monkeypatch):
        """A `type:bug`'s admission ticket is a test that fails before and passes
        after; the clause says it is there, and says nothing when it is not."""
        shown = self._lines(
            monkeypatch,
            closed=[pr(1, body="the regression test fails before the fix", merged_at="2026-08-16T14:00:00Z")],
        )
        assert any("regression test added" in line for line in shown)

        bare = self._lines(monkeypatch, closed=[pr(1, body="", merged_at="2026-08-16T14:00:00Z")])
        assert not any("regression test added" in line for line in bare)

    def test_a_chore_never_claims_a_regression_run(self, monkeypatch):
        lines = self._lines(
            monkeypatch,
            closed=[
                pr(
                    1,
                    labels=["cowork", "workstream:analysis", "type:chore"],
                    body="the regression test fails before the fix",
                    merged_at="2026-08-16T14:00:00Z",
                )
            ],
        )
        assert not any("regression test added" in line for line in lines)

    def test_a_pr_with_no_type_label_ships_untagged_rather_than_unmentioned(self, monkeypatch):
        lines = self._lines(
            monkeypatch,
            closed=[pr(1, labels=["cowork", "workstream:analysis"], merged_at="2026-08-16T14:00:00Z")],
        )
        assert any("/pull/1)" in line for line in lines)
        assert not any("[]" in line for line in lines)


class TestReviewVerdict:
    """Read or omitted, never invented — and never the wrong one."""

    def _verdict(self, monkeypatch, reviews):
        """The verdict half only — the timestamp half has its own tests below."""
        monkeypatch.undo()
        monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")
        monkeypatch.setattr(evening, "_get", lambda path: reviews)
        return evening.review_verdict(1)[0]

    def test_an_approval_reads_clean(self, monkeypatch):
        assert self._verdict(monkeypatch, [{"state": "APPROVED", "user": {"login": "a"}}]) == "review clean"

    def test_an_approval_after_changes_requested_is_a_resolved_round(self, monkeypatch):
        """Counting both would report a clean PR as contested."""
        reviews = [
            {"state": "CHANGES_REQUESTED", "user": {"login": "a"}},
            {"state": "APPROVED", "user": {"login": "a"}},
        ]
        assert self._verdict(monkeypatch, reviews) == "review clean"

    def test_an_outstanding_objection_survives_someone_elses_approval(self, monkeypatch):
        reviews = [
            {"state": "APPROVED", "user": {"login": "a"}},
            {"state": "CHANGES_REQUESTED", "user": {"login": "b"}},
        ]
        assert self._verdict(monkeypatch, reviews) == "changes requested"

    def test_no_review_is_no_clause(self, monkeypatch):
        assert self._verdict(monkeypatch, []) == ""

    def test_an_unreadable_response_is_no_clause(self, monkeypatch):
        assert self._verdict(monkeypatch, None) == ""

    def test_a_comment_only_review_is_not_an_approval(self, monkeypatch):
        assert self._verdict(monkeypatch, [{"state": "COMMENTED", "user": {"login": "a"}}]) == ""


class TestBlindness:
    """A quiet evening and a blind one arrive looking identical.

    None is never an empty list anywhere in this module: a page reported empty
    when it could not be asked renders as a quiet day, which is the one thing
    this post must not invent.
    """

    def test_an_unreadable_merge_list_is_a_warning(self, monkeypatch):
        monkeypatch.setattr(evening, "fetch_closed_since", lambda since: None)
        monkeypatch.setattr(evening, "fetch_open", lambda: [])
        warnings = evening.build(SINCE, NOW)["payload"]["warnings"]
        assert any("could not read merged PRs" in warning for warning in warnings)

    def test_an_unreadable_open_list_is_a_separate_warning(self, monkeypatch):
        monkeypatch.setattr(evening, "fetch_closed_since", lambda since: [])
        monkeypatch.setattr(evening, "fetch_open", lambda: None)
        warnings = evening.build(SINCE, NOW)["payload"]["warnings"]
        assert any("could not read open PRs" in warning for warning in warnings)

    def test_a_full_open_page_is_blindness_not_a_short_answer(self, monkeypatch):
        """A full page is provably not the whole answer."""
        monkeypatch.setattr(evening, "_get", lambda path: [pr(n) for n in range(100)])
        assert evening.fetch_open() is None

    def test_a_walk_that_never_reaches_the_window_is_blindness(self, monkeypatch):
        """Returned short, a truncated list becomes "nothing else merged"."""
        page = [pr(n, updated_at="2026-08-16T17:00:00Z") for n in range(100)]
        monkeypatch.setattr(evening, "_get", lambda path: page)
        assert evening.fetch_closed_since(SINCE) is None

    def test_the_walk_stops_at_the_window_edge(self, monkeypatch):
        """Sorted by `updated` descending, so it does not page the whole history.
        Safe in the one direction that matters: a PR's `updated_at` is never
        earlier than its `merged_at`."""
        pages = {
            1: [pr(n, updated_at="2026-08-16T17:00:00Z") for n in range(100)],
            2: [pr(n, updated_at="2026-08-01T09:00:00Z") for n in range(100)],
        }
        seen: list[int] = []

        def fake_get(path: str):
            page = int(path.split("&page=")[1])
            seen.append(page)
            return pages.get(page, [])

        monkeypatch.setattr(evening, "_get", fake_get)
        assert len(evening.fetch_closed_since(SINCE)) == 200
        assert seen == [1, 2], "it kept paging past the window"

    def test_an_unreadable_first_page_is_blindness(self, monkeypatch):
        monkeypatch.setattr(evening, "_get", lambda path: None)
        assert evening.fetch_closed_since(SINCE) is None

    def test_an_unplaceable_merge_time_drops_the_pr_rather_than_the_window(self, monkeypatch):
        """A merge whose timestamp will not parse cannot be placed in the window,
        and the two ways to handle that are not symmetric: excluding it loses one
        line, and including it risks reporting a merge the last post already
        named — which reads as a second change that never happened."""
        _serve(monkeypatch, closed=[pr(1, merged_at="not-a-timestamp")])
        assert evening.build(SINCE, NOW)["posts"] == []


class TestFleetHealth:
    """The one failure the fleet cannot report on itself."""

    def _agenda(self, monkeypatch, entries):
        monkeypatch.setattr(
            setup,
            "agenda",
            lambda day: {"today": entries},
        )

    def test_a_routine_that_never_checked_in_is_named(self, monkeypatch):
        self._agenda(
            monkeypatch,
            [{"name": "security-sweep", "times_utc": ["06:00"], "times_local": ["07:00"]}],
        )
        health = evening.build(SINCE, NOW, checked_in=[])["health"]
        assert health["lines"][0].startswith("🩺 **Fleet health** — ")
        assert "**security-sweep** — due, never checked in" in health["lines"][-1]

    def test_a_routine_that_checked_in_is_not(self, monkeypatch):
        self._agenda(
            monkeypatch,
            [{"name": "security-sweep", "times_utc": ["06:00"], "times_local": ["07:00"]}],
        )
        assert evening.build(SINCE, NOW, checked_in=["security-sweep"])["health"] is None

    def test_a_run_due_before_the_schedule_post_is_never_a_no_show(self, monkeypatch):
        """`cd-deploy` fires at 04:00 and has no 📅 thread to reply to. Reporting
        those would put a false 🔴 in this post every morning."""
        self._agenda(
            monkeypatch,
            [{"name": "cd-deploy", "times_utc": ["04:00"], "times_local": ["05:00"]}],
        )
        assert evening.build(SINCE, NOW, checked_in=[])["health"] is None

    def test_a_run_not_yet_due_is_never_a_no_show(self, monkeypatch):
        self._agenda(
            monkeypatch,
            [{"name": "late-thing", "times_utc": ["22:00"], "times_local": ["23:00"]}],
        )
        assert evening.build(SINCE, NOW, checked_in=[])["health"] is None

    def test_no_check_in_list_at_all_means_no_health_message(self, monkeypatch):
        """A missing 📅 means the routine has nothing to diff against, and an
        empty list read as seventeen no-shows would be the loudest false alarm
        in the fleet."""
        self._agenda(
            monkeypatch,
            [{"name": "security-sweep", "times_utc": ["06:00"], "times_local": ["07:00"]}],
        )
        assert evening.build(SINCE, NOW)["health"] is None

    def test_a_name_is_matched_case_and_space_insensitively(self, monkeypatch):
        """The names come off a human-readable Slack thread, not an API."""
        self._agenda(
            monkeypatch,
            [{"name": "security-sweep", "times_utc": ["06:00"], "times_local": ["07:00"]}],
        )
        assert evening.build(SINCE, NOW, checked_in=[" Security-Sweep "])["health"] is None


class TestTheAreaGlyphContract:
    """The renderer and `cowork/README.md` must agree, in both directions."""

    def test_every_workstream_has_a_glyph_and_a_name(self):
        workstreams = set(setup.parse_workstreams())
        assert set(setup.parse_workstream_glyphs()) == workstreams
        assert set(setup.parse_workstream_names()) == workstreams

    def test_an_area_with_no_glyph_is_a_warning_rather_than_a_bare_post(self, monkeypatch):
        """A post with no title emoji is not identifiable from its preview, which
        is the only thing the table exists to provide."""
        monkeypatch.setattr(setup, "parse_workstream_glyphs", dict)
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        result = evening.build(SINCE, NOW)
        assert result["posts"] == []
        assert any("no area glyph" in warning for warning in result["payload"]["warnings"])

    def test_the_disclosure_glyph_is_not_an_area_glyph(self):
        """🔐 is the security disclosure lane — an ALERT answerable with ✅ at the
        top level. A routine TELL that looked like one in a preview is the one
        confusion here that costs something."""
        assert "🔐" not in setup.parse_workstream_glyphs().values()
        assert setup.parse_workstream_glyphs()["security"] == "🦺"


class TestTheCallerIsNeverItsOwnNoShow:
    """🩺 fired falsely on every single evening before this class existed.

    ``shipped-standup`` is on the 18:00 agenda and checks in *after* posting, so
    its name can never be in the list it is handed — and the whole section is the
    fault the fleet cannot otherwise report on itself. A 🔴 that is wrong every
    evening is a 🔴 nobody reads, which costs more than the section is worth.

    Every other case in ``TestFleetHealth`` stubs ``setup.agenda`` with a
    synthetic entry, which is why none of them saw it. These run against the real
    fleet schedule on purpose.
    """

    def test_the_real_schedule_never_names_the_routine_that_posts_it(self):
        zone, _ = setup.display_zone()
        missing = evening.no_shows(NOW, zone, ["digest", "day-ahead"])
        assert evening.SELF_ROUTINE not in {item["name"] for item in missing}

    def test_the_real_schedule_still_names_something_that_did_not_run(self):
        """The exclusion is by name and must not have muted the section."""
        zone, _ = setup.display_zone()
        every = {entry["name"] for entry in setup.agenda(NOW.date()).get("today", [])}
        assert every - {evening.SELF_ROUTINE}, "no other routine on the agenda to assert against"
        missing = {item["name"] for item in evening.no_shows(NOW, zone, [])}
        assert missing, "excluding the caller silently emptied the whole section"

    def test_a_routine_named_in_the_agenda_is_still_excluded_when_it_checked_in(self):
        zone, _ = setup.display_zone()
        named = {item["name"] for item in evening.no_shows(NOW, zone, [])}
        one = sorted(named)[0]
        assert one not in {item["name"] for item in evening.no_shows(NOW, zone, [one])}


class TestTheNoShowWindowIsUtcOnBothSides:
    """The floor is UTC, so the ceiling has to be.

    A UTC due-time was once compared against a *local* ceiling. That breaks two
    ways at once, and both are silent: a ``DISPLAY_TZ`` past about +6 wraps
    ``now`` past midnight so the ceiling sorts below everything and 🩺 never
    fires, and ``cowork_setup._local()`` appends ``" (+1d)"`` to a time landing
    on another date, which string-sorts below every bare ``HH:MM``.
    """

    def test_the_window_is_half_open_above_the_schedule_post(self):
        assert evening._between("05:46", "05:45", "18:00")
        assert not evening._between("05:45", "05:45", "18:00")

    def test_a_run_still_ahead_of_now_is_excluded(self):
        assert not evening._between("22:00", "05:45", "18:00")

    def test_an_unparseable_time_is_excluded_rather_than_reported(self):
        assert not evening._between("", "05:45", "18:00")

    def test_a_far_east_display_zone_does_not_mute_the_section(self, monkeypatch):
        """+9 renders 18:00 UTC as 03:00 local. Comparing against that ceiling
        excluded every routine on the agenda and 🩺 silently never fired."""
        import zoneinfo

        monkeypatch.setattr(setup, "display_zone", lambda: (zoneinfo.ZoneInfo("Asia/Tokyo"), ""))
        monkeypatch.setattr(
            setup,
            "agenda",
            lambda day: {"today": [{"name": "security-sweep", "times_utc": ["06:00"], "times_local": ["15:00"]}]},
        )
        health = evening.build(SINCE, NOW, checked_in=[])["health"]
        assert health is not None
        assert "security-sweep" in health["lines"][-1]

    def test_a_local_time_that_wrapped_the_date_is_not_read_as_early(self, monkeypatch):
        """`_local()` writes `00:30 (+1d)`, which sorts below every bare HH:MM."""
        monkeypatch.setattr(
            setup,
            "agenda",
            lambda day: {"today": [{"name": "late-thing", "times_utc": ["22:30"], "times_local": ["00:30 (+1d)"]}]},
        )
        assert evening.build(SINCE, NOW, checked_in=[])["health"] is None


class TestChecks:
    """Red CI is one of the three ways to be stuck, and it came back.

    The renderer read only `/pulls/{n}/reviews` at first, so a PR red since the
    hour it opened reported as *building* while the README went on promising the
    trace behind each merge.
    """

    def _state(self, monkeypatch, payload):
        # The autouse fixture stubs `ci_state` itself; these tests want the real one.
        monkeypatch.undo()
        monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")
        monkeypatch.setattr(evening, "_get", lambda path: payload)
        return evening.ci_state("abc123")

    def test_a_failure_is_red(self, monkeypatch):
        assert self._state(monkeypatch, {"state": "failure", "statuses": [{"context": "ci"}]}) == "ci red"

    def test_an_error_is_red(self, monkeypatch):
        assert self._state(monkeypatch, {"state": "error", "statuses": [{"context": "ci"}]}) == "ci red"

    def test_a_success_is_green(self, monkeypatch):
        assert self._state(monkeypatch, {"state": "success", "statuses": [{"context": "ci"}]}) == "ci green"

    def test_a_commit_with_no_statuses_claims_nothing(self, monkeypatch):
        """The combined API answers `pending` both for a run in flight and for a
        commit nothing ever reported on, and this repo's Actions report as check
        runs. Reading that as a fact would mark every open PR stuck."""
        assert self._state(monkeypatch, {"state": "pending", "statuses": []}) == ""

    def test_a_pending_run_claims_nothing(self, monkeypatch):
        assert self._state(monkeypatch, {"state": "pending", "statuses": [{"context": "ci"}]}) == ""

    def test_an_unreadable_response_claims_nothing(self, monkeypatch):
        assert self._state(monkeypatch, None) == ""

    def test_no_sha_is_no_call(self, monkeypatch):
        monkeypatch.undo()
        called = []
        monkeypatch.setattr(evening, "_get", lambda path: called.append(path))
        assert evening.ci_state("") == ""
        assert called == []

    def test_a_red_pr_is_stuck_rather_than_building(self, monkeypatch):
        monkeypatch.setattr(evening, "ci_state", lambda sha: "ci red")
        _serve(monkeypatch, opened=[pr(9, created_at="2026-08-16T09:00:00Z")])
        post = evening.build(SINCE, NOW)["posts"][0]
        assert any("🚧 **Stuck**" in line for line in post["lines"])
        assert not any("🔨 **Building**" in line for line in post["lines"])

    def test_the_check_result_reaches_the_merged_clause(self, monkeypatch):
        monkeypatch.setattr(evening, "ci_state", lambda sha: "ci green")
        _serve(monkeypatch, closed=[pr(1, labels=["cowork", "workstream:analysis"], merged_at="2026-08-16T14:00:00Z")])
        post = evening.build(SINCE, NOW)["posts"][0]
        assert any("ci green" in line for line in post["lines"])


class TestAChangesRequestedCrossingFires:
    """A block on day 2 is stuck on day 2.

    Dating that crossing by *age* left it unannounced until day 7 — and for ever
    if it merged first, which is the case the area most needed to hear about.
    """

    def _blocked(self, monkeypatch, when):
        monkeypatch.setattr(evening, "review_verdict", lambda number: ("changes requested", when))

    def test_a_block_inside_the_window_fires_a_post(self, monkeypatch):
        self._blocked(monkeypatch, datetime(2026, 8, 16, 11, 0, tzinfo=UTC))
        _serve(monkeypatch, opened=[pr(9, created_at="2026-08-01T09:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"]

    def test_a_block_before_the_window_does_not_re_announce(self, monkeypatch):
        """The standing state must not re-fire nightly."""
        self._blocked(monkeypatch, datetime(2026, 8, 10, 11, 0, tzinfo=UTC))
        _serve(monkeypatch, opened=[pr(9, created_at="2026-08-12T09:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"] == []

    def test_the_verdict_carries_the_moment_of_the_surviving_block(self, monkeypatch):
        monkeypatch.undo()
        monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")
        monkeypatch.setattr(
            evening,
            "_get",
            lambda path: [
                {"state": "CHANGES_REQUESTED", "user": {"login": "a"}, "submitted_at": "2026-08-10T09:00:00Z"},
                {"state": "CHANGES_REQUESTED", "user": {"login": "b"}, "submitted_at": "2026-08-14T09:00:00Z"},
            ],
        )
        verdict, when = evening.review_verdict(1)
        assert verdict == "changes requested"
        assert when == datetime(2026, 8, 14, 9, 0, tzinfo=UTC)

    def test_an_approval_carries_no_moment(self, monkeypatch):
        monkeypatch.undo()
        monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")
        monkeypatch.setattr(
            evening,
            "_get",
            lambda path: [{"state": "APPROVED", "user": {"login": "a"}, "submitted_at": "2026-08-14T09:00:00Z"}],
        )
        assert evening.review_verdict(1) == ("review clean", None)


class TestTheReleaseChannelReportsItsOwnBlindness:
    """ "" renders as *no new pre-release*, which is a claim about the world."""

    def test_an_unreadable_channel_is_a_warning(self, monkeypatch):
        monkeypatch.setattr(evening, "_installable", lambda: ("", "could not read the release channel"))
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        warnings = evening.build(SINCE, NOW)["payload"]["warnings"]
        assert any("could not read the release channel" in warning for warning in warnings)

    def test_an_unreadable_channel_makes_no_claim_in_the_title(self, monkeypatch):
        """Three states, not two. "" means *nothing was published* only when the
        channel answered; rendering an unanswered read as that sentence is a
        claim about the world assembled out of a failure — and the warning
        telling the routine so does not un-post the message."""
        monkeypatch.setattr(evening, "_installable", lambda: ("", "could not read the release channel"))
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        lines = evening.build(SINCE, NOW)["posts"][0]["lines"]
        assert lines[0].endswith("· 1 merged")
        assert "no new pre-release" not in lines[0]
        assert not any(line.startswith("`pip install") for line in lines)

    def test_a_channel_that_answered_nothing_still_says_so(self, monkeypatch):
        """The distinction only works if the ordinary empty case is unchanged."""
        monkeypatch.setattr(evening, "_installable", lambda: ("", ""))
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert evening.build(SINCE, NOW)["posts"][0]["lines"][0].endswith("→ no new pre-release")

    def test_a_failed_import_does_not_kill_the_run(self, monkeypatch):
        def explode():
            raise RuntimeError("no git here")

        monkeypatch.undo()
        monkeypatch.setattr(evening, "_get", lambda path: None)
        import release_channel

        monkeypatch.setattr(release_channel, "pending", explode)
        value, note = evening._installable()
        assert value == ""
        assert "could not read the release channel" in note

    def test_an_unexpected_shape_is_a_warning_rather_than_a_claim(self, monkeypatch):
        monkeypatch.undo()
        import release_channel

        monkeypatch.setattr(release_channel, "pending", lambda: "not a dict")
        value, note = evening._installable()
        assert value == ""
        assert note


class TestTheInstallFooter:
    def test_a_post_with_no_merge_carries_no_install_line(self, monkeypatch):
        """A post that fired because a PR opened contributed nothing to that
        build, and an install line under it advertises a version it is not in."""
        _serve(monkeypatch, opened=[pr(9, created_at="2026-08-16T09:00:00Z")])
        lines = evening.build(SINCE, NOW)["posts"][0]["lines"]
        assert not any(line.startswith("`pip install") for line in lines)

    def test_a_post_with_a_merge_still_carries_one(self, monkeypatch):
        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        lines = evening.build(SINCE, NOW)["posts"][0]["lines"]
        assert any(line.startswith("`pip install --pre yeaboi==3.9.0rc14`") for line in lines)


class TestTheEntryPoint:
    """`main()` is what the routine actually invokes, and had no test at all."""

    def test_it_prints_the_three_top_level_keys(self, monkeypatch, capsys):
        import json

        _serve(monkeypatch)
        assert evening.main([]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"payload", "posts", "health"}

    def test_a_naive_since_does_not_kill_the_run(self, monkeypatch, capsys):
        """A human types `--since` and the `Z` is easy to leave off. That parsed
        cleanly and then raised TypeError on the first comparison."""
        import json

        _serve(monkeypatch, closed=[pr(1, merged_at="2026-08-16T14:00:00Z")])
        assert evening.main(["--since", "2026-08-16T00:00:00"]) == 0
        assert json.loads(capsys.readouterr().out)["posts"]

    def test_a_naive_stamp_is_read_as_utc(self):
        assert evening._moment("2026-08-16T09:00:00") == datetime(2026, 8, 16, 9, 0, tzinfo=UTC)

    def test_no_checked_in_flag_means_no_health_message(self, monkeypatch, capsys):
        import json

        _serve(monkeypatch)
        evening.main([])
        assert json.loads(capsys.readouterr().out)["health"] is None


class TestThePagedWalk:
    """A short page ends the walk, and only GitHub decides what short means."""

    def _pages(self, monkeypatch, pages):
        monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")

        def get(path):
            number = int(path.split("&page=")[1]) if "&page=" in path else 1
            return pages[number - 1] if number <= len(pages) else []

        monkeypatch.setattr(evening, "_get", get)

    def test_one_malformed_row_does_not_end_a_full_page_early(self, monkeypatch):
        """`len(items)` is measured after filtering non-dicts, so a single junk
        entry on a full page read as the last page and the walk stopped."""
        full = [pr(n, updated_at="2026-08-16T17:00:00Z") for n in range(99)] + ["junk"]
        self._pages(monkeypatch, [full, [pr(500, updated_at="2026-08-16T17:00:00Z")]])
        collected = evening.fetch_closed_since(SINCE)
        assert collected is not None
        assert 500 in [item["number"] for item in collected]

    def test_the_walk_stops_at_the_window_edge(self, monkeypatch):
        full = [pr(n, updated_at="2026-08-01T17:00:00Z") for n in range(100)]
        self._pages(monkeypatch, [full, [pr(500)]])
        collected = evening.fetch_closed_since(SINCE)
        assert 500 not in [item["number"] for item in collected]

    def test_running_out_of_pages_is_blindness_rather_than_a_short_answer(self, monkeypatch):
        full = [pr(n, updated_at="2026-08-16T17:00:00Z") for n in range(100)]
        self._pages(monkeypatch, [full] * (evening.MAX_PAGES + 1))
        assert evening.fetch_closed_since(SINCE) is None


class TestTheTransportSeams:
    """`_get` is the only place the two transports are chosen, and had no test.

    A routine session has a GitHub *token* and no `gh` CLI, and its egress proxy
    refuses GraphQL — so `gh pr list --json` cannot work there and both branches
    have to ask the same REST question. A branch that only ever runs unattended
    is exactly the one nothing notices breaking.
    """

    def _no_stub(self, monkeypatch):
        monkeypatch.undo()

    def test_the_gh_branch_parses_its_stdout(self, monkeypatch):
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "gh_available", lambda: True)
        monkeypatch.setattr(
            evening.transport,
            "gh",
            lambda *args: type("R", (), {"returncode": 0, "stdout": '[{"number": 7}]'})(),
        )
        assert evening._get("/repos/o/r/pulls") == [{"number": 7}]

    def test_a_failed_gh_call_is_blindness(self, monkeypatch):
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "gh_available", lambda: True)
        monkeypatch.setattr(
            evening.transport,
            "gh",
            lambda *args: type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        assert evening._get("/repos/o/r/pulls") is None

    def test_an_html_error_page_is_blindness_rather_than_a_crash(self, monkeypatch):
        """An egress proxy answers with HTML, not JSON."""
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "gh_available", lambda: True)
        monkeypatch.setattr(
            evening.transport,
            "gh",
            lambda *args: type("R", (), {"returncode": 0, "stdout": "<html>403</html>"})(),
        )
        assert evening._get("/repos/o/r/pulls") is None

    def test_the_rest_branch_is_used_when_there_is_no_cli(self, monkeypatch):
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "gh_available", lambda: False)
        monkeypatch.setattr(
            evening.transport,
            "api",
            lambda verb, path: type("R", (), {"ok": True, "data": [{"number": 9}]})(),
        )
        assert evening._get("/repos/o/r/pulls") == [{"number": 9}]

    def test_a_failed_rest_call_is_blindness(self, monkeypatch):
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "gh_available", lambda: False)
        monkeypatch.setattr(
            evening.transport,
            "api",
            lambda verb, path: type("R", (), {"ok": False, "data": None})(),
        )
        assert evening._get("/repos/o/r/pulls") is None

    def test_an_unresolvable_checkout_is_no_path_rather_than_a_wrong_one(self, monkeypatch):
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "resolve_slug", lambda root: "")
        assert evening._repo_path("/pulls") is None

    def test_the_slug_is_segment_escaped(self, monkeypatch):
        self._no_stub(monkeypatch)
        monkeypatch.setattr(evening.transport, "resolve_slug", lambda root: "o/r")
        assert evening._repo_path("/pulls") == "/repos/o/r/pulls"
