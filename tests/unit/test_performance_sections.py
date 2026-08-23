"""Rows for a performance artifact's detail view.

What is worth testing here is not that a heading appears but the three promises
the module makes: prose stays prose, an empty section says which kind of empty
it is, and every item is exactly as tall as it claims — the last of which is what
keeps a long artifact's tail reachable.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from yeaboi.agent.state import (
    ActivityEvidence,
    Annotation,
    EngineerActivity,
    EvidenceGroup,
    OneOnOnePrep,
    OneOnOneRecord,
    PerfMetric,
    PerformanceNote,
    SixMonthReview,
)
from yeaboi.ui.mode_select.screens._performance_sections import performance_detail_rows
from yeaboi.ui.shared._components import PERFORMANCE_THEME

WIDTH = 100


def _rows(artifact, kind, width=WIDTH):
    return performance_detail_rows(artifact, kind=kind, theme=PERFORMANCE_THEME, width=width)


def _text(rows) -> str:
    return "\n".join(getattr(r, "plain", "") for r in rows)


def _styles(rows, needle: str) -> list[str]:
    """Every style applied to the segments of the row containing ``needle``."""
    out: list[str] = []
    for row in rows:
        if needle in getattr(row, "plain", ""):
            out.extend(str(span.style) for span in getattr(row, "spans", []))
            out.append(str(getattr(row, "style", "")))
    return out


class TestHeightsAreHonest:
    """``item_heights`` is what maps a scroll offset to terminal rows."""

    @pytest.mark.parametrize("width", [60, 80, 100, 120])
    def test_every_item_renders_exactly_as_tall_as_it_claims(self, width):
        prep = OneOnOnePrep(
            engineer="Ada",
            date="2026-08-23",
            talking_points=("word " * 60,),
            activity_summary="prose " * 40,
            metrics=(PerfMetric(key="a", label="Stories completed", value=12, denominator=14, group="delivery"),),
            warnings=("a warning that is quite long " * 4,),
        )
        rows, heights = _rows(prep, "prep", width)
        console = Console(width=width - 7, file=io.StringIO(), legacy_windows=False)
        for row, claimed in zip(rows, heights, strict=True):
            assert len(console.render_lines(row, pad=False)) == claimed, row

    def test_a_long_artifact_produces_more_items_than_a_short_one(self):
        short, _ = _rows(OneOnOnePrep(engineer="Ada"), "prep")
        long, _ = _rows(OneOnOnePrep(engineer="Ada", talking_points=tuple(f"point {i}" for i in range(30))), "prep")
        assert len(long) > len(short)


class TestProseStaysProse:
    def test_the_summary_email_body_is_never_styled_as_a_heading(self):
        # The old renderer sniffed line prefixes, so an unindented email body
        # became one bold coral heading — and counted as a single row while
        # occupying many.
        record = OneOnOneRecord(
            engineer="Ada",
            email_subject="1:1 summary",
            email_summary="Thanks for the chat.\nAgreed: you own billing next.",
        )
        rows, heights = _rows(record, "completion")
        body = [r for r in rows if "Agreed: you own billing" in getattr(r, "plain", "")]
        assert body, "the email body must render"
        assert all(PERFORMANCE_THEME.accent not in s for s in _styles(rows, "Agreed: you own billing"))
        assert all(h == 1 for h in heights)

    def test_a_multiline_email_body_keeps_its_own_paragraph_breaks(self):
        record = OneOnOneRecord(engineer="Ada", email_subject="s", email_summary="first\nsecond")
        rows, _ = _rows(record, "completion")
        text = _text(rows)
        assert "first" in text and "second" in text

    def test_a_bullet_continuation_lines_up_under_its_text(self):
        prep = OneOnOnePrep(engineer="Ada", talking_points=("The SSO rollout landed early. " * 6,))
        rows, _ = _rows(prep, "prep", 80)
        bullets = [r.plain for r in rows if "SSO rollout" in getattr(r, "plain", "")]
        assert len(bullets) > 1, "the fixture must wrap"
        first_text = bullets[0].index("The SSO")
        assert bullets[1].index("SSO") >= first_text - 4  # hanging, not flush with the marker

    def test_the_transcript_is_never_rendered(self):
        # It is the input rather than the output, and the most sensitive text
        # this mode holds.
        record = OneOnOneRecord(engineer="Ada", transcript="Lead: what went wrong with the rollout?")
        assert "what went wrong" not in _text(_rows(record, "completion")[0])


class TestCoverageIsLegible:
    COVERAGE = (
        ("tickets", "covered", "7 of 7 named them."),
        ("retro", "partial", "3 runs, none named them."),
        ("poker", "failed", "The poker history could not be read."),
        ("documentation", "not_configured", "No run scanned documentation."),
    )

    def test_each_state_gets_its_own_glyph_and_its_own_word(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", evidence_coverage=self.COVERAGE), "prep")
        text = _text(rows)
        for glyph in ("●", "◐", "✕", "○"):
            assert glyph in text, glyph
        # Colour alone is not a signal every reader can receive.
        for word in ("covered", "partial", "failed", "not configured"):
            assert word in text, word

    def test_covered_and_not_configured_do_not_look_alike(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", evidence_coverage=self.COVERAGE), "prep")
        legend = next(r.plain for r in rows if "EVIDENCE" in getattr(r, "plain", ""))
        assert "● 1 covered" in legend
        assert "○ 1 not configured" in legend

    def test_only_a_gap_spends_a_row_on_its_reason(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", evidence_coverage=self.COVERAGE), "prep")
        text = _text(rows)
        assert "3 runs, none named them." in text
        assert "The poker history could not be read." in text
        assert "7 of 7 named them." not in text  # covered explains itself

    def test_an_unknown_state_still_renders_with_its_word(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", evidence_coverage=(("code", "sampled", "why"),)), "prep")
        assert "sampled" in _text(rows)

    def test_no_coverage_costs_no_rows(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada"), "prep")
        assert "EVIDENCE" not in _text(rows)


class TestEmptySectionsSayWhichKindOfEmpty:
    def test_nothing_found_and_nobody_looked_read_differently(self):
        prep = OneOnOnePrep(
            engineer="Ada",
            section_states=(
                ("gaps", "not_configured", "No saved team analysis covers this engineer."),
                ("feedback", "covered", ""),
            ),
        )
        text = _text(_rows(prep, "prep")[0])
        assert "not assessed — No saved team analysis covers this engineer." in text
        assert "none found in this period" in text

    def test_a_heading_renders_even_when_its_section_is_empty(self):
        # Silence is the bug: a section that simply vanished was indistinguishable
        # from one that was never asked for.
        text = _text(_rows(OneOnOnePrep(engineer="Ada"), "prep")[0])
        for label in ("Talking points", "Feedback to give", "Goals to align on", "Gaps observed"):
            assert label in text, label

    def test_a_populated_section_is_counted_in_its_heading(self):
        prep = OneOnOnePrep(engineer="Ada", talking_points=("a", "b", "c"))
        assert "Talking points (3)" in _text(_rows(prep, "prep")[0])

    def test_the_empty_vocabulary_is_the_coverage_vocabulary(self):
        prep = OneOnOnePrep(engineer="Ada", section_states=(("gaps", "failed", "unreadable"),))
        assert "✕ could not be read — unreadable" in _text(_rows(prep, "prep")[0])


class TestNumbers:
    METRICS = (
        PerfMetric(key="stories", label="Stories completed", value=12, denominator=14, group="delivery"),
        PerfMetric(key="pts", label="Points delivered", value=34, unit="pts", group="delivery"),
        PerfMetric(key="tests", label="Tests alongside changes", value=62, unit="%", group="practice"),
    )

    def test_a_ratio_gets_a_meter_and_a_bare_count_does_not(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", metrics=self.METRICS), "prep")
        plains = [getattr(r, "plain", "") for r in rows]
        ratio = next(t for t in plains if "Stories completed" in t and "12 of 14" in t)
        points = next(t for t in plains if "Points delivered" in t and "34pts" in t)
        assert "▰" in ratio
        assert "▰" not in points  # an unscaled meter is a lie

    def test_a_percentage_is_metered_against_a_hundred(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", metrics=self.METRICS), "prep")
        rate = next(
            getattr(r, "plain", "")
            for r in rows
            if "Tests alongside changes" in getattr(r, "plain", "") and "62%" in getattr(r, "plain", "")
        )
        assert rate.count("▰") == 7  # 62% of a 12-wide meter

    def test_a_whole_number_carries_no_trailing_decimal(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", metrics=(PerfMetric(key="a", label="X", value=34.0),)), "prep")
        assert "34" in _text(rows) and "34.0" not in _text(rows)

    def test_no_metrics_means_no_numbers_section(self):
        assert "By the numbers" not in _text(_rows(OneOnOnePrep(engineer="Ada"), "prep")[0])

    def test_metrics_are_grouped_under_their_own_headings(self):
        text = _text(_rows(OneOnOnePrep(engineer="Ada", metrics=self.METRICS), "prep")[0])
        assert "Delivery" in text and "Practice" in text


class TestEvidenceRows:
    GROUP = EvidenceGroup(
        source="tickets",
        label="Tickets worked",
        items=tuple(ActivityEvidence(kind="issue", key=f"P-{i}", title=f"Story {i}", status="Done") for i in range(20)),
    )

    def test_rows_carry_the_key_the_title_and_the_status(self):
        rows, _ = _rows(OneOnOnePrep(engineer="Ada", evidence_items=(self.GROUP,)), "prep")
        row = next(getattr(r, "plain", "") for r in rows if "P-0" in getattr(r, "plain", ""))
        assert "Story 0" in row and "Done" in row

    def test_a_long_list_is_capped_and_says_how_much_it_dropped(self):
        rows, heights = _rows(OneOnOnePrep(engineer="Ada", evidence_items=(self.GROUP,)), "prep")
        assert "… and 8 more" in _text(rows)
        # Every evidence row stays one item of one row, so it can be scrolled to.
        assert all(h == 1 for h in heights)

    def test_a_groups_own_note_wins_over_the_generic_overflow_line(self):
        group = EvidenceGroup(source="code", label="Code", note="capped at 12 of 47", items=self.GROUP.items)
        assert "capped at 12 of 47" in _text(_rows(OneOnOnePrep(engineer="Ada", evidence_items=(group,)), "prep")[0])

    def test_an_empty_group_draws_nothing(self):
        group = EvidenceGroup(source="code", label="Code", items=())
        assert "Code" not in _text(_rows(OneOnOnePrep(engineer="Ada", evidence_items=(group,)), "prep")[0])


class TestPerArtifactLayout:
    def test_a_prep_leads_with_carried_actions(self):
        # The reader opened this to run a meeting.
        prep = OneOnOnePrep(engineer="Ada", carried_action_items=("Write the runbook",), talking_points=("later",))
        text = _text(_rows(prep, "prep")[0])
        assert text.index("Carried actions") < text.index("Talking points")

    def test_a_prep_names_the_sprint_window_it_read(self):
        prep = OneOnOnePrep(
            engineer="Ada",
            date="2026-08-23",
            activity=EngineerActivity(current_sprint="Sprint 14", previous_sprint="Sprint 13"),
        )
        assert "Sprint 13 → Sprint 14" in _text(_rows(prep, "prep")[0])

    def test_a_completion_states_whether_the_email_went_out(self):
        sent = _text(_rows(OneOnOneRecord(engineer="Ada", delivery_state="sent"), "completion")[0])
        failed = _text(_rows(OneOnOneRecord(engineer="Ada", delivery_state="failed"), "completion")[0])
        assert "EMAIL SENT" in sent
        assert "NOT DELIVERED" in failed

    def test_a_completion_with_no_delivery_state_claims_nothing(self):
        assert "EMAIL" not in _text(_rows(OneOnOneRecord(engineer="Ada"), "completion")[0])

    def test_a_review_carries_its_period_and_framework_as_facts(self):
        review = SixMonthReview(
            engineer="Ada",
            period_start="2026-01-12",
            period_end="2026-07-12",
            framework_used="acme-ladder-v3",
        )
        assert "2026-01-12 → 2026-07-12" in _text(_rows(review, "review")[0])
        assert "framework acme-ladder-v3" in _text(_rows(review, "review")[0])

    def test_a_note_gets_a_title_and_a_date_and_prose_that_is_not_a_heading(self):
        note = PerformanceNote(engineer="Ada", date="2026-08-23T10:00:00", text="Wants the billing service.")
        rows, _ = _rows(note, "note")
        text = _text(rows)
        assert "Note — Ada" in text
        assert "2026-08-23" in text and "T10:00" not in text
        assert all(PERFORMANCE_THEME.accent not in s for s in _styles(rows, "Wants the billing"))

    def test_an_empty_note_says_so(self):
        assert "(empty note)" in _text(_rows(PerformanceNote(engineer="Ada", text="  "), "note")[0])


class TestAnnotations:
    def test_reader_added_notes_render(self):
        # A stored annotation that no surface draws is worse than one never
        # accepted: the person who wrote it believes they corrected the document.
        prep = OneOnOnePrep(
            engineer="Ada",
            annotations=(
                Annotation(kind="field", anchor="goals", label="Promo target", text="Senior in H2", author="Lead"),
            ),
        )
        text = _text(_rows(prep, "prep")[0])
        assert "Promo target" in text and "Senior in H2" in text and "Lead" in text


class TestDegradesRatherThanRaises:
    """This runs from a page loop: a page that cannot draw is worse than one that says so."""

    def test_a_missing_artifact_renders_the_empty_state(self):
        rows, heights = _rows(None, "prep")
        assert "Nothing to show" in _text(rows)
        assert len(rows) == len(heights)

    def test_an_unknown_kind_renders_the_empty_state(self):
        assert "Nothing to show" in _text(_rows(OneOnOnePrep(engineer="Ada"), "something-else")[0])

    @pytest.mark.parametrize("kind", ["prep", "completion", "review", "note"])
    def test_a_default_artifact_of_every_kind_renders(self, kind):
        artifact = {
            "prep": OneOnOnePrep(),
            "completion": OneOnOneRecord(),
            "review": SixMonthReview(),
            "note": PerformanceNote(),
        }[kind]
        rows, heights = _rows(artifact, kind)
        assert rows and len(rows) == len(heights)

    @pytest.mark.parametrize("width", [40, 60, 68, 112, 200])
    def test_every_width_renders(self, width):
        prep = OneOnOnePrep(engineer="Ada", metrics=TestNumbers.METRICS, evidence_items=(TestEvidenceRows.GROUP,))
        rows, heights = _rows(prep, "prep", width)
        assert rows and len(rows) == len(heights)
