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


class TestOneSourceKeepsOneCoverageRow:
    """A second pass over a source amends its row; it never adds a rival one.

    Two rows for one source repeat the word in ``contributing_sources``, hand the
    browser duplicate keys, and draw two chips with different states for the same
    thing — while the reader has no way to tell which one is current.
    """

    def _rows(self):
        return [
            evidence.SourceCoverage(evidence.SOURCE_CODE, evidence.COVERED, "8 of 9 standup runs scanned code."),
            evidence.SourceCoverage(evidence.SOURCE_POKER, evidence.COVERED, "Voted in 4 session(s)."),
        ]

    def test_a_successful_scan_folds_into_the_existing_row(self):
        out = evidence._amend_coverage(self._rows(), evidence.SOURCE_CODE, "Live scan added 3 item(s).", "ok", True)

        assert [r.source for r in out] == [evidence.SOURCE_CODE, evidence.SOURCE_POKER]
        assert out[0].state == evidence.COVERED
        assert out[0].detail == "8 of 9 standup runs scanned code. Live scan added 3 item(s)."

    def test_a_failed_scan_over_a_covered_source_is_partial_not_failed(self):
        out = evidence._amend_coverage(self._rows(), evidence.SOURCE_CODE, "The live scan failed.", "failed", True)

        assert out[0].state == evidence.PARTIAL, "saved history still covered it; only the extra pass failed"

    def test_a_failed_scan_with_nothing_else_covering_the_source_is_failed(self):
        rows = [evidence.SourceCoverage(evidence.SOURCE_CODE, evidence.NOT_CONFIGURED, "No standup history.")]

        out = evidence._amend_coverage(rows, evidence.SOURCE_CODE, "The live scan failed.", "failed", False)

        assert out[0].state == evidence.FAILED, "we looked and got nothing — 'partly scanned' would overstate it"

    def test_a_skipped_scan_leaves_the_state_alone(self):
        out = evidence._amend_coverage(self._rows(), evidence.SOURCE_CODE, "Nothing to scan with.", "skipped", True)

        assert out[0].state == evidence.COVERED
        assert "Nothing to scan with." in out[0].detail

    def test_a_source_with_no_row_yet_gets_one(self):
        out = evidence._amend_coverage([], evidence.SOURCE_CODE, "Live scan added 3 item(s).", "ok", True)

        assert len(out) == 1 and out[0].source == evidence.SOURCE_CODE


class TestPokerCountsSessionsNotTickets:
    """``5 of 20`` must mean five sessions, not five tickets in one session."""

    @staticmethod
    def _report(date: str, voters: list[str]):
        return PokerReport(
            date=date,
            tickets=tuple(
                PokerTicketResult(
                    key=f"PROJ-{i}",
                    summary="Ship it",
                    final_points=3.0,
                    votes=tuple(PokerVote(voter=v, value="3") for v in voters),
                )
                for i in range(3)
            ),
        )

    def test_three_votes_in_one_session_is_one_session_attended(self):
        reports = [self._report("2026-07-01", ["Ada"]), self._report("2026-07-08", [])]

        _lines, voted, total, attended = evidence._poker_lines(reports, frozenset({"ada"}))

        assert (voted, total, attended) == (3, 2, 1), "three tickets, two sessions, one of them attended"

    def test_attending_every_session_counts_every_session(self):
        reports = [self._report("2026-07-01", ["Ada"]), self._report("2026-07-08", ["Ada"])]

        _lines, _voted, total, attended = evidence._poker_lines(reports, frozenset({"ada"}))

        assert attended == total == 2

    def test_never_voting_attends_nothing(self):
        _lines, voted, _total, attended = evidence._poker_lines(
            [self._report("2026-07-01", ["Grace"])], frozenset({"ada"})
        )

        assert (voted, attended) == (0, 0)


class TestEmptinessCountsTheNumbers:
    def test_evidence_with_only_metrics_is_not_empty(self):
        from yeaboi.agent.state import PerfMetric

        ev = evidence.EngineerEvidence(engineer="Ada", metrics=(PerfMetric(key="spill_rate", value=18.0),))

        assert not ev.is_empty, "a gathered number is evidence even with no prose line behind it"


class TestTheGapFillNoteCountsWhatSurvived:
    """The paid scan may contribute nothing; the sentence must say so.

    Saved lines are already truncated when the live results are appended, so a
    dense standup history swallows every scanned row. Counting what was *found*
    made the one block whose job is coverage honesty claim items no reader could
    see — on the opt-in path a lead paid a live multi-source fetch for.
    """

    def test_rows_that_survived_are_the_rows_reported(self):
        assert "added 3 code and 2 documentation item(s)" in evidence._gap_note(found=5, added_code=3, added_docs=2)

    def test_a_scan_swallowed_by_the_cap_says_so_instead_of_claiming_nine(self):
        note = evidence._gap_note(found=9, added_code=0, added_docs=0)

        assert "found 9 item(s)" in note and "none of which fit" in note
        assert "added 9" not in note

    def test_a_scan_that_found_nothing_says_that(self):
        assert "found nothing" in evidence._gap_note(found=0, added_code=0, added_docs=0)


