"""Render tests for the Feedback page builder (_build_feedback_screen)."""

from __future__ import annotations

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.mode_select.screens._screens_secondary import _build_feedback_screen


def _render(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=io.StringIO(), width=width, height=height + 5, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


class TestFormView:
    def test_returns_panel(self):
        assert isinstance(_build_feedback_screen("form", width=80, height=24), Panel)

    def test_respects_exact_height(self):
        out = _render(_build_feedback_screen("form", width=80, height=24), width=80, height=24)
        assert len(out.splitlines()) == 24

    def test_shows_all_field_labels_and_buttons(self):
        out = _render(_build_feedback_screen("form", width=100, height=40))
        for label in ("Type", "Area", "Title", "Description", "Submit", "AI Polish", "Back"):
            assert label in out

    def test_empty_form_shows_placeholders(self):
        out = _render(_build_feedback_screen("form", width=100, height=40))
        assert "(required" in out
        assert "Ctrl+V screenshots" in out

    def test_selected_type_and_area_shown(self):
        out = _render(_build_feedback_screen("form", kind_idx=1, area_idx=2, width=100, height=40))
        assert "Feature" in out
        assert "planning" in out

    def test_area_chip_uses_mode_color(self):
        from yeaboi.changelog import AREA_COLORS

        panel = _build_feedback_screen("form", area_idx=2, field_sel=1, width=100, height=40)
        # color_system pinned: CI terminals lack COLORTERM, and Rich would
        # otherwise downgrade rgb() styles below truecolor.
        console = Console(
            file=io.StringIO(),
            width=100,
            height=45,
            legacy_windows=False,
            force_terminal=True,
            color_system="truecolor",
        )
        console.print(panel)
        out = console.file.getvalue()
        r, g, b = (110, 140, 220)  # planning blue from AREA_COLORS
        assert AREA_COLORS["planning"] == f"rgb({r},{g},{b})"
        assert f"{r};{g};{b}" in out  # ANSI truecolor sequence present

    def test_filled_values_and_attachment_count(self):
        out = _render(
            _build_feedback_screen(
                "form",
                title_text="crash on resize",
                description="line one\nline two",
                attachments_count=2,
                width=100,
                height=40,
            )
        )
        assert "crash on resize" in out
        assert "line one" in out
        assert "line two" in out  # continuation lines shown, not hidden
        assert "more line" not in out  # short description fits — no overflow note
        assert "2" in out

    def test_long_description_shows_many_lines_then_note(self):
        description = "\n".join(f"step {i}" for i in range(1, 40))
        out = _render(
            _build_feedback_screen("form", title_text="t", description=description, width=100, height=40),
            width=100,
            height=40,
        )
        assert "step 1" in out
        assert "step 5" in out  # several continuation lines visible
        assert "more line" in out  # overflow note for what doesn't fit
        assert len(out.splitlines()) == 40  # buttons not pushed off the panel

    def test_small_terminal_selected_field_autoscrolls_into_view(self):
        description = "\n".join(f"step {i}" for i in range(1, 40))
        out = _render(
            _build_feedback_screen("form", title_text="t", description=description, field_sel=3, width=80, height=24),
            width=80,
            height=24,
        )
        assert "Description" in out  # selected row scrolled into the small viewport
        assert "Submit" in out  # buttons still visible
        assert len(out.splitlines()) == 24

    def test_narrow_width_no_crash(self):
        out = _render(_build_feedback_screen("form", title_text="x" * 300, width=60, height=24), width=60, height=24)
        assert len(out.splitlines()) == 24

    def test_status_line_shown(self):
        out = _render(_build_feedback_screen("form", status="Title is required", width=100, height=40))
        assert "Title is required" in out


class TestPolishPreviewView:
    def test_shows_polished_draft_and_buttons(self):
        out = _render(
            _build_feedback_screen(
                "polish_preview",
                polished=("Better title", "Clearer description"),
                width=100,
                height=40,
            )
        )
        assert "Better title" in out
        assert "Clearer description" in out
        assert "Accept" in out
        assert "Keep Original" in out

    def test_long_description_scrolls(self):
        meta: dict = {}
        panel = _build_feedback_screen(
            "polish_preview",
            polished=("T", "word " * 500),
            scroll_meta=meta,
            width=80,
            height=24,
        )
        assert len(_render(panel, width=80, height=24).splitlines()) == 24
        assert meta["max_offset"] > 0

    def test_scroll_clamps_past_end(self):
        panel = _build_feedback_screen("polish_preview", polished=("T", "D"), scroll_offset=9999, width=80, height=24)
        assert len(_render(panel, width=80, height=24).splitlines()) == 24


class TestResultView:
    def test_success_shows_url_and_done(self):
        out = _render(
            _build_feedback_screen(
                "result",
                status="Issue #42 created!",
                result_url="https://github.com/yeaboi-ai/yeaboi.ai/issues/42",
                width=100,
                height=40,
            )
        )
        assert "Issue #42 created!" in out
        assert "issues/42" in out
        assert "Done" in out
        assert "Open Browser" not in out

    def test_error_offers_open_browser(self):
        out = _render(
            _build_feedback_screen(
                "result",
                status="GitHub API submission failed",
                result_url="https://github.com/x",
                show_open_browser=True,
                width=100,
                height=40,
            )
        )
        assert "Open Browser" in out


class TestBusyView:
    def test_no_buttons_while_busy(self):
        out = _render(_build_feedback_screen("busy", status="Submitting…", width=100, height=40))
        assert "Submit" not in out.replace("Submitting…", "")
        assert "Submitting…" in out

    def test_custom_border_style_accepted(self):
        panel = _build_feedback_screen("busy", border_style="rgb(200,200,220)", width=80, height=24)
        assert isinstance(panel, Panel)


class TestButtonColorsRegistered:
    def test_new_labels_in_btn_colors(self):
        from yeaboi.ui.shared._components import _BTN_COLORS

        for label in ("Submit", "AI Polish", "Keep Original", "Open Browser"):
            assert label in _BTN_COLORS


class TestFeedbackWordmark:
    def test_baked_wordmark_present_and_uniform(self):
        from yeaboi.ui.shared._wordmarks import SHADOW_WORDMARKS

        mark = SHADOW_WORDMARKS["FEEDBACK"]
        assert len(mark) == 6
        assert len({len(row) for row in mark}) == 1  # equal-width rows
