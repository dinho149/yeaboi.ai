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
    monkeypatch.setattr(evening, "_installable", lambda: "3.9.0rc14")
    monkeypatch.setattr(evening, "review_verdict", lambda number: "")


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
        monkeypatch.setattr(evening, "_installable", lambda: "")
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
        monkeypatch.undo()
        monkeypatch.setattr(evening, "_repo_path", lambda suffix: f"/repos/o/r{suffix}")
        monkeypatch.setattr(evening, "_get", lambda path: reviews)
        return evening.review_verdict(1)

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
