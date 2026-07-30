"""Unit tests for the delivery-report prompt factory (ARC + supporting signals)."""

from yeaboi.prompts.reporting import get_delivery_report_prompt

_ITEMS = [
    {"key": "P-1", "title": "Ship SSO", "status": "Done", "assignee": "Ada"},
    {"key": "P-2", "title": "Harden pipeline", "status": "Closed", "assignee": ""},
]

_SIGNALS = [
    {"kind": "pull_requests", "source": "github", "count": 12, "samples": ("Fix auth (#41)", "Add SSO (#44)")},
    {"kind": "doc_updates", "source": "confluence", "count": 3, "samples": ("SSO runbook",)},
]


def _base(**overrides):
    kwargs = {
        "delivered_items": _ITEMS,
        "project_name": "Demo",
        "period_label": "Last sprint",
        "sprint_names": ["Sprint 9"],
    }
    kwargs.update(overrides)
    return get_delivery_report_prompt(**kwargs)


class TestDeliveryReportPrompt:
    def test_core_arc_shape(self):
        prompt = _base()
        assert "UNTRUSTED DATA" in prompt
        assert "P-1 Ship SSO (Done) — Ada" in prompt
        assert '"headline"' in prompt  # JSON contract present

    def test_no_signals_no_block(self):
        prompt = _base()
        # The standing requirement bullet may mention signals; the evidence block must not exist.
        assert "Supporting signals — REFERENCE ONLY" not in prompt

    def test_signals_render_inside_untrusted_context(self):
        prompt = _base(supporting_signals=_SIGNALS)
        assert "Supporting signals — REFERENCE ONLY" in prompt
        assert '- 12 pull requests merged (GitHub): "Fix auth (#41)"; "Add SSO (#44)"' in prompt
        assert "- 3 doc pages updated (Confluence)" in prompt
        # The block must live inside the untrusted-data context, after the tickets.
        assert prompt.index("UNTRUSTED DATA") < prompt.index("Supporting signals — REFERENCE ONLY")
        assert prompt.index("Completed tickets") < prompt.index("Supporting signals — REFERENCE ONLY")

    def test_reference_only_requirement_present(self):
        prompt = _base(supporting_signals=_SIGNALS)
        assert "context, NOT deliverables" in prompt
        assert "at most one corroborating clause" in prompt

    def test_ticket_evidence_unchanged_by_signals(self):
        with_signals = _base(supporting_signals=_SIGNALS)
        without = _base()
        # Signals only append to the context — everything before the block is identical.
        assert with_signals.startswith(without)
        assert "P-2 Harden pipeline (Closed)" in with_signals
