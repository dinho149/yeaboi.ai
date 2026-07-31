"""Tests for the canonical beta wording (src/yeaboi/beta.py)."""

import ast
from pathlib import Path

from yeaboi import beta


class TestConstants:
    def test_all_constants_are_populated(self):
        assert beta.BETA_LABEL == "BETA"
        assert beta.BETA_TAG == "(beta)"
        assert beta.PERFORMANCE_BETA_PHRASE
        assert beta.PERFORMANCE_BETA_NOTICE

    def test_notice_contains_the_greppable_phrase(self):
        # The cross-surface sync test (test_beta_surfaces.py) greps docs and
        # Markdown for the short phrase; if the notice ever stops containing it,
        # the two checks would drift apart silently.
        assert beta.PERFORMANCE_BETA_PHRASE in beta.PERFORMANCE_BETA_NOTICE

    def test_notice_names_the_mode_and_gives_an_instruction(self):
        assert "Performance" in beta.PERFORMANCE_BETA_NOTICE
        assert "draft" in beta.PERFORMANCE_BETA_NOTICE

    def test_beta_colour_differs_from_the_new_badge_gold(self):
        # A caution and a freshness cue render side by side in the tips gallery;
        # sharing a colour would make them indistinguishable.
        assert beta.BETA_RGB != (226, 186, 96)
        assert all(0 <= channel <= 255 for channel in beta.BETA_RGB)


class TestModuleStaysImportFree:
    def test_beta_module_has_no_imports(self):
        """The whole design depends on this module being free to import.

        ``mcp/tools_performance.py`` imports it at module scope. If someone adds
        ``from yeaboi.performance import ...`` here, every MCP server boot starts
        paying for langchain — a regression with no visible symptom other than
        latency, which is exactly the kind that survives review.
        """
        source = Path(beta.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
        assert imports == [], f"src/yeaboi/beta.py must import nothing, found: {ast.dump(imports[0])}"
