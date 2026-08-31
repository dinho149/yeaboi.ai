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
            CheckResult("provider", "AI provider", "ok", detail="ollama configured", feature="Every mode"),
            CheckResult(
                "music",
                "Music (ffplay)",
                "missing",
                detail="ffplay not on PATH",
                hint="Install ffmpeg to enable music",
                feature="Background music",
            ),
            CheckResult("voice", "Dictation", "unsupported", detail="no wheel for this platform", feature="Dictation"),
            CheckResult("disk", "Disk space", "unknown", detail="probe failed"),
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
