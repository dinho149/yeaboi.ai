"""Unit tests for the cross-mode per-engineer evidence gatherer.

Two properties matter more than the rest and are pinned hardest:

* **Attribution** — one person's work, and nobody else's, reaches their artifact.
* **Coverage honesty** — a source that was never scanned must never read as an
  engineer who did nothing. Every source says which of the two it is.
"""

import pytest

from yeaboi.agent.state import (
    DeliveredItem,
    DeliveryReport,
    MemberUpdate,
    PokerReport,
    PokerTicketResult,
    PokerVote,
    PracticeSignal,
    RetroCard,
    RetroReport,
    StandupReport,
)
from yeaboi.performance import evidence
from yeaboi.poker.store import PokerStore
from yeaboi.reporting.store import ReportingStore
from yeaboi.retro.store import RetroStore
from yeaboi.sessions import SessionStore
from yeaboi.standup.store import StandupStore
from yeaboi.team_profile import TeamProfile, TeamProfileStore

ENGINEER = "Ada Lovelace"
PERIOD = {"period_start": "2026-01-01", "period_end": "2026-12-31"}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sessions.db"
    with SessionStore(path) as store:
        store.create_session("perf-demo", mode="performance")
    return path


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No tracker, no roster lookup — every test drives the saved stores only."""
    monkeypatch.setattr("yeaboi.performance.identity.roster_handles", lambda *a, **k: ())
    monkeypatch.setattr(
        "yeaboi.performance.activity.gather_engineer_activity",
        lambda engineer, **kw: __import__("yeaboi.agent.state", fromlist=["EngineerActivity"]).EngineerActivity(
            engineer=engineer
        ),
    )


def _gather(db_path, **over):
    return evidence.gather_engineer_evidence(ENGINEER, db_path=db_path, **{**PERIOD, **over})


def _coverage(ev, source):
    return next(c for c in ev.coverage if c.source == source)


def _standup(date, *, name=ENGINEER, coverage=(("code", "covered"),), **member):
    return StandupReport(
        date=date,
        session_id="perf-demo",
        category_coverage=tuple(coverage),
        member_updates=(MemberUpdate(name=name, **member),),
    )


class TestEmptyDatabase:
    def test_nothing_recorded_yields_no_evidence(self, db_path):
        ev = _gather(db_path)
        assert ev.is_empty
        assert ev.contributing_sources == ()

    def test_every_source_explains_its_own_absence(self, db_path):
        ev = _gather(db_path)
        assert {c.source for c in ev.coverage} == {
            evidence.SOURCE_TICKETS,
            evidence.SOURCE_CODE,
            evidence.SOURCE_DOCUMENTATION,
            evidence.SOURCE_STANDUP,
            evidence.SOURCE_ANALYSIS,
            evidence.SOURCE_RETRO,
            evidence.SOURCE_POKER,
            evidence.SOURCE_DELIVERY,
        }
        assert all(c.detail for c in ev.coverage), "a coverage row with no explanation is the bug this guards"

    def test_a_missing_database_is_not_an_error(self, tmp_path):
        ev = evidence.gather_engineer_evidence(ENGINEER, db_path=tmp_path / "absent.db", **PERIOD)
        assert ev.is_empty


class TestStandup:
    def test_their_own_update_blockers_code_and_practices_are_read_back(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(
                _standup(
                    "2026-08-10",
                    summary="Shipped the redirect fix.",
                    self_report="Finished the redirect bug.",
                    blockers="waiting on a staging slot",
                    code_summary="3 commits across yeaboi/web.",
                    practices=(PracticeSignal(rule="untracked-work", title="Untracked work", detail="No ticket."),),
                )
            )
        ev = _gather(db_path)
        assert any("Finished the redirect bug" in line for line in ev.standup_lines)
        assert any("staging slot" in line for line in ev.standup_lines)
        assert any("3 commits" in line for line in ev.code_lines)
        assert any("Untracked work" in line for line in ev.practice_lines)

    def test_another_members_update_is_not_attributed(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", name="Bob Jones", summary="Bob's work", code_summary="Bob's code"))
        ev = _gather(db_path)
        assert ev.standup_lines == ()
        assert ev.code_lines == ()

    def test_runs_that_never_named_them_are_an_attribution_gap_not_an_idle_period(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", name="Bob Jones", summary="Bob's work"))
        row = _coverage(_gather(db_path), evidence.SOURCE_STANDUP)
        assert row.state == evidence.PARTIAL
        assert "attribution gap" in row.detail

    def test_runs_outside_the_period_are_ignored(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2020-01-01", summary="ancient history"))
        ev = _gather(db_path)
        assert ev.standup_lines == ()

    def test_an_engineer_matches_under_the_handle_standup_recorded(self, db_path, monkeypatch):
        monkeypatch.setattr("yeaboi.performance.identity.roster_handles", lambda *a, **k: ("ada@corp.com",))
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", name="ada@corp.com", summary="Shipped it."))
        assert any("Shipped it" in line for line in _gather(db_path).standup_lines)


class TestCoverageHonesty:
    """The single most important property: unknown must never render as absent."""

    def test_a_category_no_run_scanned_is_not_configured(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", summary="x", coverage=(("code", "covered"),)))
        row = _coverage(_gather(db_path), evidence.SOURCE_DOCUMENTATION)
        assert row.state == evidence.NOT_CONFIGURED
        assert "nothing is known about it either way" in row.detail

    def test_a_scanned_category_with_no_activity_says_so_explicitly(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", summary="x", coverage=(("code", "covered"),)))
        row = _coverage(_gather(db_path), evidence.SOURCE_CODE)
        assert row.state == evidence.PARTIAL
        assert "Scanned by 1 of 1" in row.detail
        assert "No code activity was attributed" in row.detail

    def test_a_scanned_category_with_activity_is_covered(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", code_summary="2 commits", coverage=(("code", "covered"),)))
        assert _coverage(_gather(db_path), evidence.SOURCE_CODE).state == evidence.COVERED

    def test_the_coverage_block_renders_every_row(self, db_path):
        block = evidence.format_coverage_md(_gather(db_path))
        assert block.startswith("**Evidence coverage")
        assert "documentation" in block and "not_configured" in block


class TestAnalysis:
    def _profile(self, db_path, member=ENGINEER):
        with TeamProfileStore(db_path) as store:
            store.save(
                TeamProfile(team_id="jira-DEMO-1", source="jira", project_key="DEMO"),
                examples={
                    "contributor_stats": [
                        {"name": member, "stories_completed": 14, "stories_total": 16, "delivery_pts": 34}
                    ],
                    "ai_adoption": {
                        "member_practices": {
                            "members": [{"member": member, "commits": 40, "prs": 9, "tests_rate": 72.0}]
                        },
                        "member_activity": [{"member": member, "commits": 40, "prs": 9, "ai_marked": 12}],
                    },
                },
            )

    def test_delivery_practice_and_ai_rows_are_read(self, db_path):
        self._profile(db_path)
        lines = _gather(db_path).analysis_lines
        assert any("14 of 16 stories" in line for line in lines)
        assert any("tests alongside production changes 72.0%" in line for line in lines)
        assert any("AI-tool markers" in line and "lower bound" in line for line in lines)

    def test_another_members_metrics_are_not_attributed(self, db_path):
        self._profile(db_path, member="Bob Jones")
        assert _gather(db_path).analysis_lines == ()

    def _metric(self, db_path, key: str):
        return next((m for m in _gather(db_path).metrics if m.key == key), None)

    def test_the_numbers_behind_the_prose_survive_as_numbers(self, db_path):
        self._profile(db_path)
        stories = self._metric(db_path, "stories_completed")
        assert (stories.value, stories.denominator) == (14.0, 16.0)
        assert stories.source == evidence.SOURCE_ANALYSIS
        assert self._metric(db_path, "delivery_points").unit == "pts"
        assert self._metric(db_path, "tests_rate").value == 72.0

    def test_a_rate_with_no_sample_is_absent_rather_than_zero(self, db_path):
        # The fixture sets tests_rate and nothing else. An engineer whose docs
        # rate was never measured has not touched docs 0% of the time, and a 0
        # here would read as a finding about them.
        self._profile(db_path)
        assert self._metric(db_path, "tests_rate") is not None
        for absent in ("docs_rate", "ticket_rate", "desc_rate", "spill_rate", "avg_cycle_time"):
            assert self._metric(db_path, absent) is None, absent

    def test_the_prose_and_the_metrics_cannot_disagree(self, db_path):
        # Both are projections of one row match, which is the whole point of the
        # split: a number quoted to the model is the number drawn on the page.
        self._profile(db_path)
        ev = _gather(db_path)
        stories = next(m for m in ev.metrics if m.key == "stories_completed")
        assert f"{stories.value:g} of {stories.denominator:g} stories" in " ".join(ev.analysis_lines)

    def test_a_member_who_is_not_them_contributes_no_metrics(self, db_path):
        self._profile(db_path, member="Bob Jones")
        assert _gather(db_path).metrics == ()


class TestRetro:
    def _retro(self, db_path, author=ENGINEER):
        with RetroStore(db_path) as store:
            store.record_run(
                RetroReport(
                    date="2026-08-12",
                    session_id="perf-demo",
                    participants=(author,),
                    cards=(
                        RetroCard(id="1", grid="didnt_go_well", text="CI is flaky", author=author, origin="web"),
                        RetroCard(id="2", grid="went_well", text="pairing helped", author="Bob Jones", origin="web"),
                    ),
                )
            )

    def test_their_cards_are_attributed_by_grid(self, db_path):
        self._retro(db_path)
        lines = _gather(db_path).retro_lines
        assert any("What didn't go well" in line and "CI is flaky" in line for line in lines)
        assert not any("pairing helped" in line for line in lines), "another author's card must not be attributed"

    def test_participation_is_reported(self, db_path):
        self._retro(db_path)
        assert any("Took part in 1 of 1" in line for line in _gather(db_path).retro_lines)

    def test_ai_generated_cards_are_never_attributed_to_a_person(self, db_path):
        with RetroStore(db_path) as store:
            store.record_run(
                RetroReport(
                    date="2026-08-12",
                    session_id="perf-demo",
                    cards=(RetroCard(id="1", grid="action_items", text="automate it", author=ENGINEER, origin="ai"),),
                )
            )
        assert not any("automate it" in line for line in _gather(db_path).retro_lines)


class TestPoker:
    def _poker(self, db_path, voter=ENGINEER):
        with PokerStore(db_path) as store:
            store.record_run(
                PokerReport(
                    date="2026-08-13",
                    session_id="perf-demo",
                    tickets=(
                        PokerTicketResult(
                            key="PROJ-1",
                            final_points=3.0,
                            estimated=True,
                            votes=(PokerVote(voter=voter, value="3"), PokerVote(voter="Bob Jones", value="5")),
                        ),
                        PokerTicketResult(
                            key="PROJ-2",
                            final_points=3.0,
                            estimated=True,
                            votes=(PokerVote(voter=voter, value="13"),),
                        ),
                    ),
                )
            )

    def test_estimate_calibration_is_summarised(self, db_path):
        self._poker(db_path)
        lines = _gather(db_path).poker_lines
        assert any("Estimated on 2 ticket(s)" in line and "50%" in line for line in lines)

    def test_a_divergent_estimate_is_reported_with_its_direction(self, db_path):
        self._poker(db_path)
        lines = _gather(db_path).poker_lines
        assert any("PROJ-2" in ln and "team settled on 3" in ln and "high" in ln for ln in lines)

    def test_another_voters_estimates_are_not_attributed(self, db_path):
        self._poker(db_path, voter="Bob Jones")
        assert _gather(db_path).poker_lines == ()

    def test_a_non_numeric_vote_is_not_scored_as_a_miss(self, db_path):
        with PokerStore(db_path) as store:
            store.record_run(
                PokerReport(
                    date="2026-08-13",
                    session_id="perf-demo",
                    tickets=(
                        PokerTicketResult(key="P-1", final_points=3.0, votes=(PokerVote(voter=ENGINEER, value="?"),)),
                    ),
                )
            )
        assert not any("estimated ?" in line for line in _gather(db_path).poker_lines)


class TestDelivery:
    def test_only_their_shipped_items_are_credited(self, db_path):
        with ReportingStore(db_path) as store:
            store.record_run(
                DeliveryReport(
                    delivered_items=(
                        DeliveredItem(key="P-9", title="Auth fix", status="Done", assignee=ENGINEER),
                        DeliveredItem(key="P-8", title="Bob's work", status="Done", assignee="Bob Jones"),
                    )
                )
            )
        lines = _gather(db_path).delivery_lines
        assert any("Auth fix" in line for line in lines)
        assert not any("Bob's work" in line for line in lines)


class TestResilience:
    def test_one_broken_store_costs_only_its_own_lines(self, db_path, monkeypatch):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", summary="Shipped it."))

        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("corrupt table")

        monkeypatch.setattr("yeaboi.retro.store.RetroStore", _Boom)
        ev = _gather(db_path)
        assert any("Shipped it" in line for line in ev.standup_lines), "a broken retro must not cost the standup"
        assert _coverage(ev, evidence.SOURCE_RETRO).state == evidence.FAILED

    def test_the_gatherer_never_raises_on_a_hostile_store(self, db_path, monkeypatch):
        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("nope")

        for target in (
            "yeaboi.standup.store.StandupStore",
            "yeaboi.retro.store.RetroStore",
            "yeaboi.poker.store.PokerStore",
            "yeaboi.reporting.store.ReportingStore",
            "yeaboi.team_profile.TeamProfileStore",
        ):
            monkeypatch.setattr(target, _Boom)
        ev = _gather(db_path)
        assert ev.is_empty
        assert all(c.state == evidence.FAILED for c in ev.coverage if c.source != evidence.SOURCE_TICKETS)


class TestCostControl:
    def test_the_default_run_never_touches_the_network(self, db_path, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("a default gather must not collect live activity")

        monkeypatch.setattr("yeaboi.standup.collector.collect_recent_activity", _boom)
        _gather(db_path)  # deep_scan defaults to False

    def test_deep_scan_without_a_saved_scope_is_skipped_and_reported(self, db_path, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("no standup scope means nothing to scan with")

        monkeypatch.setattr("yeaboi.standup.collector.collect_recent_activity", _boom)
        ev = _gather(db_path, deep_scan=True)
        assert any("gap-fill was skipped" in c.detail for c in ev.coverage)

    def test_line_buckets_stay_within_their_caps(self, db_path):
        with StandupStore(db_path) as store:
            for day in range(1, 29):
                store.record_run(
                    _standup(f"2026-08-{day:02d}", summary=f"day {day}", code_summary=f"commits on day {day}")
                )
        ev = _gather(db_path)
        assert len(ev.standup_lines) <= evidence._MAX_STANDUP_LINES
        assert len(ev.code_lines) <= evidence._MAX_CODE_LINES


class TestCeremonyEvidenceStaysPrivate:
    """Retro and poker are attributed by name inside the artifact.

    That is what the lead asked for, and it is also why the existing anonymize
    path has to keep working on these fields: a performance share page is
    deliberately anonymous, and widening the evidence must not widen what a
    share link exposes.
    """

    def test_masking_reaches_the_coverage_rows(self):
        from yeaboi.agent.state import SixMonthReview
        from yeaboi.anonymize.apply import mask_artifact

        review = SixMonthReview(
            engineer="Ada Lovelace",
            strengths=("Ada Lovelace raised CI flakiness in the retro",),
            evidence_sources=("retro", "poker"),
            evidence_coverage=(("retro", "covered", "Ada Lovelace took part in 5 of 6 retros."),),
        )
        masked = mask_artifact(review, [("Ada Lovelace", "Engineer A")])
        assert "Ada Lovelace" not in masked.strengths[0]
        assert "Ada Lovelace" not in masked.evidence_coverage[0][2]
        assert masked.evidence_coverage[0][0] == "retro", "the source key is structure, not a name to mask"

    def test_masked_coverage_rows_survive_as_triples(self):
        from yeaboi.agent.state import OneOnOnePrep
        from yeaboi.anonymize.apply import mask_artifact

        prep = OneOnOnePrep(
            engineer="Ada Lovelace",
            evidence_coverage=(("code", "covered", "Ada Lovelace: 3 commits."),),
        )
        masked = mask_artifact(prep, [("Ada Lovelace", "Engineer A")])
        assert len(masked.evidence_coverage) == 1
        assert len(masked.evidence_coverage[0]) == 3
