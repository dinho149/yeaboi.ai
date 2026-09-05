"""Tests for src/yeaboi/agentwatch/security_verdict.py — what a match means."""

import pytest

from yeaboi.agentwatch import security_verdict as sv


class TestLooksLikeTestPath:
    @pytest.mark.parametrize(
        "path",
        [
            "/r/tests/unit/test_x.py",
            "/r/fixtures/keys.json",
            "/r/docs/setup.md",
            "/r/README.md",
            "/r/src/yeaboi/redaction.py",
            "/Users/x/.claude/plans/when-i-asked.md",
            "/private/tmp/x/scratchpad/leak.py",
        ],
    )
    def test_true(self, path):
        assert sv.looks_like_test_path(path)

    @pytest.mark.parametrize(
        "path", ["/r/src/app.py", "/r/.env", "", "/r/config/prod.yaml", "/r/config/redaction-keys.env"]
    )
    def test_false(self, path):
        assert not sv.looks_like_test_path(path)


class TestVerdict:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            (dict(category="risky_tool", severity="high", context="command"), sv.NEEDS_DECISION),
            (dict(category="risky_tool", severity="high", context="heredoc", target="/r/plan.md"), sv.TEST_DATA),
            (dict(category="risky_tool", severity="high", context="heredoc"), sv.NEEDS_DECISION),
            (dict(category="risky_tool", severity="high", context="inline-script"), sv.NEEDS_DECISION),
            (dict(category="risky_tool", severity="high", context="write-input", target="/r/x.sh"), sv.TEST_DATA),
            (dict(category="secret", severity="high", context="inline-script"), sv.NEEDS_DECISION),
            (dict(category="secret", severity="high", context="tool-input"), sv.NEEDS_DECISION),
            (dict(category="risky_tool", severity="high", context="tool-result"), sv.TEST_DATA),
            (dict(category="risky_tool", severity="medium", context=""), sv.UNSURE),
            (dict(category="secret", severity="high", context="command"), sv.NEEDS_DECISION),
            (dict(category="secret", severity="high", context="user-prompt"), sv.NEEDS_DECISION),
            (dict(category="secret", severity="high", context="tool-result", target="/r/tests/t.py"), sv.TEST_DATA),
            (dict(category="secret", severity="high", context="write-input", target="/r/docs/a.md"), sv.TEST_DATA),
            (dict(category="secret", severity="high", context="tool-result", target="/r/.env"), sv.NEEDS_DECISION),
            (dict(category="secret", severity="medium", context="command", generic=True), sv.UNSURE),
            (
                dict(category="secret", severity="medium", context="tool-result", target="/r/.env", generic=True),
                sv.UNSURE,
            ),
            (dict(category="secret", severity="info", context="command"), sv.INFO),
            (dict(category="settings", severity="critical", context=""), sv.NEEDS_DECISION),
            (dict(category="mcp", severity="medium", context=""), sv.NEEDS_DECISION),
            (dict(category="secret", severity="high", context="command", dismissed_reason="rotated"), sv.HANDLED),
        ],
    )
    def test_rule_table(self, kwargs, expected):
        word, reason = sv.verdict(**kwargs)
        assert word == expected
        assert reason

    def test_a_dismissal_wins_over_everything_and_carries_its_reason(self):
        assert sv.verdict(category="settings", severity="critical", context="", dismissed_reason="meant it") == (
            sv.HANDLED,
            "meant it",
        )

    def test_rows_without_context_ask_for_a_rescan(self):
        _word, reason = sv.verdict(category="secret", severity="high", context="")
        assert "re-run" in reason


class TestWorstAndLine:
    def test_worst_orders_decision_first(self):
        assert sv.worst(["info", "test-data", "needs-decision", "unsure"]) == "needs-decision"
        assert sv.worst(["handled", "info"]) == "handled"
        assert sv.worst([]) == "unsure"

    @pytest.mark.parametrize(
        ("counts", "expected"),
        [
            ({}, "Nothing needs a decision."),
            ({"needs-decision": 1}, "One thing needs a decision."),
            ({"needs-decision": 2, "test-data": 41}, "Two things need a decision. 41 look like test data."),
            (
                {"needs-decision": 0, "unsure": 1, "test-data": 3, "handled": 7, "info": 83},
                "Nothing needs a decision. 1 is worth a look, 3 look like test data, 7 are handled and "
                "83 are informational.",
            ),
            ({"needs-decision": 7}, "7 things need a decision."),
        ],
    )
    def test_verdict_line(self, counts, expected):
        assert sv.verdict_line(counts) == expected
