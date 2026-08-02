"""Risk-registry sync check — every @tool must be classified in tools/risk.py.

Mirrors test_tools_registry.py's AST discovery (no imports, no side effects)
and the surface-parity style of two-way set equality: a newly added ``@tool``
fails here until it gets an explicit READ/WRITE row, so a write tool can never
ship silently ungated; a removed tool rots the registry loudly.

# See docs: "Guardrails" — human-in-the-loop pattern (Tool layer)
"""

from yeaboi.tools.risk import TOOL_RISK, ToolRisk, high_risk_tool_names

from .test_tools_registry import _discover_all_tools

_EXPECTED_WRITES = {
    "gitlab_create_issue",
    "jira_create_epic",
    "jira_create_story",
    "jira_create_sprint",
    "confluence_create_page",
    "confluence_update_page",
    "notion_create_page",
    "notion_update_page",
    "azdevops_create_epic",
    "azdevops_create_story",
    "azdevops_create_iteration",
}


class TestRiskRegistryCoverage:
    def test_every_tool_is_classified(self):
        """Two-way equality: registry keys == AST-discovered @tool names."""
        discovered = _discover_all_tools()
        missing = set(discovered) - set(TOOL_RISK)
        stale = set(TOOL_RISK) - set(discovered)
        assert not missing, (
            f"@tool functions without a risk classification: {sorted(missing)} — "
            f"add each to TOOL_RISK in src/yeaboi/tools/risk.py "
            f"(source files: { {n: discovered[n] for n in sorted(missing)} })"
        )
        assert not stale, f"TOOL_RISK rows for tools that no longer exist: {sorted(stale)} — remove them from risk.py"

    def test_every_row_is_a_toolrisk(self):
        assert all(isinstance(risk, ToolRisk) for risk in TOOL_RISK.values())


class TestHighRiskDerivation:
    def test_write_tools_are_exactly_the_expected_set(self):
        """All external-system mutations are WRITE; adding one here is a conscious act."""
        assert high_risk_tool_names() == frozenset(_EXPECTED_WRITES)

    def test_reads_do_not_leak_into_the_gate(self):
        assert "read_codebase" not in high_risk_tool_names()
        assert "github_read_repo" not in high_risk_tool_names()
        assert "azdevops_read_board" not in high_risk_tool_names()
