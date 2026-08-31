"""Render tests for the System Check page builder (_build_system_check_screen)."""

from __future__ import annotations

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.system_check import CheckResult, SystemReport
from yeaboi.ui.mode_select.screens._screens_secondary import _build_system_check_screen


def _report() -> SystemReport:
    return SystemReport(
        checks=(
            CheckResult(
                "provider", "AI provider", "ok", detail="ollama configured", feature="Every mode", category="ai"
            ),
            CheckResult("github", "GitHub", "ok", detail="configured", category="integrations"),
            CheckResult(
                "music",
                "Music (ffplay)",
                "missing",
                detail="ffplay not on PATH",
                hint="Install ffmpeg to enable music",
                feature="Background music",
                category="tools",
            ),
            CheckResult(
                "voice",
                "Dictation",
                "unsupported",
                detail="no wheel for this platform",
                feature="Dictation",
                category="packages",
            ),
            CheckResult("disk", "Disk space", "unknown", detail="probe failed", category="machine"),
        )
    )


def _render(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=io.StringIO(), width=width, height=height + 5, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


class TestBuildSystemCheckScreen:
    def test_returns_panel(self):
        assert isinstance(_build_system_check_screen(_report(), width=80, height=24), Panel)

    def test_respects_exact_height(self):
        out = _render(_build_system_check_screen(_report(), width=80, height=24), width=80, height=24)
        assert len(out.splitlines()) == 24

    def test_shows_labels_details_and_hints(self):
        out = _render(_build_system_check_screen(_report(), width=120, height=40), width=120)
        assert "AI provider" in out
        assert "ffplay not on PATH" in out
        assert "Install ffmpeg" in out

    def test_status_glyphs_distinguish_states(self):
        out = _render(_build_system_check_screen(_report(), width=120, height=40), width=120)
        assert "✓" in out  # ok
        assert "○" in out  # missing
        assert "✗" in out  # unsupported
        assert "?" in out  # unknown

    def test_rerun_hint_is_shown(self):
        out = _render(_build_system_check_screen(_report(), width=100, height=40))
        assert "r re-run" in out
        assert "esc back" in out


class TestSections:
    def test_every_populated_category_gets_a_header(self):
        out = _render(_build_system_check_screen(_report(), width=120, height=60), width=120)
        for category, _rows in _report().by_category():
            assert category["title"].upper() in out

    def test_an_empty_category_renders_no_header(self):
        report = SystemReport(checks=(CheckResult("git", "Git", "ok", category="tools"),))
        out = _render(_build_system_check_screen(report, width=120, height=40), width=120)
        assert "TOOLS ON PATH" in out
        assert "INTEGRATIONS" not in out

    def test_headers_carry_a_readiness_meter_and_count(self):
        report = SystemReport(
            checks=(
                CheckResult("git", "Git", "ok", category="tools"),
                CheckResult("music", "Music (ffplay)", "missing", category="tools"),
            )
        )
        out = _render(_build_system_check_screen(report, width=120, height=40), width=120)
        assert "1/2" in out
        assert "▰" in out and "▱" in out

    def test_hints_are_marked_as_the_actionable_line(self):
        out = _render(_build_system_check_screen(_report(), width=120, height=60), width=120)
        assert "→ Install ffmpeg" in out

    def test_glyph_map_matches_the_declared_categories_two_ways(self):
        from yeaboi.system_check import CHECK_CATEGORIES
        from yeaboi.ui.mode_select.screens._screens_secondary import _CATEGORY_GLYPHS

        assert set(_CATEGORY_GLYPHS) == {category["key"] for category in CHECK_CATEGORIES}

    def test_glyphs_are_single_width(self):
        from rich.cells import cell_len

        from yeaboi.ui.mode_select.screens._screens_secondary import _CATEGORY_GLYPHS

        # An emoji (or a variation selector) measures differently across
        # terminals and would make the section rule's fill arithmetic a lie.
        for glyph in _CATEGORY_GLYPHS.values():
            assert len(glyph) == 1 and cell_len(glyph) == 1, glyph
