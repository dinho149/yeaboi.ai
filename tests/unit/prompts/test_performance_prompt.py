"""Unit tests for the performance prompt factories.

The evidence rules are the load-bearing part: the gatherer goes to some trouble
to record that a source was never scanned, and that is wasted unless the prompt
forbids reading it as an absence.
"""

from yeaboi.prompts.performance import (
    get_one_on_one_prep_prompt,
    get_six_month_review_prompt,
)

_ACTIVITY = {
    "current_sprint": "Sprint 5",
    "stories": [{"key": "P-1", "title": "auth redirect", "status": "Done", "sprint": "current", "source": "jira"}],
}


def _prep(**over):
    args = {"engineer": "Ada Lovelace", "activity": _ACTIVITY, "open_action_items": [], "notes": []}
    return get_one_on_one_prep_prompt(**{**args, **over})


def _review(**over):
    args = {
        "engineer": "Ada Lovelace",
        "period_start": "2026-01-01",
        "period_end": "2026-06-30",
        "one_on_one_history": "",
        "delivery_history": "",
        "ceremony_summary": "",
        "notes": [],
        "framework_text": "",
        "custom_template": False,
    }
    return get_six_month_review_prompt(**{**args, **over})


class TestEvidenceRules:
    """Both prompts must refuse to infer absence from an unscanned source."""

    def test_prep_forbids_inferring_absence(self):
        out = _prep()
        assert "unknown, not absent" in out
        assert "NEVER infer" in out

    def test_review_forbids_inferring_absence(self):
        out = _review()
        assert "unknown, not absent" in out
        assert "NEVER infer" in out

    def test_both_require_naming_the_source(self):
        for out in (_prep(), _review()):
            assert "name the source it rests on" in out


class TestOneOnOnePrepPrompt:
    def test_the_ticket_evidence_carries_its_source(self):
        assert "[jira]" in _prep()

    def test_the_wider_evidence_and_coverage_blocks_are_injected(self):
        out = _prep(
            evidence_md="**Code activity:**\n  - 3 commits",
            coverage_md="**Evidence coverage:**\n  - code: covered",
        )
        assert "3 commits" in out
        assert "code: covered" in out

    def test_omitting_them_leaves_no_empty_scaffolding(self):
        out = _prep()
        assert "Evidence coverage" not in out
        assert out.count("\n\n\n") == 0

    def test_untrusted_data_is_still_framed_as_untrusted(self):
        assert "UNTRUSTED DATA" in _prep()

    def test_the_json_contract_is_unchanged(self):
        out = _prep()
        for key in ("talking_points", "feedback", "goals", "gaps", "improvements", "activity_summary"):
            assert f'"{key}"' in out


class TestSixMonthReviewPrompt:
    def test_the_wider_evidence_and_coverage_blocks_are_injected(self):
        out = _review(
            evidence_md="**Retrospectives:**\n  - raised CI flakiness",
            coverage_md="**Evidence coverage:**\n  - retro: covered",
        )
        assert "raised CI flakiness" in out
        assert "retro: covered" in out

    def test_the_framework_still_lands_after_the_evidence(self):
        out = _review(framework_text="LEVEL 3 EXPECTATIONS", evidence_md="**Code activity:**\n  - x")
        assert out.index("Code activity") < out.index("LEVEL 3 EXPECTATIONS")

    def test_a_custom_template_changes_the_instruction_not_the_shape(self):
        custom = _review(framework_text="OUR TEMPLATE", custom_template=True)
        assert "TEMPLATE/competency framework" in custom
        for key in ("strengths", "areas_for_improvement", "achievements", "goals", "overall"):
            assert f'"{key}"' in custom

    def test_untrusted_data_is_still_framed_as_untrusted(self):
        assert "UNTRUSTED DATA" in _review()