class TestASourceThatNamesSomebodyElseIsAnAttributionGap:
    """`not_configured` means nobody looked. It must not mean "looked, found others"."""

    def test_a_delivery_report_crediting_others_is_partial(self, db_path):
        with ReportingStore(db_path) as store:
            store.record_run(
                DeliveryReport(
                    delivered_items=(DeliveredItem(key="P-8", title="Bob's work", status="Done", assignee="Bob Jones"),)
                )
            )

        row = _coverage(_gather(db_path), evidence.SOURCE_DELIVERY)

        assert row.state == evidence.PARTIAL, "a report exists and names someone else — that is a gap, not absence"
        assert "attribution gap" in row.detail

    def test_no_delivery_report_at_all_is_not_configured(self, db_path):
        row = _coverage(_gather(db_path), evidence.SOURCE_DELIVERY)

        assert row.state == evidence.NOT_CONFIGURED
        assert "No delivery report" in row.detail


class TestStandupRunsAreCountedOnce:
    def test_two_matching_member_rows_in_one_run_are_one_run(self):
        report = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(name="Ada Lovelace", summary="Shipped the parser"),
                MemberUpdate(name="ada", summary="Reviewed the guard"),
            ),
        )

        stats = evidence._standup_lines([report], frozenset({"ada lovelace", "ada"}))

        assert stats["matched"] == 1, "one standup named her; 'N of M runs' must never exceed M"


