"""Unit tests for standup/aggregate.py — the deterministic-aggregation seam.

The wire-shape guarantees matter more than the arithmetic here (the helpers the
reference implementation calls all have their own suites): the inputs and the
result must be pure JSON so the Go sidecar's output is indistinguishable from
the Python reference after ``json.loads``, and the two-pass adjudication
protocol must be idempotent.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.agent.state import MemberUpdate, PracticeSignal, StandupReport
from yeaboi.standup import aggregate, engine
from yeaboi.standup.collector import ActivityBundle
from yeaboi.standup.sprint_context import SprintContext


@pytest.fixture(autouse=True)
def _no_git_lookup(monkeypatch):
    """build_aggregate_inputs hoists the git identity lookup — keep it offline."""
    monkeypatch.setattr(engine, "_detect_git_identity", lambda repo_path: ["dev@example.com"])


def _bundle() -> ActivityBundle:
    return ActivityBundle(
        items=[
            {
                "source": "jira",
                "author": "Ada Lovelace",
                "author_email": "ada@example.com",
                "kind": "issue",
                "title": "PROJ-1 Fix login redirect",
                "summary": "Fix login redirect",
                "status": "In Progress",
                "timestamp": "2026-08-05T10:00:00+00:00",
                "key": "PROJ-1",
                "url": "https://jira.example.com/browse/PROJ-1",
            },
            {
                "source": "local_git",
                "author": "ada@example.com",
                "kind": "commit",
                "title": "fix the login redirect flow",
                "timestamp": "2026-08-05T11:00:00+00:00",
                "key": "abc1234",
                "url": "",
                "repository": "web",
                "changed_files": ["src/login.py"],
            },
        ],
        counts=[("jira", 1), ("local_git", 1)],
        errors=[],
        partial_sources=[],
        skipped=[("github", "STANDUP_GITHUB_REPO not set")],
        reference_tickets=[
            {
                "source": "jira",
                "author": "Ada Lovelace",
                "kind": "ticket_context",
                "title": "PROJ-9 Login redirect broken on Safari",
                "summary": "Login redirect broken on Safari",
                "body": "The login redirect flow drops the session on Safari; fix the redirect handling.",
                "status": "To Do",
                "key": "PROJ-9",
                "url": "https://jira.example.com/browse/PROJ-9",
            }
        ],
    )


def _previous_report() -> StandupReport:
    return StandupReport(
        member_updates=(
            MemberUpdate(
                name="Ada Lovelace",
                summary="Started on PROJ-1",
                blockers="",
                outlook="Continuing PROJ-1",
                links=(("PROJ-1", "https://jira.example.com/browse/PROJ-1"),),
                practices=(PracticeSignal(rule="untracked-work"),),
            ),
        )
    )


def _inputs(*, want_adjudication: bool = False) -> dict:
    return aggregate.build_aggregate_inputs(
        bundle=_bundle(),
        members=["Me", "Ada Lovelace"],
        my_name="Me",
        my_aliases="dinho, dev",
        repo_path="",
        tracker_identities=("Dev Person",),
        self_reported_names=["Me"],
        config={"automation_handling": "exclude", "habit_detection": "on"},
        previous_report=_previous_report(),
        transcript_corrections={"Ada Lovelace": ["also finished PROJ-4"]},
        corrections=(),
        feedback_excused={("large-change", "url:https://example.com/pr/1")},
        enabled_sources={"jira", "local_git"},
        sprint=SprintContext(
            sprint_name="Sprint 12",
            start_date="2026-08-03",
            sprint_length_weeks=2,
            capacity_points=20.0,
            completed_points=5.0,
            have_burn=True,
        ),
        history=[{"status": "success", "standup_date": "2026-08-05", "confidence_pct": 60, "extra": "dropped"}],
        today="2026-08-06",
        want_adjudication=want_adjudication,
    )


class TestWireSafety:
    def test_inputs_survive_a_json_round_trip(self):
        inputs = _inputs()
        assert json.loads(json.dumps(inputs)) == inputs

    def test_result_survives_a_json_round_trip(self):
        result = aggregate.aggregate_standup(_inputs())
        assert json.loads(json.dumps(result)) == result

    def test_history_rows_are_projected_to_the_three_wire_fields(self):
        inputs = _inputs()
        assert inputs["history"] == [{"status": "success", "standup_date": "2026-08-05", "confidence_pct": 60}]

    def test_config_subset_only(self):
        inputs = _inputs()
        assert set(inputs["config"]) <= {"automation_handling", "automation_markers", "habit_detection", "habit_rules"}


class TestReferenceImplementation:
    def test_result_carries_the_deterministic_scaffold(self):
        result = aggregate.aggregate_standup(_inputs())
        assert result["members"] == ["Me", "Ada Lovelace"]
        assert result["total_items"] == 2
        assert dict(result["counts"]) == {"jira": 1, "local_git": 1}
        # The email closure attaches the git commit to Ada's card.
        assert len(result["grouped"]["Ada Lovelace"]) == 2
        # Confidence ran on the wire inputs: day 4 of 10, 5 of 20 committed.
        assert result["progress"]["sprint_day"] == 4
        assert result["progress"]["confidence_pct"] > 0
        skeletons = {sk["name"]: sk for sk in result["member_skeletons"]}
        assert skeletons["Me"]["source"] == "self-reported"  # self-report, no activity
        assert skeletons["Ada Lovelace"]["source"] == "inferred"
        assert skeletons["Ada Lovelace"]["activity_count"] == 2

    def test_yesterday_context_carries_transcript_corrections(self):
        result = aggregate.aggregate_standup(_inputs())
        entry = result["yesterday"]["Ada Lovelace"]
        assert entry["summary"] == "Started on PROJ-1"
        assert entry["corrections"] == ["also finished PROJ-4"]

    def test_members_filter_drops_unmatched_authors(self):
        inputs = _inputs()
        inputs["bundle"]["items"].append(
            {"source": "jira", "author": "Stranger", "kind": "issue", "title": "X-1", "key": "X-1"}
        )
        result = aggregate.aggregate_standup(inputs)
        assert result["total_items"] == 2  # the stranger's item never reaches a card


class TestTwoPassAdjudication:
    def test_pass_one_returns_cases_and_keeps_verdicts(self):
        result = aggregate.aggregate_standup(_inputs(want_adjudication=True))
        cases = result["adjudication_cases"]
        # The loose commit ("fix the login redirect flow") resembles PROJ-9's
        # text, so it must be offered to the adjudicator as a case…
        assert cases, "expected the untracked commit to surface as an adjudication case"
        assert cases[0]["case_id"]
        assert any(candidate[0] == "PROJ-9" for candidate in cases[0]["candidates"])
        # …while the deterministic verdict stands until something drops it.
        rules = [s["rule"] for s in result["practices"].get("Ada Lovelace", ())]
        assert "untracked-work" in rules

    def test_pass_two_applies_drops_and_returns_no_cases(self):
        first = aggregate.aggregate_standup(_inputs(want_adjudication=True))
        dropped = [case["case_id"] for case in first["adjudication_cases"]]
        second_inputs = {**_inputs(want_adjudication=True), "dropped_case_ids": dropped}
        second = aggregate.aggregate_standup(second_inputs)
        assert second["adjudication_cases"] == []
        rules = [s["rule"] for s in second["practices"].get("Ada Lovelace", ())]
        assert "untracked-work" not in rules

    def test_pass_two_is_otherwise_identical_to_pass_one(self):
        first = aggregate.aggregate_standup(_inputs(want_adjudication=True))
        second = aggregate.aggregate_standup({**_inputs(want_adjudication=True), "dropped_case_ids": ["bogus-99"]})
        # A junk id drops nothing (habits intersects with the sent ids), and
        # everything except the cases list is byte-identical.
        first.pop("adjudication_cases")
        second.pop("adjudication_cases")
        assert first == second

    def test_no_adjudication_wanted_builds_no_cases(self):
        result = aggregate.aggregate_standup(_inputs(want_adjudication=False))
        assert result["adjudication_cases"] == []


class TestRehydration:
    def test_practices_round_trip(self):
        signal = PracticeSignal(
            rule="untracked-work",
            title="Untracked work",
            detail="2 commits reference no ticket.",
            evidence=(("abc1234", ""),),
            repeat=True,
            handles=("commit:web:sdeadbeef00000000",),
        )
        wire = aggregate._signal_to_wire(signal)
        assert json.loads(json.dumps(wire)) == wire
        assert aggregate.practices_from_wire({"Ada": [wire]}) == {"Ada": (signal,)}

    def test_cases_round_trip(self):
        from yeaboi.standup.habits import AdjudicationCase

        case = AdjudicationCase(
            case_id="work-0",
            subject="fix the login redirect flow",
            branch="fix/login",
            paths=("src/login.py",),
            candidates=(("PROJ-9", "Login redirect broken on Safari", "The login redirect flow…"),),
        )
        wire = aggregate._case_to_wire(case)
        assert aggregate.cases_from_wire([wire]) == (case,)

    def test_progress_from_wire(self):
        progress = aggregate.progress_from_wire(
            {
                "sprint_day": 4,
                "sprint_total_days": 10,
                "confidence_pct": 63,
                "confidence_label": "At risk",
                "confidence_rationale": "Day 4 of 10…",
                "confidence_delta": 3,
                "confidence_trend": "improving",
            }
        )
        assert progress.sprint_day == 4
        assert progress.confidence_trend == "improving"

    def test_previous_report_round_trip_keeps_the_consumed_fields(self):
        wire = aggregate._previous_report_to_wire(_previous_report())
        assert json.loads(json.dumps(wire)) == wire
        report = aggregate._previous_report_from_wire(wire)
        member = report.member_updates[0]
        assert member.name == "Ada Lovelace"
        assert member.links == (("PROJ-1", "https://jira.example.com/browse/PROJ-1"),)
        assert member.practices[0].rule == "untracked-work"

    def test_evidence_round_trip_keeps_children(self):
        from yeaboi.agent.state import ActivityEvidence

        row = ActivityEvidence(
            kind="pr",
            key="#91",
            title="Fix login",
            url="https://github.com/x/web/pull/91",
            repository="web",
            status="merged",
            timestamp="2026-08-05T11:00:00+00:00",
            children=(ActivityEvidence(kind="commit", key="abc1234", title="fix the login redirect flow"),),
            ticket_keys=("PROJ-1",),
        )
        wire = aggregate._evidence_to_wire(row)
        assert json.loads(json.dumps(wire)) == wire
        assert aggregate.evidence_from_wire(wire) == row
