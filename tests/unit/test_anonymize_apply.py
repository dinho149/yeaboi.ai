"""Tests for the in-place anonymize masker (`anonymize.apply`).

The engine produces the `(original -> placeholder)` map; this module applies it to a
mode's native data so the result screen re-renders masked instead of showing a separate
raw-text view. Covers the text masker's boundary/case/ordering rules, the line masker,
and the artifact round-trip for every result artifact type.
"""

from __future__ import annotations

from yeaboi.agent.state import (
    MemberUpdate,
    RetroCard,
    RetroReport,
    RoadmapAnalysis,
    RoadmapProject,
    StandupReport,
)
from yeaboi.anonymize.apply import apply_replacements, mask_artifact, mask_lines

REPS = (("Omar", "[PERSON_1]"), ("Acme", "[COMPANY]"))


class TestApplyReplacements:
    def test_basic_substitution(self):
        assert apply_replacements("Omar owns Acme", REPS) == "[PERSON_1] owns [COMPANY]"

    def test_case_insensitive(self):
        assert apply_replacements("omar and OMAR", REPS) == "[PERSON_1] and [PERSON_1]"

    def test_word_boundary_protects_substrings(self):
        # "Acme" inside a longer word is NOT masked; the issue-key form IS (hyphen boundary).
        assert apply_replacements("Acmeication", REPS) == "Acmeication"
        assert apply_replacements("Acme-123", REPS) == "[COMPANY]-123"

    def test_longest_first(self):
        reps = (("Acme", "[CO]"), ("Acme Payments", "[PRODUCT]"))
        # The multi-word original must win over its substring.
        assert apply_replacements("Acme Payments ships", reps) == "[PRODUCT] ships"

    def test_noop_when_term_absent(self):
        assert apply_replacements("nothing to see", REPS) == "nothing to see"

    def test_empty_inputs_safe(self):
        assert apply_replacements("", REPS) == ""
        assert apply_replacements("Omar", ()) == "Omar"

    def test_ignores_empty_original(self):
        assert apply_replacements("Omar", (("", "[X]"),)) == "Omar"


class TestMaskLines:
    def test_masks_each_line(self):
        lines = ["- Omar: 5 pts", "- team: Acme", "totals"]
        assert mask_lines(lines, REPS) == ["- [PERSON_1]: 5 pts", "- team: [COMPANY]", "totals"]

    def test_empty_replacements_returns_copy(self):
        lines = ["a", "b"]
        out = mask_lines(lines, ())
        assert out == lines and out is not lines


class TestMaskArtifact:
    def test_standup_report_masks_names_keeps_numbers(self):
        report = StandupReport(
            date="2026-07-21",
            team_summary="Omar unblocked Acme",
            member_updates=(MemberUpdate(name="Omar", summary="shipped it", activity_count=7),),
            confidence_pct=88,
        )
        masked = mask_artifact(report, REPS)
        assert isinstance(masked, StandupReport)
        assert masked.member_updates[0].name == "[PERSON_1]"
        assert masked.team_summary == "[PERSON_1] unblocked [COMPANY]"
        # numbers survive the round-trip untouched
        assert masked.member_updates[0].activity_count == 7
        assert masked.confidence_pct == 88

    def test_retro_report_masks_author_and_text(self):
        report = RetroReport(
            project_name="Acme",
            cards=(RetroCard(grid="went_well", text="Omar helped", author="Omar"),),
            participants=("Omar",),
        )
        masked = mask_artifact(report, REPS)
        assert isinstance(masked, RetroReport)
        assert masked.cards[0].author == "[PERSON_1]"
        assert masked.cards[0].text == "[PERSON_1] helped"
        assert masked.participants == ("[PERSON_1]",)
        assert masked.project_name == "[COMPANY]"

    def test_roadmap_analysis_masks_nested_project_fields(self):
        analysis = RoadmapAnalysis(
            summary="Acme roadmap",
            projects=(RoadmapProject(name="Acme Portal", description="for Omar", priority=1),),
        )
        masked = mask_artifact(analysis, REPS)
        assert isinstance(masked, RoadmapAnalysis)
        assert masked.projects[0].name == "[COMPANY] Portal"
        assert masked.projects[0].description == "for [PERSON_1]"
        assert masked.projects[0].priority == 1  # int preserved

    def test_team_profile_round_trips(self):
        from yeaboi.team_profile import TeamProfile

        profile = TeamProfile(team_id="Omar-team", source="jira", project_key="Acme", velocity_avg=15.9)
        masked = mask_artifact(profile, REPS)
        assert isinstance(masked, TeamProfile)
        assert masked.team_id == "[PERSON_1]-team"
        assert masked.project_key == "[COMPANY]"
        assert masked.velocity_avg == 15.9

    def test_delivery_report_masks_nested_items(self):
        from yeaboi.agent.state import DeliveredItem, DeliveryReport

        report = DeliveryReport(
            period_label="Last sprint",
            project_name="Acme",
            headline="Omar shipped the portal",
            themes=(("Acme wins", ("Omar fixed auth",)),),
            metrics=(("Items delivered", "1"),),
            delivered_items=(DeliveredItem(key="A-1", title="Omar's auth fix", status="Done", assignee="Omar"),),
        )
        masked = mask_artifact(report, REPS)
        assert isinstance(masked, DeliveryReport)
        assert masked.headline == "[PERSON_1] shipped the portal"
        assert masked.project_name == "[COMPANY]"
        assert masked.themes[0][0] == "[COMPANY] wins"
        assert masked.delivered_items[0].title == "[PERSON_1]'s auth fix"
        assert masked.delivered_items[0].assignee == "[PERSON_1]"
        assert masked.metrics == (("Items delivered", "1"),)  # numbers-as-strings untouched

    def test_empty_replacements_returns_same_object(self):
        report = StandupReport(team_summary="Omar")
        assert mask_artifact(report, ()) is report

    def test_unknown_artifact_returned_unmasked(self):
        # A type with no registered reconstructor is passed through, never crashes.
        class Foo:
            pass

        foo = Foo()
        assert mask_artifact(foo, REPS) is foo


class TestSupportingSignalMasking:
    def test_signal_samples_are_masked(self):
        from yeaboi.agent.state import DeliveryReport, SupportingSignal
        from yeaboi.anonymize.apply import mask_artifact

        report = DeliveryReport(
            period_label="Last sprint",
            supporting_signals=(
                SupportingSignal(kind="pull_requests", source="github", count=2, samples=("Omar fixed auth (#41)",)),
            ),
        )
        masked = mask_artifact(report, REPS)
        assert masked.supporting_signals[0].samples == ("[PERSON_1] fixed auth (#41)",)
        assert masked.supporting_signals[0].count == 2  # numbers untouched