class TestProgressReporting:
    """The live checklist is derived from the coverage the run itself produced.

    Deriving rather than writing a second status is the whole point: the rows a
    lead watches tick past and the coverage strip on the artifact they end up
    reading can never disagree about what a source contributed.
    """

    def _events(self, db_path, **over):
        seen: list = []
        ev = evidence.gather_engineer_evidence(ENGINEER, db_path=db_path, on_progress=seen.append, **{**PERIOD, **over})
        return ev, seen

    def test_every_event_is_a_well_formed_lifecycle_event(self, db_path):
        from yeaboi.analysis.progress import is_component_progress

        _ev, seen = self._events(db_path)
        assert seen
        assert all(is_component_progress(e) for e in seen)

    def test_every_source_opens_and_settles(self, db_path):
        _ev, seen = self._events(db_path)
        for source in evidence.EVIDENCE_SOURCES:
            if source in (evidence.SOURCE_CODE, evidence.SOURCE_DOCUMENTATION):
                continue  # sub-categories of standup — they carry no phase of their own
            statuses = [e["status"] for e in seen if e["component_id"] == source]
            assert statuses, f"{source} never reported"
            assert statuses[0] == "running", f"{source} settled without starting"
            assert statuses[-1] != "running", f"{source} never settled"

    def test_each_terminal_status_matches_that_sources_coverage(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", summary="Shipped it."))
        ev, seen = self._events(db_path)
        expected = {evidence.COVERED: "completed", evidence.PARTIAL: "partial", evidence.NOT_CONFIGURED: "no_data"}
        for source in (evidence.SOURCE_STANDUP, evidence.SOURCE_RETRO, evidence.SOURCE_POKER):
            last = [e for e in seen if e["component_id"] == source][-1]
            assert last["status"] == expected[_coverage(ev, source).state]
            assert last["detail"] == _coverage(ev, source).detail

    def test_a_broken_store_settles_as_failed_rather_than_hanging(self, db_path, monkeypatch):
        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("corrupt table")

        monkeypatch.setattr("yeaboi.retro.store.RetroStore", _Boom)
        _ev, seen = self._events(db_path)
        assert [e for e in seen if e["component_id"] == evidence.SOURCE_RETRO][-1]["status"] == "failed"

    def test_no_database_still_settles_every_source(self, tmp_path):
        # A machine that has never run a standup still settles every row.
        seen: list = []
        evidence.gather_engineer_evidence(ENGINEER, db_path=tmp_path / "absent.db", on_progress=seen.append, **PERIOD)
        settled = {e["component_id"] for e in seen if e["status"] != "running"}
        assert {
            evidence.SOURCE_TICKETS,
            evidence.SOURCE_STANDUP,
            evidence.SOURCE_ANALYSIS,
            evidence.SOURCE_RETRO,
            evidence.SOURCE_POKER,
            evidence.SOURCE_DELIVERY,
        } <= settled

    def test_no_callback_is_the_default(self, db_path):
        assert evidence.gather_engineer_evidence(ENGINEER, db_path=db_path, **PERIOD).engineer == ENGINEER


class TestContextScope:
    """The run's context toggles on the evidence gather: tokened sources obey
    their dep and settle a 'switched off' coverage row; the untokened poker and
    delivery reads go quiet only under full incognito."""

    def _seed_everything(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_standup("2026-08-10", summary="Shipped it.", code_summary="3 commits."))
        with RetroStore(db_path) as store:
            store.record_run(
                RetroReport(
                    date="2026-08-12",
                    session_id="perf-demo",
                    participants=(ENGINEER,),
                    cards=(RetroCard(id="1", grid="didnt_go_well", text="CI is flaky", author=ENGINEER, origin="web"),),
                )
            )
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
                            votes=(PokerVote(voter=ENGINEER, value="3"),),
                        ),
                    ),
                )
            )
        with ReportingStore(db_path) as store:
            store.record_run(
                DeliveryReport(
                    delivered_items=(DeliveredItem(key="P-9", title="Auth fix", status="Done", assignee=ENGINEER),)
                )
            )
        with TeamProfileStore(db_path) as store:
            store.save(
                TeamProfile(team_id="jira-DEMO-1", source="jira", project_key="DEMO"),
                examples={
                    "contributor_stats": [
                        {"name": ENGINEER, "stories_completed": 14, "stories_total": 16, "delivery_pts": 34}
                    ]
                },
            )

    def _scope(self, deps):
        from yeaboi.projects.scope import ProjectScope

        return ProjectScope("", None, None if deps is None else frozenset(deps))

    def test_no_scope_and_all_on_are_identical(self, db_path):
        self._seed_everything(db_path)
        bare = _gather(db_path)
        with_none = _gather(db_path, scope=None)
        all_on = _gather(db_path, scope=self._scope(None))
        assert bare == with_none == all_on
        assert bare.standup_lines and bare.retro_lines and bare.poker_lines and bare.delivery_lines

    def test_standup_off_silences_the_source_and_says_why(self, db_path):
        self._seed_everything(db_path)
        ev = _gather(db_path, scope=self._scope({"retro", "plan", "performance", "analysis"}))
        assert ev.standup_lines == () and ev.code_lines == ()
        for source in (evidence.SOURCE_STANDUP, evidence.SOURCE_CODE, evidence.SOURCE_DOCUMENTATION):
            row = _coverage(ev, source)
            assert row.state == evidence.NOT_CONFIGURED and "context toggles" in row.detail
        assert ev.retro_lines and ev.poker_lines  # the others untouched

    def test_analysis_off_silences_the_profile(self, db_path):
        self._seed_everything(db_path)
        ev = _gather(db_path, scope=self._scope({"retro", "standup", "plan", "performance"}))
        assert ev.analysis_lines == ()
        row = _coverage(ev, evidence.SOURCE_ANALYSIS)
        assert row.state == evidence.NOT_CONFIGURED and "context toggles" in row.detail

    def test_retro_off_silences_the_retro_read(self, db_path):
        self._seed_everything(db_path)
        ev = _gather(db_path, scope=self._scope({"standup", "plan", "performance", "analysis"}))
        assert ev.retro_lines == ()
        row = _coverage(ev, evidence.SOURCE_RETRO)
        assert row.state == evidence.NOT_CONFIGURED and "context toggles" in row.detail

    def test_incognito_silences_the_untokened_sources_too(self, db_path):
        self._seed_everything(db_path)
        ev = _gather(db_path, scope=self._scope(()))
        assert ev.poker_lines == () and ev.delivery_lines == ()
        for source in (evidence.SOURCE_POKER, evidence.SOURCE_DELIVERY):
            row = _coverage(ev, source)
            assert row.state == evidence.NOT_CONFIGURED and "context toggles" in row.detail
        assert ev.standup_lines == () and ev.retro_lines == () and ev.analysis_lines == ()

    def test_a_partial_toggle_set_keeps_poker_and_delivery(self, db_path):
        self._seed_everything(db_path)
        ev = _gather(db_path, scope=self._scope({"retro"}))
        assert ev.poker_lines and ev.delivery_lines

    def test_session_scope_narrows_standup_and_retro(self, db_path):
        from yeaboi.projects.scope import ProjectScope

        self._seed_everything(db_path)
        ev = _gather(db_path, scope=ProjectScope("proj-11112222", ("some-other-session",), None))
        assert ev.standup_lines == () and ev.retro_lines == ()
        assert ev.poker_lines  # untokened sources are not session-narrowed today

    def test_standup_off_skips_the_live_gap_fill(self, db_path, monkeypatch):
        self._seed_everything(db_path)

        def _boom(*a, **k):
            raise AssertionError("the live gap-fill must not run with the standup toggle off")

        monkeypatch.setattr(evidence, "_gap_fill", _boom)
        ev = _gather(db_path, deep_scan=True, scope=self._scope({"retro"}))
        assert ev.standup_lines == ()

    def test_the_gap_fill_still_runs_when_standup_is_on(self, db_path, monkeypatch):
        self._seed_everything(db_path)
        called = {}

        def _fake(*a, **k):
            called["yes"] = True
            return [], [], "", "skipped"

        monkeypatch.setattr(evidence, "_gap_fill", _fake)
        _gather(db_path, deep_scan=True, scope=self._scope(None))
        assert called.get("yes")
